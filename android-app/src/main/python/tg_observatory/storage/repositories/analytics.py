"""Efficient SQL aggregates over locally indexed messages."""

from __future__ import annotations

from typing import Any

from tg_observatory.storage.database import Database


class AnalyticsRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def activity(
        self,
        account_id: int,
        *,
        dialog_telegram_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        filters = ["d.account_id = ?"]
        params: list[Any] = [account_id]
        if dialog_telegram_id is not None:
            filters.append("d.telegram_id = ?")
            params.append(dialog_telegram_id)
        if start_date is not None:
            filters.append("m.date_utc >= ?")
            params.append(f"{start_date}T00:00:00Z")
        if end_date is not None:
            filters.append("m.date_utc < ?")
            params.append(f"{end_date}T23:59:59.999999Z")
        where = " AND ".join(filters)
        source = (
            "FROM messages AS m JOIN dialogs AS d ON d.id = m.dialog_id "
            "LEFT JOIN users AS u ON u.id = m.sender_user_id "
            f"WHERE {where}"
        )
        totals = self._database.fetchone(
            f"""SELECT COUNT(*) AS messages,
                       COUNT(DISTINCT m.sender_user_id) AS senders,
                       SUM(CASE WHEN m.reply_to_message_id IS NOT NULL THEN 1 ELSE 0 END) AS replies,
                       SUM(CASE WHEN m.forward_from_peer_id IS NOT NULL THEN 1 ELSE 0 END) AS forwards
                {source}""",
            params,
        )
        # An aggregate without GROUP BY always returns one row, including an empty index.
        assert totals is not None
        daily = self._database.fetchall(
            f"SELECT substr(m.date_utc, 1, 10) AS day, COUNT(*) AS count {source} "
            "GROUP BY day ORDER BY day",
            params,
        )
        hours = self._database.fetchall(
            f"SELECT CAST(substr(m.date_utc, 12, 2) AS INTEGER) AS hour, COUNT(*) AS count {source} "
            "GROUP BY hour ORDER BY hour",
            params,
        )
        senders = self._database.fetchall(
            f"""SELECT u.telegram_id, u.username, u.display_name, COUNT(*) AS count
                {source} AND u.id IS NOT NULL
                GROUP BY u.id ORDER BY count DESC, u.telegram_id LIMIT 20""",
            params,
        )
        media = self._database.fetchall(
            f"""SELECT COALESCE(m.media_type, 'unknown') AS kind, COUNT(*) AS count
                {source} AND m.media_type IS NOT NULL GROUP BY kind ORDER BY count DESC""",
            params,
        )
        return {
            "total_messages": int(totals["messages"] or 0),
            "unique_senders": int(totals["senders"] or 0),
            "replies": int(totals["replies"] or 0),
            "forwards": int(totals["forwards"] or 0),
            "daily": [dict(row) for row in daily],
            "by_hour": [dict(row) for row in hours],
            "top_senders": [dict(row) for row in senders],
            "media": [dict(row) for row in media],
            "source": "local_index",
        }
