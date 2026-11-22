"""Dialog persistence and indexed search."""

from __future__ import annotations

import builtins

from tg_observatory.models.dialog import DialogSummary
from tg_observatory.storage.database import Database
from tg_observatory.storage.time import utc_now


class DialogRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def sync(self, account_id: int, dialogs: list[DialogSummary]) -> int:
        now = utc_now()
        with self._database.transaction() as connection:
            for dialog in dialogs:
                connection.execute(
                    """INSERT INTO dialogs
                       (account_id, telegram_id, raw_telegram_id, title, kind, username,
                        unread_count, participant_count, last_message_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(account_id, telegram_id) DO UPDATE SET
                         raw_telegram_id = excluded.raw_telegram_id,
                         title = excluded.title,
                         kind = excluded.kind,
                         username = excluded.username,
                         unread_count = excluded.unread_count,
                         participant_count = excluded.participant_count,
                         last_message_at = excluded.last_message_at,
                         updated_at = excluded.updated_at""",
                    (
                        account_id,
                        dialog.telegram_id,
                        dialog.raw_telegram_id,
                        dialog.title,
                        dialog.kind,
                        dialog.username,
                        dialog.unread_count,
                        dialog.participant_count,
                        dialog.last_message_at,
                        now,
                    ),
                )
        return len(dialogs)

    def list(
        self,
        account_id: int,
        *,
        query: str = "",
        kind: str | None = None,
        sort: str = "activity",
        limit: int = 100,
        offset: int = 0,
        list_name: str | None = None,
        hide_channels: bool = False,
    ) -> list[dict[str, object]]:
        filters = ["account_id = ?"]
        parameters: list[object] = [account_id]
        preview = "(SELECT text FROM messages WHERE dialog_id=dialogs.id ORDER BY telegram_message_id DESC LIMIT 1)"
        rank = f"dialog_rank(?, title, username, {preview})"
        if query:
            filters.append(f"{rank}>0")
            parameters.append(query)
        if kind:
            filters.append("kind = ?")
            parameters.append(kind)
        if list_name == "recent":
            # Recent is Telegram's activity list, not Observatory's local open history.
            filters.append("last_message_at IS NOT NULL")
            limit = min(limit, max(0, 50 - offset))
            if limit == 0:
                return []
        elif list_name == "personal":
            filters.append("(kind IN ('private', 'bot') OR favorite=1)")
        elif list_name == "communities":
            filters.append("kind IN ('group', 'channel')")
        if hide_channels:
            filters.append("kind != 'channel'")
        order_by = "last_message_at IS NULL, last_message_at DESC, title COLLATE NOCASE" if list_name == "recent" else {
            "name": "title COLLATE NOCASE, telegram_id",
            "activity": "last_message_at IS NULL, last_message_at DESC, title COLLATE NOCASE",
            "unread": "unread_count DESC, title COLLATE NOCASE",
        }.get(sort, "last_message_at IS NULL, last_message_at DESC, title COLLATE NOCASE")
        if query:
            order_by = f"{rank} DESC, " + order_by
            parameters.append(query)
        order_by += ", telegram_id"
        parameters.extend((max(1, min(limit, 500)), max(offset, 0)))
        rows = self._database.fetchall(
            f"""SELECT telegram_id, raw_telegram_id, kind, title, username,
                       unread_count, participant_count, last_message_at, favorite, last_opened_at
                FROM dialogs WHERE {' AND '.join(filters)}
                ORDER BY {order_by} LIMIT ? OFFSET ?""",
            parameters,
        )
        return [dict(row) for row in rows]

    def count_by_kind(self, account_id: int) -> dict[str, int]:
        rows = self._database.fetchall(
            "SELECT kind, COUNT(*) AS count FROM dialogs WHERE account_id = ? GROUP BY kind",
            (account_id,),
        )
        return {row["kind"]: row["count"] for row in rows}

    def count(self, account_id: int) -> int:
        row = self._database.fetchone(
            "SELECT COUNT(*) AS count FROM dialogs WHERE account_id = ?", (account_id,)
        )
        return int(row["count"]) if row is not None else 0

    def latest_activity(self, account_id: int) -> builtins.list[dict[str, object]]:
        rows = self._database.fetchall(
            """SELECT telegram_id, kind, title, last_message_at FROM dialogs
               WHERE account_id = ? AND last_message_at IS NOT NULL
               ORDER BY last_message_at DESC LIMIT 5""",
            (account_id,),
        )
        return [dict(row) for row in rows]

    def mark_opened(self, account_id: int, telegram_id: int) -> bool:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE dialogs SET last_opened_at=? WHERE account_id=? AND telegram_id=?",
                (utc_now(), account_id, telegram_id),
            )
            return cursor.rowcount == 1

    def set_favorite(self, account_id: int, telegram_id: int, favorite: bool) -> bool:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE dialogs SET favorite=? WHERE account_id=? AND telegram_id=?",
                (int(favorite), account_id, telegram_id),
            )
            return cursor.rowcount == 1
