"""Persist media download state for indexed messages."""

from __future__ import annotations

from typing import Any

from tg_observatory.storage.database import Database


class MediaRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def indexed_media(self, account_id: int, dialog_telegram_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self._database.fetchall(
            """SELECT m.id AS message_row_id, m.telegram_message_id, m.media_type
               FROM messages AS m JOIN dialogs AS d ON d.id = m.dialog_id
               WHERE d.account_id = ? AND d.telegram_id = ? AND m.media_type IS NOT NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM media AS saved
                   WHERE saved.message_id = m.id AND saved.download_state = 'completed'
                 )
               ORDER BY m.telegram_message_id DESC LIMIT ?""",
            (account_id, dialog_telegram_id, limit),
        )
        return [dict(row) for row in rows]

    def completed(self, message_row_id: int) -> bool:
        row = self._database.fetchone(
            "SELECT 1 FROM media WHERE message_id = ? AND download_state = 'completed' LIMIT 1",
            (message_row_id,),
        )
        return row is not None

    def mark_downloading(self, message_row_id: int, kind: str) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                """INSERT INTO media(message_id, kind, filename, download_state)
                   VALUES (?, ?, '', 'downloading')
                   ON CONFLICT(message_id, kind, filename) DO UPDATE SET
                     download_state='downloading', local_path=NULL""",
                (message_row_id, kind),
            )

    def mark_failed(self, message_row_id: int, kind: str) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                """UPDATE media SET download_state='failed'
                   WHERE message_id=? AND kind=? AND filename=''""",
                (message_row_id, kind),
            )

    def complete(
        self,
        message_row_id: int,
        kind: str,
        mime_type: str | None,
        filename: str,
        size_bytes: int,
        local_path: str,
        sha256: str,
    ) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                "DELETE FROM media WHERE message_id=? AND kind=? AND filename=''",
                (message_row_id, kind),
            )
            connection.execute(
                """INSERT INTO media(message_id, kind, mime_type, filename, size_bytes,
                   local_path, sha256, download_state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'completed')
                   ON CONFLICT(message_id, kind, filename) DO UPDATE SET
                     mime_type=excluded.mime_type, size_bytes=excluded.size_bytes,
                     local_path=excluded.local_path, sha256=excluded.sha256,
                     download_state='completed'""",
                (message_row_id, kind, mime_type, filename, size_bytes, local_path, sha256),
            )

    def storage_usage(self, account_id: int) -> int:
        row = self._database.fetchone(
            """SELECT COALESCE(SUM(media.size_bytes), 0) AS total
               FROM media JOIN messages ON messages.id=media.message_id
               JOIN dialogs ON dialogs.id=messages.dialog_id
               WHERE dialogs.account_id=? AND media.download_state='completed'""",
            (account_id,),
        )
        return int(row["total"]) if row is not None else 0

    def list_completed(self, account_id: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._database.fetchall(
            """SELECT media.id, media.local_path, media.filename, media.mime_type, media.size_bytes,
                      media.sha256, messages.telegram_message_id, dialogs.telegram_id AS dialog_id,
                      dialogs.title AS dialog_title
               FROM media JOIN messages ON messages.id=media.message_id
               JOIN dialogs ON dialogs.id=messages.dialog_id
               WHERE dialogs.account_id=? AND media.download_state='completed'
               ORDER BY messages.date_utc DESC, media.id DESC LIMIT ?""",
            (account_id, max(1, min(limit, 500))),
        )
        return [dict(row) for row in rows]

    def get_completed_for_account(self, media_id: int, account_id: int) -> dict[str, Any] | None:
        row = self._database.fetchone(
            """SELECT media.id, media.local_path, media.filename, media.size_bytes
               FROM media JOIN messages ON messages.id=media.message_id
               JOIN dialogs ON dialogs.id=messages.dialog_id
               WHERE media.id=? AND dialogs.account_id=? AND media.download_state='completed'""",
            (media_id, account_id),
        )
        return dict(row) if row is not None else None

    def delete_completed_for_account(self, media_id: int, account_id: int) -> bool:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """DELETE FROM media WHERE id=? AND download_state='completed' AND message_id IN (
                     SELECT messages.id FROM messages JOIN dialogs ON dialogs.id=messages.dialog_id
                     WHERE dialogs.account_id=?
                   )""",
                (media_id, account_id),
            )
            return cursor.rowcount == 1
