"""Portable, text-only Telegram message export using an already connected client."""
from __future__ import annotations

import csv
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from . import attribution


class ChatLookupError(Exception):
    """Telethon could not resolve the selected chat to an input peer."""


MESSAGE_FIELDS = ("record_type", "chat_id", "chat_title", "chat_type", "id", "date", "edit_date",
                  "sender_id", "sender_name", "sender_username", "post_author", "sender",
                  "reply_to_msg_id", "reply_to_message_id", "message_link", "text")


def message_link(chat_id: int, message_id: int, chat_info: dict[str, Any] | None) -> str:
    info = chat_info or {}
    username = info.get("username") or ""
    if info.get("type") in {"group", "channel"} and re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
        return f"https://t.me/{username}/{message_id}"
    identifier = str(chat_id)
    if info.get("type") in {"group", "channel"} and identifier.startswith("-100") and identifier[4:].isdigit():
        return f"https://t.me/c/{identifier[4:]}/{message_id}"
    return ""


def spreadsheet_record(record: dict[str, Any]) -> dict[str, Any]:
    """Protect text cells when the CSV is opened in spreadsheet software."""
    def protect(value: Any) -> Any:
        if isinstance(value, str) and (value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
            return "'" + value
        return value
    return {key: protect(value) for key, value in record.items()}


def _date(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def message_record(message: Any, chat_id: int) -> dict[str, Any]:
    """Keep text and identifiers only; never serialize media or raw objects."""
    # Telethon's sender property is cached data from the response. Do not call
    # get_sender(): fetching each sender would multiply Telegram API requests.
    sender = getattr(message, "sender", None)
    sender_name = None
    if sender is not None:
        sender_name = getattr(sender, "title", None) or " ".join(
            part for part in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if part
        ) or None
    sender_username = getattr(sender, "username", None) if sender is not None else None
    post_author = getattr(message, "post_author", None)
    reply_id = getattr(message, "reply_to_msg_id", None)
    return {
        "chat_id": chat_id,
        "id": int(message.id),
        "date": _date(getattr(message, "date", None)),
        "edit_date": _date(getattr(message, "edit_date", None)),
        "sender_id": getattr(message, "sender_id", None),
        "sender_name": sender_name,
        "sender_username": sender_username,
        "post_author": post_author,
        "sender": sender_name or sender_username or post_author,
        "reply_to_msg_id": reply_id,
        "reply_to_message_id": reply_id,
        "text": getattr(message, "message", None) or "",
    }


async def export_chat(client: Any, chat_id: int, out_dir: Path, *, after_id: int = 0, chat_info: dict[str, Any] | None = None, on_progress=None, skip_empty: bool = True, since: datetime | None = None, until: datetime | None = None, limit: int | None = None) -> dict[str, Any]:
    """Write a complete CSV file atomically; caller commits the returned checkpoint.

    Telegram messages are traversed oldest first so the highest message ID is a
    safe incremental checkpoint. A failed iteration leaves no completed file.
    """
    chat_id, after_id = int(chat_id), int(after_id)
    if after_id < 0:
        raise ValueError("Checkpoint must not be negative")
    if limit is not None and limit < 1:
        raise ValueError("Message limit must be positive")
    if (since is None) != (until is None) or (since and (since.tzinfo is None or until.tzinfo is None or since > until)):
        raise ValueError("Choose a valid timezone-aware date interval.")
    try:
        peer = await client.get_input_entity(chat_id)
    except ValueError as exc:
        raise ChatLookupError(str(exc)) from exc
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = out_dir / f"chat_{chat_id}_{stamp}_{uuid.uuid4().hex[:8]}.csv"
    fd, temporary = tempfile.mkstemp(prefix=".chat-", suffix=".part", dir=out_dir)
    count, last_id = 0, after_id
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=MESSAGE_FIELDS)
            writer.writeheader()
            options = {"min_id": after_id, "reverse": True, "wait_time": 3}
            if limit is not None:
                options["limit"] = limit
            if since is not None:
                # Telethon's reverse offset is exclusive and Telegram timestamps
                # have second precision. Filter locally for exact boundaries.
                options["offset_date"] = since - timedelta(seconds=1)
            async for message in client.iter_messages(peer, **options):
                if since is not None:
                    date = getattr(message, "date", None)
                    if date is None or date < since or date > until:
                        continue
                record = message_record(message, chat_id)
                record["record_type"] = "message"
                record["message_link"] = message_link(chat_id, record["id"], chat_info)
                if record["id"] <= last_id:
                    raise RuntimeError("Telegram returned messages out of order; the checkpoint was not advanced.")
                if chat_info:
                    record["chat_title"] = chat_info.get("title", "")
                    record["chat_type"] = chat_info.get("type", "")
                writer.writerow(spreadsheet_record(record))
                count += 1
                last_id = max(last_id, record["id"])
                if on_progress and count % 100 == 0:
                    on_progress(count)
            writer.writerow({"record_type": "project", "text": attribution.text()})
            stream.flush()
            os.fsync(stream.fileno())
        if count or not skip_empty:
            os.replace(temporary, final)
        else:
            Path(temporary).unlink()
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return {"path": str(final) if count or not skip_empty else None, "chat_id": chat_id,
            "chat_title": (chat_info or {}).get("title", ""),
            "messages": count, "after_id": after_id, "last_id": last_id,
            "from": _date(since), "to": _date(until)}
