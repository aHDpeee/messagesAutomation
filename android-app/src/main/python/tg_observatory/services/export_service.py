"""Checkpointed, bounded-memory exports of locally indexed message history."""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import json
import os
import re
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from tg_observatory.exporters import markdown
from tg_observatory.exceptions import EmptyMessageRange, InvalidRequest
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.exports import ExportRepository
from tg_observatory.storage.repositories.messages import MessageRepository
from tg_observatory.storage.time import utc_now

EXPORT_FORMATS = {"md", "jsonl", "json", "csv", "zip"}
CSV_FIELDS = (
    "account_id", "dialog_id", "dialog_title", "dialog_kind",
    "message_id", "date_utc", "sender_id", "sender_username", "sender_name", "post_author", "text",
    "reply_to_message_id", "edit_date_utc", "forward_from_peer_id", "media_type", "raw",
)


class ExportService:
    def __init__(
        self,
        database: Database,
        accounts: AccountRepository,
        messages: MessageRepository,
        exports: ExportRepository,
        export_dir: str | Path,
        *,
        report_message: Callable[[str], None] | None = None,
        report_progress: Callable[[float, str | None], None] | None = None,
        save_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._database = database
        self._accounts = accounts
        self._messages = messages
        self._exports = exports
        self._export_dir = Path(export_dir)
        self._report = report_message or (lambda _: None)
        self._progress = report_progress or (lambda _value, _message: None)
        self._save_checkpoint = save_checkpoint or (lambda _: None)

    async def export_dialog_jsonl(
        self, job_id: str, params: dict[str, Any], checkpoint: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self.export_dialog(job_id, {**params, "format": "jsonl"}, checkpoint)

    async def export_dialog(
        self, job_id: str, params: dict[str, Any], checkpoint: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        output_format = str(params.get("format", "md")).lower()
        if output_format not in EXPORT_FORMATS:
            raise InvalidRequest("Export format must be md, jsonl, json, csv or zip.")
        if not job_id or Path(job_id).name != job_id or job_id in {".", ".."}:
            raise InvalidRequest("Invalid export job ID.")

        dialog_id = _telegram_id(params.get("dialog_id"))
        profile = self._accounts.get_profile()
        if profile is None:
            raise InvalidRequest("No locally indexed Telegram account is available.")
        account_id = self._accounts.local_id(profile.telegram_id)
        dialog = self._database.fetchone(
            """SELECT id, telegram_id, title, kind, username FROM dialogs
               WHERE account_id=? AND telegram_id=?""",
            (account_id, dialog_id),
        )
        if dialog is None:
            raise InvalidRequest("Sync this dialog before exporting its messages.")

        state = checkpoint or {}
        start_id = _message_boundary(params.get("start_message_id"), "start_message_id")
        end_id = _message_boundary(params.get("end_message_id"), "end_message_id")
        if start_id is not None and end_id is not None and start_id > end_id:
            raise InvalidRequest("Start message must not be later than end message.")
        period_start = _date_boundary(params.get("period_start"), "period_start")
        period_end = _date_boundary(params.get("period_end"), "period_end")
        if period_start and period_end and period_start >= period_end:
            raise InvalidRequest("Start time must be earlier than end time (end is exclusive).")
        range_sql = "dialog_id=?"
        range_args: list[Any] = [int(dialog["id"])]
        source_job = job_id if params.get("fetch_source") else None
        if source_job is not None:
            range_sql += " AND EXISTS (SELECT 1 FROM export_source_messages source WHERE source.export_job_id=? AND source.telegram_message_id=messages.telegram_message_id)"
            range_args.append(source_job)
        if start_id is not None:
            range_sql += " AND telegram_message_id>=?"
            range_args.append(start_id)
        if end_id is not None:
            range_sql += " AND telegram_message_id<=?"
            range_args.append(end_id)
        if period_start:
            range_sql += " AND timestamp_us(date_utc)>=timestamp_us(?)"
            range_args.append(period_start)
        if period_end:
            range_sql += " AND timestamp_us(date_utc)<timestamp_us(?)"
            range_args.append(period_end)
        bounds = self._database.fetchone(
            "SELECT COUNT(*) AS count, MIN(date_utc) AS start, MAX(date_utc) AS end, "
            "MAX(telegram_message_id) AS max_id "
            f"FROM messages WHERE {range_sql}", range_args,
        )
        assert bounds is not None  # Aggregate SELECT always returns one row.
        if not bounds["count"]:
            raise EmptyMessageRange()

        end_id = int(state.get("end_id") or bounds["max_id"])
        checkpoint_format = state.get("format")
        if checkpoint_format is not None and checkpoint_format != output_format:
            raise InvalidRequest("The export format cannot change while resuming a job.")
        job_dir = self._export_dir / job_id
        title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(dialog["title"] or "Conversation"))
        title = title.strip(" .").encode("utf-8")[:140].decode("utf-8", errors="ignore") or "Conversation"
        filename = str(state.get("filename") or (
            f"{title} — Chat Export — {utc_now()[:10]}.md" if output_format == "md"
            else f"messages.{output_format}"))
        if Path(filename).name != filename:
            raise InvalidRequest("Invalid checkpoint filename.")
        part_path = job_dir / ("messages.jsonl.part" if output_format == "zip" else f"{filename}.part")
        final_path = job_dir / filename
        manifest_path = job_dir / "manifest.json"
        metadata = _metadata(profile.telegram_id, dialog)
        if state.get("finalizing") and final_path.is_file():
            expected_hash = state.get("archive_sha256") if output_format == "zip" else state.get("sha256")
            if output_format == "zip":
                _verify_archive(final_path, str(state.get("payload_sha256", "")))
            if expected_hash and _hash_file(final_path) != expected_hash:
                raise InvalidRequest("The finalized export file does not match its checkpoint.")
            if output_format == "zip" and not expected_hash:
                state = {**state, "sha256": _hash_file(final_path)}
                self._save_checkpoint({**state, "archive_sha256": state["sha256"]})
            return self._complete_export(
                job_id, profile.telegram_id, dialog, final_path, manifest_path, state, output_format
            )

        if not state:
            if job_dir.exists():
                if final_path.exists():
                    raise InvalidRequest("An export file already exists for this job.")
                part_path.unlink(missing_ok=True)
            else:
                job_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            stream = part_path.open("x+b")
            os.chmod(part_path, 0o600)
            if output_format == "md":
                stream.write(markdown.header(dialog, metadata["exported_at"],
                                             int(bounds["count"]), period_start or bounds["start"],
                                             period_end or bounds["end"],
                                             end_exclusive=period_end is not None))
            elif output_format in {"jsonl", "zip"}:
                stream.write(_line({"record_type": "metadata", **metadata}))
            elif output_format == "json":
                stream.write(b'{"metadata":' + _json_bytes(metadata) + b',"messages":[')
            else:
                stream.write(_csv_bytes(None, header=True))
            stream.flush()
            os.fsync(stream.fileno())
            checkpoint_data: dict[str, Any] = {
                "relative_part": f"{job_id}/{filename}.part",
                "bytes": stream.tell(),
                "after_id": (start_id - 1) if start_id is not None else 0,
                "count": 0,
                "format": output_format,
                "filename": filename,
                "end_id": end_id,
                "expected_count": int(bounds["count"]),
            }
            if source_job is not None:
                checkpoint_data["source_scope_version"] = 1
            try:
                self._save_checkpoint(checkpoint_data)
            except Exception:
                stream.close()
                part_path.unlink(missing_ok=True)
                raise
        else:
            if not part_path.is_file():
                raise InvalidRequest("The checkpointed partial export file is missing.")
            expected_bytes = int(state.get("bytes", -1))
            if expected_bytes < 0 or part_path.stat().st_size < expected_bytes:
                raise InvalidRequest("The checkpointed partial export file is incomplete.")
            stream = part_path.open("r+b")
            stream.truncate(expected_bytes)
            stream.seek(expected_bytes)
            checkpoint_data = {
                **state,
                "filename": filename,
                "end_id": end_id,
                "relative_part": f"{job_id}/{filename}.part",
                "bytes": expected_bytes,
                "after_id": int(state.get("after_id", 0)),
                "count": int(state.get("count", 0)),
                "format": output_format,
            }

        digest = hashlib.sha256()
        stream.flush()
        stream.seek(0)
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        stream.seek(checkpoint_data["bytes"])

        total = int(bounds["count"])
        exported = int(checkpoint_data["count"])
        after_id = int(checkpoint_data["after_id"])
        try:
            while not checkpoint_data.get("finalizing"):
                page = self._messages.export_page(int(dialog["id"]), after_id=after_id, end_id=end_id,
                                                  period_start=period_start, period_end=period_end, source_job=source_job)
                if not page:
                    break
                for item in page:
                    if output_format == "md":
                        encoded = markdown.message(item, dialog)
                    elif output_format in {"jsonl", "zip"}:
                        encoded = _line({"record_type": "message", **item})
                    elif output_format == "json":
                        encoded = (b"," if exported else b"") + _json_bytes(item)
                    else:
                        encoded = _csv_bytes(item, metadata=metadata)
                    stream.write(encoded)
                    digest.update(encoded)
                    exported += 1
                stream.flush()
                os.fsync(stream.fileno())
                after_id = int(page[-1]["message_id"])
                checkpoint_data.update(
                    {"bytes": stream.tell(), "after_id": after_id, "count": exported}
                )
                self._save_checkpoint(checkpoint_data)
                progress = min(0.99, exported / total) if total else 0.99
                self._progress(progress, f"Exported {exported} messages")
                self._report(f"Exported {exported} messages")
                await asyncio.sleep(0)

            expected_count = checkpoint_data.get("expected_count")
            if expected_count is not None and exported != expected_count:
                raise InvalidRequest("The local message index changed during export. Start a new export.")
            if output_format in {"json", "md"} and not checkpoint_data.get("finalizing"):
                ending = markdown.footer() if output_format == "md" else b"]}"
                stream.write(ending)
                digest.update(ending)
                stream.flush()
                os.fsync(stream.fileno())
                checkpoint_data["bytes"] = stream.tell()
            checkpoint_data["finalizing"] = True
            payload_hash = digest.hexdigest()
            checkpoint_data["sha256"] = payload_hash
            if output_format == "zip":
                checkpoint_data["payload_sha256"] = payload_hash
                checkpoint_data["payload_bytes"] = stream.tell()
            self._save_checkpoint(checkpoint_data)
            stream.close()
            if output_format == "zip":
                archive_part = job_dir / f"{filename}.tmp"
                archive_part.unlink(missing_ok=True)
                archive_manifest = {
                    "schema": "telegram-observatory-export-v1",
                    "exported_at": metadata["exported_at"],
                    "account_id": profile.telegram_id,
                    "dialog": _dialog_data(dialog),
                    "format": "zip",
                    "records": exported,
                    "files": [{
                        "path": "messages.jsonl",
                        "size_bytes": int(checkpoint_data["payload_bytes"]),
                        "sha256": payload_hash,
                    }],
                }
                with zipfile.ZipFile(archive_part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                    archive.write(part_path, "messages.jsonl")
                    archive.writestr("manifest.json", _line(archive_manifest))
                os.chmod(archive_part, 0o600)
                with archive_part.open("rb") as archive_stream:
                    os.fsync(archive_stream.fileno())
                if final_path.exists():
                    raise FileExistsError("An export file already exists for this job.")
                # Android app sandboxes can prohibit hard links, even within
                # their private directory. Each job owns its unique directory.
                os.replace(archive_part, final_path)
                part_path.unlink()
                checkpoint_data["archive_sha256"] = _hash_file(final_path)
                checkpoint_data["sha256"] = checkpoint_data["archive_sha256"]
                checkpoint_data["bytes"] = final_path.stat().st_size
                self._save_checkpoint(checkpoint_data)
                return self._complete_export(
                    job_id, profile.telegram_id, dialog, final_path, manifest_path,
                    checkpoint_data, output_format,
                )
            if final_path.exists():
                raise FileExistsError("An export file already exists for this job.")
            os.replace(part_path, final_path)
            return self._complete_export(
                job_id, profile.telegram_id, dialog, final_path, manifest_path,
                checkpoint_data, output_format,
            )
        finally:
            if not stream.closed:
                stream.close()

    def _complete_export(
        self,
        job_id: str,
        account_id: int,
        dialog: Any,
        final_path: Path,
        manifest_path: Path,
        checkpoint: dict[str, Any],
        output_format: str,
    ) -> dict[str, Any]:
        size_bytes = final_path.stat().st_size
        sha256 = str(checkpoint.get("sha256") or _hash_file(final_path))
        manifest = {
            "schema": "telegram-observatory-export-v1",
            "exported_at": utc_now(),
            "account_id": account_id,
            "dialog": _dialog_data(dialog),
            "format": output_format,
            "records": int(checkpoint.get("count", 0)),
            "file": final_path.name,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }
        temporary_manifest = manifest_path.with_suffix(".json.tmp")
        temporary_manifest.write_bytes(_line(manifest))
        os.chmod(temporary_manifest, 0o600)
        os.replace(temporary_manifest, manifest_path)
        relative = final_path.relative_to(self._export_dir).as_posix()
        self._exports.record_file(job_id, relative, size_bytes, sha256)
        self._progress(1.0, "Export generated — partial Telegram history")
        return {
            "format": output_format,
            "dialog_id": int(dialog["telegram_id"]),
            "records": int(checkpoint.get("count", 0)),
            "completeness": "partial",
            "warnings": [markdown.WARNING],
            "size_bytes": size_bytes,
            "sha256": sha256,
            "file_path": str(final_path),
            "manifest_path": str(manifest_path),
        }


def _metadata(account_id: int, dialog: Any) -> dict[str, Any]:
    return {
        "schema": "telegram-observatory-export-v1",
        "exported_at": utc_now(),
        "account_id": account_id,
        "dialog": _dialog_data(dialog),
    }


def _dialog_data(dialog: Any) -> dict[str, Any]:
    return {
        "telegram_id": dialog["telegram_id"],
        "title": dialog["title"],
        "kind": dialog["kind"],
        "username": dialog["username"],
    }


def _telegram_id(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise InvalidRequest("dialog_id must be an integer.") from error
    if parsed == 0 or not -(2**63) <= parsed < 2**63:
        raise InvalidRequest("dialog_id must be a nonzero 64-bit integer.")
    return parsed


def _line(value: Any) -> bytes:
    return _json_bytes(value) + b"\n"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, default=_json_default, separators=(",", ":")
    ).encode("utf-8")


def _csv_bytes(
    value: dict[str, Any] | None,
    *,
    header: bool = False,
    metadata: dict[str, Any] | None = None,
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\n")
    if header:
        writer.writeheader()
    elif value is not None:
        row = dict(value)
        if metadata is not None:
            dialog = metadata["dialog"]
            row.update({
                "account_id": metadata["account_id"],
                "dialog_id": dialog["telegram_id"],
                "dialog_title": dialog["title"],
                "dialog_kind": dialog["kind"],
            })
        raw = row.get("raw", {})
        row["post_author"] = raw.get("post_author") if isinstance(raw, dict) else None
        row["raw"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        result = value.replace(tzinfo=UTC) if isinstance(value, datetime) and value.tzinfo is None else value
        return result.isoformat().replace("+00:00", "Z")
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return str(value)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_archive(path: Path, expected_payload_hash: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if names != {"messages.jsonl", "manifest.json"} or archive.testzip() is not None:
                raise InvalidRequest("The finalized ZIP archive is incomplete or invalid.")
            digest = hashlib.sha256()
            with archive.open("messages.jsonl") as payload:
                while chunk := payload.read(1024 * 1024):
                    digest.update(chunk)
            if not expected_payload_hash or digest.hexdigest() != expected_payload_hash:
                raise InvalidRequest("The archived message data does not match its checkpoint.")
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise InvalidRequest("The finalized ZIP archive is incomplete or invalid.") from error


def _message_boundary(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not str(value).isdigit():
        raise InvalidRequest(f"{field} must be a positive message ID.")
    parsed = int(value)
    if not 1 <= parsed < 2**31:
        raise InvalidRequest(f"{field} must be a positive 32-bit message ID.")
    return parsed


def _date_boundary(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timezone required")
        return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError) as error:
        raise InvalidRequest(f"{field} must be an ISO 8601 timestamp with an explicit timezone.") from error
