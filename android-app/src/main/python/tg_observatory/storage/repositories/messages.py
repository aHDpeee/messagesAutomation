"""Message indexing operations with stable Telegram identifiers."""

from __future__ import annotations

import json
import builtins
from typing import Any

from tg_observatory.exceptions import InvalidRequest
from tg_observatory.storage.database import Database
from tg_observatory.storage.time import utc_now


class MessageRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def upsert_batch(self, dialog_id: int, messages: list[dict[str, Any]]) -> int:
        now = utc_now()
        with self._database.transaction() as connection:
            for message in messages:
                sender_id = message.get("sender_id")
                local_sender_id = None
                if sender_id is not None:
                    connection.execute(
                        """INSERT INTO users(telegram_id, username, display_name, updated_at)
                           VALUES (?, ?, ?, ?) ON CONFLICT(telegram_id) DO UPDATE SET
                           username=COALESCE(NULLIF(excluded.username, ''), users.username),
                           display_name=COALESCE(NULLIF(excluded.display_name, ''), users.display_name),
                           updated_at=excluded.updated_at""",
                        (sender_id, message.get("sender_username"),
                         message.get("sender_name"), now),
                    )
                    local_sender_id = connection.execute(
                        "SELECT id FROM users WHERE telegram_id=?", (sender_id,)
                    ).fetchone()[0]
                connection.execute(
                    """INSERT INTO messages(
                       dialog_id, telegram_message_id, sender_user_id, date_utc, text,
                       reply_to_message_id, edit_date_utc, forward_from_peer_id,
                       media_type, raw_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(dialog_id, telegram_message_id) DO UPDATE SET
                       sender_user_id=COALESCE(excluded.sender_user_id, messages.sender_user_id), date_utc=excluded.date_utc,
                       text=excluded.text, reply_to_message_id=excluded.reply_to_message_id,
                       edit_date_utc=excluded.edit_date_utc,
                       forward_from_peer_id=excluded.forward_from_peer_id,
                       media_type=excluded.media_type, raw_json=excluded.raw_json""",
                    (dialog_id, message["message_id"], local_sender_id,
                     message["date_utc"], message.get("text"),
                     message.get("reply_to_message_id"), message.get("edit_date_utc"),
                     message.get("forward_from_peer_id"), message.get("media_type"),
                     json.dumps(message.get("raw", {}), ensure_ascii=False, separators=(",", ":"))),
                )
        return len(messages)

    def list(
        self,
        dialog_id: int,
        *,
        limit: int = 100,
        before_id: int | None = None,
        query: str = "",
    ) -> list[dict[str, Any]]:
        sql = """SELECT m.telegram_message_id, m.date_utc, m.text, m.reply_to_message_id,
                        m.edit_date_utc, m.forward_from_peer_id, m.media_type,
                        m.raw_json, u.telegram_id AS sender_id, u.username AS sender_username,
                        u.display_name AS sender_name
                 FROM messages m LEFT JOIN users u ON u.id=m.sender_user_id
                 WHERE m.dialog_id=?"""
        params: list[Any] = [dialog_id]
        if before_id is not None:
            sql += " AND m.telegram_message_id < ?"
            params.append(before_id)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql += " AND COALESCE(m.text, '') LIKE ? ESCAPE '\\'"
            params.append(f"%{escaped}%")
        sql += " ORDER BY m.telegram_message_id DESC LIMIT ?"
        params.append(limit)
        result = []
        for row in self._database.fetchall(sql, params):
            item = dict(row)
            item["message_id"] = item.pop("telegram_message_id")
            item["raw"] = json.loads(item.pop("raw_json"))
            result.append(item)
        return result

    def export_page(self, dialog_id: int, *, after_id: int = 0, limit: int = 500,
                    end_id: int | None = None, period_start: str | None = None,
                    period_end: str | None = None, source_job: str | None = None) -> builtins.list[dict[str, Any]]:
        """Read one chronological keyset page for bounded-memory streaming exports."""
        rows = self._database.fetchall(
            """SELECT m.telegram_message_id, m.date_utc, m.text, m.reply_to_message_id,
                      m.edit_date_utc, m.forward_from_peer_id, m.media_type, m.raw_json,
                      u.telegram_id AS sender_id, u.username AS sender_username,
                      u.display_name AS sender_name
               FROM messages m LEFT JOIN users u ON u.id=m.sender_user_id
               WHERE m.dialog_id=? AND m.telegram_message_id>?
                 AND (? IS NULL OR m.telegram_message_id<=?)
                 AND (? IS NULL OR timestamp_us(m.date_utc)>=timestamp_us(?))
                 AND (? IS NULL OR timestamp_us(m.date_utc)<timestamp_us(?))
                 AND (? IS NULL OR EXISTS (SELECT 1 FROM export_source_messages source WHERE source.export_job_id=? AND source.telegram_message_id=m.telegram_message_id))
               ORDER BY m.telegram_message_id ASC LIMIT ?""",
            (dialog_id, after_id, end_id, end_id, period_start, period_start, period_end, period_end, source_job, source_job, limit),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["message_id"] = item.pop("telegram_message_id")
            item["raw"] = json.loads(item.pop("raw_json"))
            result.append(item)
        return result

    def count(self, dialog_id: int) -> int:
        row = self._database.fetchone("SELECT COUNT(*) AS count FROM messages WHERE dialog_id=?", (dialog_id,))
        return int(row["count"]) if row is not None else 0

    def range_page(
        self, dialog_id: int, *, after_id: int | None = None, limit: int = 100,
        period_start: str | None = None, period_end: str | None = None,
    ) -> dict[str, Any]:
        """Chronological, bounded pages for the range picker, with period statistics."""
        where = "m.dialog_id=?"
        params: builtins.list[Any] = [dialog_id]
        if period_start is not None:
            where += " AND timestamp_us(m.date_utc)>=timestamp_us(?)"
            params.append(period_start)
        if period_end is not None:
            where += " AND timestamp_us(m.date_utc)<timestamp_us(?)"
            params.append(period_end)
        total = self._database.fetchone(f"SELECT COUNT(*) AS count FROM messages m WHERE {where}", params)
        assert total is not None
        earliest = self._database.fetchone(
            "SELECT date_utc FROM messages WHERE dialog_id=? ORDER BY timestamp_us(date_utc), telegram_message_id LIMIT 1",
            (dialog_id,),
        )
        latest = self._database.fetchone(
            "SELECT date_utc FROM messages WHERE dialog_id=? ORDER BY timestamp_us(date_utc) DESC, telegram_message_id DESC LIMIT 1",
            (dialog_id,),
        )
        if after_id is not None:
            # Cursor timestamp + ID preserves chronological paging even for equal or edited dates.
            cursor = self._database.fetchone(
                "SELECT date_utc FROM messages WHERE dialog_id=? AND telegram_message_id=?",
                (dialog_id, after_id),
            )
            if cursor is None:
                raise InvalidRequest("after_id must identify an indexed message in this dialog.")
            where += " AND (timestamp_us(m.date_utc)>timestamp_us(?) OR (timestamp_us(m.date_utc)=timestamp_us(?) AND m.telegram_message_id>?))"
            params.extend((cursor["date_utc"], cursor["date_utc"], after_id))
        rows = self._database.fetchall(
            f"""SELECT m.telegram_message_id, m.date_utc, m.text, m.reply_to_message_id,
                       m.edit_date_utc, m.forward_from_peer_id, m.media_type, m.raw_json,
                       u.telegram_id AS sender_id, u.username AS sender_username,
                       u.display_name AS sender_name
                FROM messages m LEFT JOIN users u ON u.id=m.sender_user_id
                WHERE {where} ORDER BY timestamp_us(m.date_utc), m.telegram_message_id LIMIT ?""",
            [*params, limit + 1],
        )
        items = []
        for row in rows[:limit]:
            item = dict(row)
            item["message_id"] = item.pop("telegram_message_id")
            item["raw"] = json.loads(item.pop("raw_json"))
            items.append(item)
        return {
            "items": items, "has_more": len(rows) > limit, "total": int(total["count"]),
            "earliest": earliest["date_utc"] if earliest else None,
            "latest": latest["date_utc"] if latest else None,
            "source": "local", "next_after_id": items[-1]["message_id"] if items else None,
        }
