"""Read-only access to an existing Telegram Archive SQLite database.

Exports are snapshots of data already acquired by the older application. This
module does not open a Telegram session or alter the old app's checkpoints.
"""
from __future__ import annotations

import csv
import os
import sqlite3
import tempfile
import threading
import uuid
from . import attribution
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .direct_export import spreadsheet_record


def default_database() -> Path:
    configured = os.environ.get("TELEGRAM_ARCHIVE_HOME")
    if configured:
        root = Path(configured).expanduser()
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "TelegramArchive"
    return root / "data" / "backups" / "telegram_backup.db"


DEFAULT_DB = default_database()
_REQUIRED = {
    "chats": {"id", "account_id", "title", "type", "username"},
    "messages": {"id", "account_id", "chat_id", "date", "sender_id", "sender_name", "text", "reply_to_msg_id", "edit_date", "is_deleted"},
}


def _open(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("The existing archive path must be a SQLite file.")
    # Do not use immutable=1: it can silently omit committed data still in WAL.
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        for table, required in _REQUIRED.items():
            fields = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not required.issubset(fields):
                raise ValueError(f"Unsupported Telegram Archive database: missing required {table} fields.")
        return connection
    except BaseException:
        connection.close()
        raise


def list_chats(db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    """List archived chats, distinguishing accounts with the same chat ID."""
    with closing(_open(db_path)) as connection:
        rows = connection.execute(
            "SELECT account_id, id, title, type, username FROM chats ORDER BY title COLLATE NOCASE, account_id, id"
        )
        return [{"account_id": row["account_id"], "id": str(row["id"]),
                 "title": row["title"] or row["username"] or str(row["id"]),
                 "type": row["type"] or "chat", "username": row["username"] or ""}
                for row in rows]


def export_chat(db_path: Path, out_dir: Path, account_id: int | None, chat_id: int,
                *, since: datetime | None = None, until: datetime | None = None,
                on_progress: Callable[[int], None] | None = None,
                cancel_event: threading.Event | None = None) -> dict[str, Any]:
    """Stream one account/chat to CSV. Bounds are inclusive UTC instants.

    Telegram Archive's naive database dates are treated as UTC. Missing or
    invalid dates are excluded when bounds are supplied. No media blobs or raw
    Telegram JSON are copied. Existing archive state is never modified.
    """
    chat_id = int(chat_id)
    account_id = int(account_id) if account_id is not None else None
    if (since is None) != (until is None):
        raise ValueError("Specify both date bounds or neither.")
    if since is not None:
        if since.utcoffset() is None or until.utcoffset() is None or since > until:
            raise ValueError("Date bounds must be timezone-aware and ordered.")
        since, until = since.astimezone(timezone.utc), until.astimezone(timezone.utc)
    out_dir = Path(out_dir).expanduser()
    temporary = None
    with closing(_open(db_path)) as connection:
        connection.execute("BEGIN")  # one consistent read snapshot, including WAL
        chats = connection.execute(
            "SELECT id, title, type, username FROM chats WHERE account_id IS ? AND id = ? LIMIT 2",
            (account_id, chat_id),
        ).fetchall()
        if len(chats) != 1:
            raise ValueError("The selected account/chat is missing or ambiguous in the old archive.")
        chat = chats[0]
        query = ("SELECT id, date, sender_id, sender_name, text, reply_to_msg_id, edit_date, is_deleted "
                 "FROM messages WHERE account_id IS ? AND chat_id = ?")
        args: list[Any] = [account_id, chat_id]
        if since is not None:
            query += " AND julianday(date) >= julianday(?) AND julianday(date) <= julianday(?)"
            args.extend((since.isoformat(), until.isoformat()))
        query += " ORDER BY id"
        rows = connection.execute(query, args)
        out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        account_label = "unknown" if account_id is None else str(account_id)
        final = out_dir / f"legacy_{account_label}_{chat_id}_{stamp}_{uuid.uuid4().hex[:8]}.csv"
        fd, temporary = tempfile.mkstemp(prefix=".legacy-", suffix=".part", dir=out_dir)
        count = 0
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("record_type", "account_id", "chat_id", "chat_title", "chat_type", "id", "date", "edit_date", "sender_id", "sender", "sender_name", "reply_to_msg_id", "reply_to_message_id", "text", "is_deleted"))
                writer.writeheader()
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("Legacy export was cancelled.")
                for row in rows:
                    if cancel_event and cancel_event.is_set():
                        raise InterruptedError("Legacy export was cancelled.")
                    sender = row["sender_name"] or None
                    reply = row["reply_to_msg_id"]
                    deleted = row["is_deleted"]
                    record = {"record_type": "message", "account_id": account_id, "chat_id": chat_id,
                              "chat_title": chat["title"] or chat["username"] or str(chat_id),
                              "chat_type": chat["type"] or "chat", "id": row["id"],
                              "date": row["date"], "edit_date": row["edit_date"],
                              "sender_id": row["sender_id"], "sender": sender,
                              "sender_name": sender, "reply_to_msg_id": reply,
                              "reply_to_message_id": reply, "text": row["text"] or "",
                              "is_deleted": None if deleted is None else bool(deleted)}
                    writer.writerow(spreadsheet_record(record))
                    count += 1
                    if on_progress and count % 1000 == 0:
                        on_progress(count)
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("Legacy export was cancelled.")
                writer.writerow({"record_type": "project", "text": attribution.text()})
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    return {"path": str(final), "messages": count, "account_id": account_id,
            "chat_id": chat_id, "from": since.isoformat() if since else None,
            "to": until.isoformat() if until else None,
            "note": "Exported from the existing local archive; it may not contain the latest Telegram messages."}
