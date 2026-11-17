from __future__ import annotations

"""Telegram operations executed only on the scheduler-owned Telethon client.

This module deliberately never constructs TelegramClient or opens a session.
The caller must pass the existing ``TelegramConnection.client``.
"""

import asyncio
import csv
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telethon import errors, functions
from telethon.tl.types import Channel, ChannelForbidden, Chat, ChatForbidden, User
from . import attribution


def pace_iterator(iterator: Any, seconds: float = 3) -> Any:
    """Set Telethon 1.x RequestIter's delay between pagination requests."""
    if hasattr(iterator, "wait_time"):
        iterator.wait_time = seconds
    return iterator


def _peer_type(entity: Any) -> str:
    if isinstance(entity, User):
        return "bot" if entity.bot else "chat"
    if isinstance(entity, (Channel, ChannelForbidden)):
        return "group" if entity.megagroup else "channel"
    if isinstance(entity, (Chat, ChatForbidden)):
        return "group"
    return "chat"


def _spreadsheet_text(value: Any) -> str:
    """Prevent Telegram-controlled names from being interpreted as formulas."""
    text = str(value or "")
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def _dialog_record(dialog: Any, archived_ids: set[int]) -> dict[str, Any]:
    entity = dialog.entity
    peer_id = int(dialog.id)
    kind = _peer_type(entity)
    title = getattr(entity, "title", None)
    if not title:
        title = " ".join(x for x in [getattr(entity, "first_name", None), getattr(entity, "last_name", None)] if x)
    return {
        "id": str(peer_id),
        "title": title or getattr(entity, "username", None) or str(peer_id),
        "type": kind,
        "accessible": not isinstance(entity, (ChatForbidden, ChannelForbidden)),
        "username": getattr(entity, "username", None),
        # Some supplied entities include a description; ordinary Telegram
        # dialog objects often omit it, so keep the catalog's previous value.
        "description": getattr(entity, "about", None) or "",
        "unread_count": int(getattr(dialog, "unread_count", 0) or 0),
        "archived": bool(getattr(dialog, "folder_id", None) == 1),
        "last_message_date": dialog.date.astimezone(timezone.utc).isoformat() if getattr(dialog, "date", None) else "",
        "last_message_out": getattr(getattr(dialog, "message", None), "out", None),
        "history_known": hasattr(dialog, "message"),
        "processed": peer_id in archived_ids,
        "access_hash_present": getattr(entity, "access_hash", None) is not None,
    }


