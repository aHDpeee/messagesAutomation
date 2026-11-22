"""Controlled downloads of media attached to locally indexed messages."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

from tg_observatory.exceptions import AuthenticationRequired, InvalidRequest
from tg_observatory.services.account_service import credentials_from
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.media import MediaRepository
from tg_observatory.telegram.client import TelegramGateway


class _OversizeMedia(Exception):
    pass


class MediaService:
    def __init__(
        self,
        gateway: TelegramGateway,
        accounts: AccountRepository,
        media: MediaRepository,
        media_dir: str | Path,
        report_message: Callable[[str], None] | None = None,
        report_progress: Callable[[float], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._accounts = accounts
        self._media = media
        self._media_dir = Path(media_dir)
        self._report = report_message or (lambda _: None)
        self._progress = report_progress or (lambda _: None)

    async def download_dialog(self, params: dict[str, Any]) -> dict[str, Any]:
        api_id, api_hash = credentials_from(params)
        dialog_id = _integer(params.get("dialog_id"), "dialog_id", minimum=-9_223_372_036_854_775_807)
        if dialog_id == 0:
            raise InvalidRequest("dialog_id must be a nonzero Telegram ID.")
        limit = _integer(params.get("limit", 500), "limit", minimum=1)
        if limit > 10_000:
            raise InvalidRequest("limit must not exceed 10000 messages.")
        max_bytes = _integer(params.get("max_file_bytes", 50 * 1024 * 1024), "max_file_bytes", minimum=1)
        if max_bytes > 512 * 1024 * 1024:
            raise InvalidRequest("max_file_bytes must not exceed 512 MiB.")

        profile = await self._gateway.account_status(api_id, api_hash)
        if profile is None:
            raise AuthenticationRequired()
        account_id = self._accounts.local_id(profile.telegram_id)
        if account_id is None:
            self._accounts.save_profile(profile)
            account_id = self._accounts.local_id(profile.telegram_id)
        if account_id is None:
            raise RuntimeError("Account profile could not be saved")

        rows = self._media.indexed_media(account_id, dialog_id, limit)
        result = {"downloaded": 0, "skipped": 0, "failed": 0, "bytes": 0}
        if not rows:
            self._report("No indexed media found")
            return result
        target_dir = self._media_dir / f"dialog_{dialog_id}"
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target_dir, 0o700)
        last_progress_at = 0.0

        for index, row in enumerate(rows):
            row_id = int(row["message_row_id"])
            message_id = int(row["telegram_message_id"])
            kind = str(row["media_type"] or "other")
            if self._media.completed(row_id):
                result["skipped"] += 1
                self._progress((index + 1) / len(rows))
                continue
            destination: Path | None = None
            self._media.mark_downloading(row_id, kind)
            try:
                destination = _destination(target_dir, message_id, kind)

                def on_progress(current: int, total: int) -> None:
                    nonlocal last_progress_at
                    if current > max_bytes:
                        raise _OversizeMedia()
                    now = time.monotonic()
                    if total <= 0 or now - last_progress_at >= 0.2 or current >= total:
                        within_item = current / total if total > 0 else 0.0
                        self._progress((index + within_item) / len(rows))
                        last_progress_at = now

                info = await self._gateway.download_message_media(
                    api_id,
                    api_hash,
                    dialog_id,
                    message_id,
                    destination,
                    max_bytes=max_bytes,
                    progress_callback=on_progress,
                )
                if info is None:
                    self._media.mark_failed(row_id, kind)
                    result["skipped"] += 1
                    continue
                if info.get("skipped") == "size_limit":
                    self._media.mark_failed(row_id, kind)
                    result["skipped"] += 1
                    self._report(f"Skipped message {message_id}: larger than the configured size limit")
                    continue
                if not destination.is_file():
                    raise OSError("Telegram did not create the media file")
                os.chmod(destination, 0o600)
                size = destination.stat().st_size
                if size > max_bytes:
                    destination.unlink(missing_ok=True)
                    self._media.mark_failed(row_id, kind)
                    result["skipped"] += 1
                    continue
                digest = _sha256(destination)
                stored_name = _safe_filename(info.get("filename"), destination.name)
                self._media.complete(
                    row_id,
                    kind,
                    info.get("mime_type"),
                    stored_name,
                    size,
                    str(destination),
                    digest,
                )
                result["downloaded"] += 1
                result["bytes"] += size
            except asyncio.CancelledError:
                self._media.mark_failed(row_id, kind)
                if destination is not None:
                    destination.unlink(missing_ok=True)
                raise
            except _OversizeMedia:
                self._media.mark_failed(row_id, kind)
                if destination is not None:
                    destination.unlink(missing_ok=True)
                result["skipped"] += 1
                self._report(f"Skipped message {message_id}: larger than the configured size limit")
            except Exception as error:
                self._media.mark_failed(row_id, kind)
                if destination is not None:
                    destination.unlink(missing_ok=True)
                result["failed"] += 1
                if type(error).__name__ == "FloodWaitError":
                    raise
                self._report(f"Media for message {message_id} failed")
            finally:
                self._progress((index + 1) / len(rows))
        self._report(
            f"Downloaded {result['downloaded']} media files; skipped {result['skipped']}, failed {result['failed']}"
        )
        return result

    def delete_local(self, media_id_value: Any, account_id: int) -> dict[str, Any]:
        media_id = _integer(media_id_value, "media_id", minimum=1)
        record = self._media.get_completed_for_account(media_id, account_id)
        if record is None:
            raise InvalidRequest("Downloaded media file was not found for this account.")
        root = self._media_dir.resolve()
        source = Path(str(record["local_path"]))
        try:
            if source.is_symlink():
                raise InvalidRequest("Refusing to delete a symbolic link as media.")
            resolved = source.resolve(strict=True)
            resolved.relative_to(root)
            if not stat.S_ISREG(resolved.stat().st_mode):
                raise InvalidRequest("The selected media path is not a regular file.")
        except FileNotFoundError as error:
            self._media.delete_completed_for_account(media_id, account_id)
            raise InvalidRequest("The media file is already missing; its index record was removed.") from error
        except ValueError as error:
            raise InvalidRequest("Refusing to delete a file outside Observatory media storage.") from error
        resolved.unlink()
        if not self._media.delete_completed_for_account(media_id, account_id):
            raise RuntimeError("The file was removed, but its media record could not be updated.")
        return {"media_id": media_id, "deleted": True, "freed_bytes": int(record["size_bytes"] or 0)}


def _integer(value: Any, field: str, *, minimum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise InvalidRequest(f"{field} must be an integer.") from error
    if result < minimum:
        raise InvalidRequest(f"{field} is outside the supported range.")
    return result


def _destination(directory: Path, message_id: int, kind: str) -> Path:
    safe_kind = re.sub(r"[^a-zA-Z0-9_-]", "_", kind)[:32] or "media"
    name = f"message_{message_id}_{safe_kind}"
    candidate = directory / name
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = directory / f"{name}_{suffix}"
    return candidate


def _safe_filename(value: Any, fallback: str) -> str:
    name = Path(str(value or "")).name
    name = "".join(character for character in name if character.isprintable() and character not in "\\/")
    return name[:200] or fallback


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