async def discover_dialogs(client: Any, archived_ids: set[int], *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Return one bounded page. Telethon performs Telegram's dialog pagination."""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    result: list[dict[str, Any]] = []
    # Iterate only as far as offset+limit; no unbounded get_dialogs() call.
    index = 0
    async for dialog in pace_iterator(client.iter_dialogs(limit=offset + limit)):
        if index >= offset:
            result.append(_dialog_record(dialog, archived_ids))
        index += 1
    return {"items": result, "offset": offset, "limit": limit, "next_offset": offset + len(result) if len(result) == limit else None}


PROFILE_DETAILS_LIMIT = 100
PROFILE_REQUEST_INTERVAL = 3.0

MEMBER_FIELDS = ("record_type", "user_id", "username", "first_name", "last_name", "phone", "bio",
                 "personal_channel_id", "personal_channel_title", "personal_channel_username",
                 "is_bot", "is_premium", "group_role", "note")
BATCH_MEMBER_FIELDS = MEMBER_FIELDS + ("source_group_id", "source_group_title", "block group / plan")


async def export_group_members(client: Any, chat_id: int, out_dir: Path, *, max_members: int | None = None, on_progress=None, enrich_profiles: bool = False) -> dict[str, Any]:
    """Export members visible to this account; never bypass server permissions."""
    if max_members is not None:
        max_members = max(1, int(max_members))
    try:
        entity = await client.get_entity(int(chat_id))
    except errors.ChatAdminRequiredError:
        raise PermissionError("Telegram requires administrator privileges to list this group's members.") from None
    except errors.ChannelPrivateError:
        raise PermissionError("This group is private or is not accessible to the current account.") from None
    if not isinstance(entity, (Chat, Channel)) or (isinstance(entity, Channel) and not entity.megagroup):
        raise ValueError("This source is not a group or supergroup.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = uuid.uuid4().hex
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    final = out_dir / f"members_{chat_id}_{stamp}_{run_id[:8]}.csv"
    fd, tmp_name = tempfile.mkstemp(prefix=".members-", suffix=".part", dir=out_dir)
    count = 0
    profiles_attempted = profiles_available = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(MEMBER_FIELDS))
            writer.writeheader()
            # Telethon handles paging. Server may restrict or hide the list.
            async for participant in pace_iterator(client.iter_participants(entity, limit=max_members)):
                bio, channel_id, channel_title, channel_username = "", "", "", ""
                if enrich_profiles and profiles_attempted < PROFILE_DETAILS_LIMIT:
                    if profiles_attempted:
                        await asyncio.sleep(PROFILE_REQUEST_INTERVAL)
                    profiles_attempted += 1
                    try:
                        profile = await client(functions.users.GetFullUserRequest(id=participant))
                        full = profile.full_user
                        profiles_available += 1
                        bio = getattr(full, "about", None) or ""
                        channel_id = getattr(full, "personal_channel_id", None) or ""
                        for channel in getattr(profile, "chats", ()) or ():
                            if channel_id and getattr(channel, "id", None) == channel_id:
                                channel_title = getattr(channel, "title", None) or ""
                                channel_username = getattr(channel, "username", None) or ""
                                break
                    except (errors.UserPrivacyRestrictedError, errors.UserIdInvalidError,
                            errors.PeerIdInvalidError, errors.UserNotMutualContactError, ValueError):
                        # A single inaccessible profile must not prevent the group export.
                        pass
                writer.writerow({
                    "record_type": "member",
                    "user_id": getattr(participant, "id", ""),
                    "username": _spreadsheet_text(getattr(participant, "username", None)),
                    "first_name": _spreadsheet_text(getattr(participant, "first_name", None)),
                    "last_name": _spreadsheet_text(getattr(participant, "last_name", None)),
                    "phone": _spreadsheet_text(getattr(participant, "phone", None)),
                    "bio": _spreadsheet_text(bio),
                    "personal_channel_id": channel_id,
                    "personal_channel_title": _spreadsheet_text(channel_title),
                    "personal_channel_username": _spreadsheet_text(channel_username),
                    "is_bot": bool(getattr(participant, "bot", False)),
                    "is_premium": bool(getattr(participant, "premium", False)),
                    # The participant iterator does not provide a reliable role for every user.
                    "group_role": "unknown",
                })
                count += 1
                if on_progress and (enrich_profiles or count % 100 == 0):
                    on_progress(count)
            writer.writerow({"record_type": "project", "note": attribution.text()})
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, final)
    except (errors.ChatAdminRequiredError, errors.ChannelPrivateError) as exc:
        Path(tmp_name).unlink(missing_ok=True)
        if isinstance(exc, errors.ChatAdminRequiredError):
            raise PermissionError("Telegram requires administrator privileges to list this group's members.") from None
        raise PermissionError("This group is private or is not accessible to the current account.") from None
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    note = "Only members returned by Telegram are included; Telegram may hide members. Completeness cannot be guaranteed."
    if enrich_profiles:
        note += f" Profile details were requested for {profiles_attempted} members (limit {PROFILE_DETAILS_LIMIT}); {profiles_available} profiles were available."
    return {"path": str(final), "members_exported": count, "limit": max_members, "complete": False,
            "profile_details_attempted": profiles_attempted, "profile_details_available": profiles_available, "note": note}


async def export_group_members_batch(client: Any, chats: list[dict[str, Any]], out_dir: Path, *,
                                     plan: str = "", enrich_profiles: bool = False,
                                     on_progress=None) -> dict[str, Any]:
    """Export visible members of several groups into one CSV with source labels."""
    chats = list(chats)
    if not chats:
        raise ValueError("Choose at least one group.")
    out_dir = Path(out_dir)
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = out_dir / f"members_selected_{stamp}_{uuid.uuid4().hex[:8]}.csv"
    fd, tmp_name = tempfile.mkstemp(prefix=".members-batch-", suffix=".part", dir=out_dir)
    count = 0
    failures: list[dict[str, str]] = []
    plan_text = str(plan or "").strip()[:200]
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(BATCH_MEMBER_FIELDS))
            writer.writeheader()
            successful: list[str] = []
            for index, source in enumerate(chats):
                if index:
                    await asyncio.sleep(3)
                chat_id = int(source["id"])
                title = str(source.get("title") or chat_id)
                group_names = [str(name) for name in (source.get("groups") or []) if str(name).strip()]
                label = "; ".join(group_names)
                if plan_text:
                    label = f"{label} / {plan_text}" if label else plan_text
                try:
                    entity = await client.get_entity(chat_id)
                except (errors.ChatAdminRequiredError, errors.ChannelPrivateError) as exc:
                    failures.append({"chat_id": str(chat_id), "title": title,
                                     "error": "Недостаточно прав или чат недоступен." if isinstance(exc, errors.ChatAdminRequiredError) else "Чат приватный или недоступен."})
                    continue
                except errors.RPCError as exc:
                    from .chat_add import _raise_wait
                    _raise_wait(exc)
                    failures.append({"chat_id": str(chat_id), "title": title,
                                     "error": type(exc).__name__})
                    continue
                if not isinstance(entity, (Chat, Channel)) or (isinstance(entity, Channel) and not entity.megagroup):
                    failures.append({"chat_id": str(chat_id), "title": title,
                                     "error": "Это не группа или супергруппа."})
                    continue
                profiles_attempted = profiles_available = 0
                try:
                    async for participant in pace_iterator(client.iter_participants(entity)):
                        bio = channel_id = channel_title = channel_username = ""
                        if enrich_profiles and profiles_attempted < PROFILE_DETAILS_LIMIT:
                            if profiles_attempted:
                                await asyncio.sleep(PROFILE_REQUEST_INTERVAL)
                            profiles_attempted += 1
                            try:
                                profile = await client(functions.users.GetFullUserRequest(id=participant))
                                full = profile.full_user
                                profiles_available += 1
                                bio = getattr(full, "about", None) or ""
                                channel_id = getattr(full, "personal_channel_id", None) or ""
                                for channel in getattr(profile, "chats", ()) or ():
                                    if channel_id and getattr(channel, "id", None) == channel_id:
                                        channel_title = getattr(channel, "title", None) or ""
                                        channel_username = getattr(channel, "username", None) or ""
                                        break
                            except (errors.UserPrivacyRestrictedError, errors.UserIdInvalidError,
                                    errors.PeerIdInvalidError, errors.UserNotMutualContactError, ValueError):
                                pass
                        writer.writerow({
                            "record_type": "member",
                            "user_id": getattr(participant, "id", ""),
                            "username": _spreadsheet_text(getattr(participant, "username", None)),
                            "first_name": _spreadsheet_text(getattr(participant, "first_name", None)),
                            "last_name": _spreadsheet_text(getattr(participant, "last_name", None)),
                            "phone": _spreadsheet_text(getattr(participant, "phone", None)),
                            "bio": _spreadsheet_text(bio),
                            "personal_channel_id": channel_id,
                            "personal_channel_title": _spreadsheet_text(channel_title),
                            "personal_channel_username": _spreadsheet_text(channel_username),
                            "is_bot": bool(getattr(participant, "bot", False)),
                            "is_premium": bool(getattr(participant, "premium", False)),
                            "group_role": "unknown",
                            "source_group_id": chat_id,
                            "source_group_title": _spreadsheet_text(title),
                            "block group / plan": _spreadsheet_text(label),
                        })
                        count += 1
                        if on_progress and (enrich_profiles or count % 25 == 0):
                            on_progress(count)
                except errors.RPCError as exc:
                    from .chat_add import _raise_wait
                    _raise_wait(exc)
                    failures.append({"chat_id": str(chat_id), "title": title,
                                     "error": "Список участников недоступен." if isinstance(exc, errors.ChatAdminRequiredError) else type(exc).__name__})
                else:
                    successful.append(str(chat_id))
            writer.writerow({"record_type": "project", "note": attribution.text()})
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, final)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    if count == 0 and failures:
        final.unlink(missing_ok=True)
        raise PermissionError("Не удалось прочитать участников ни одной выбранной группы: " +
                              "; ".join(f"{f['title']}: {f['error']}" for f in failures))
    note = ("Объединены участники, видимые текущему аккаунту. Telegram может скрывать участников; "
            "полнота списка не гарантируется.")
    if plan_text:
        note += f" Метка плана: {plan_text}."
    if failures:
        note += " Не удалось прочитать: " + "; ".join(f"{f['title']}: {f['error']}" for f in failures)
    return {"path": str(final), "members_exported": count, "groups_exported": len(successful), "successful_group_ids": successful,
            "failures": failures, "complete": False, "note": note}


class TelegramJobQueue:
    """In-process serialized task queue; all Telegram jobs use one live client owner."""
    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.lock = asyncio.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.jobs = payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            self.jobs = {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.jobs, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.state_path)

    def status(self, job_id: str | None = None) -> Any:
        if job_id:
            return self.jobs.get(job_id)
        return list(self.jobs.values())[-30:]

    async def execute(self, client: Any, kind: str, operation, *, idempotency_key: str) -> dict[str, Any]:
        async with self.lock:
            for old in self.jobs.values():
                if old.get("idempotency_key") == idempotency_key and old.get("status") in {"running", "success"}:
                    return old
            job_id = uuid.uuid4().hex
            job = {"job_id": job_id, "idempotency_key": idempotency_key, "kind": kind, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(), "progress": 0}
            self.jobs[job_id] = job; self._save()
            try:
                result = await operation(client)
                job.update(status="success", progress=100, result=result, finished_at=datetime.now(timezone.utc).isoformat())
            except errors.FloodWaitError as exc:
                job.update(status="waiting", wait_seconds=int(exc.seconds), progress=0, error="Telegram FloodWait", finished_at=datetime.now(timezone.utc).isoformat())
                self._save()
                raise RuntimeError(f"Telegram requested a wait of {exc.seconds} seconds. The job was not retried automatically.") from None
            except Exception as exc:
                job.update(status="failed", error=type(exc).__name__, finished_at=datetime.now(timezone.utc).isoformat())
                self._save()
                raise
            self._save()
            return job
