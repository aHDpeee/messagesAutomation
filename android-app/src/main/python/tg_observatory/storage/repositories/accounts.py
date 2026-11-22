"""Account profile repository; never stores API credentials or session keys."""

from __future__ import annotations

from tg_observatory.models.account import AccountProfile
from tg_observatory.storage.database import Database
from tg_observatory.storage.time import utc_now


class AccountRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save_profile(self, profile: AccountProfile) -> None:
        now = utc_now()
        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT telegram_id FROM accounts WHERE telegram_id IS NOT NULL LIMIT 1"
            ).fetchone()
            if existing is not None and int(existing["telegram_id"]) != profile.telegram_id:
                from tg_observatory.exceptions import InvalidRequest
                raise InvalidRequest("This archive belongs to a different Telegram account. Use a separate workspace to avoid mixing chats.")
            row = connection.execute(
                "SELECT id FROM accounts WHERE telegram_id = ?", (profile.telegram_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO accounts
                       (telegram_id, username, first_name, last_name, phone_hint,
                        display_name, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        profile.telegram_id,
                        profile.username,
                        profile.first_name,
                        profile.last_name,
                        profile.phone_hint,
                        " ".join(filter(None, (profile.first_name, profile.last_name))),
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """UPDATE accounts SET username = ?, first_name = ?, last_name = ?,
                       phone_hint = COALESCE(?, phone_hint), display_name = ?,
                       updated_at = ? WHERE id = ?""",
                    (
                        profile.username,
                        profile.first_name,
                        profile.last_name,
                        profile.phone_hint,
                        " ".join(filter(None, (profile.first_name, profile.last_name))),
                        now,
                        row["id"],
                    ),
                )

    def get_profile(self) -> AccountProfile | None:
        row = self._database.fetchone(
            """SELECT telegram_id, username, first_name, last_name, phone_hint
               FROM accounts WHERE telegram_id IS NOT NULL ORDER BY id LIMIT 1"""
        )
        if row is None:
            return None
        return AccountProfile(
            telegram_id=row["telegram_id"],
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            phone_hint=row["phone_hint"],
        )

    def local_id(self, telegram_id: int) -> int | None:
        row = self._database.fetchone(
            "SELECT id FROM accounts WHERE telegram_id = ?", (telegram_id,)
        )
        return int(row["id"]) if row is not None else None

    def dashboard_record(self) -> dict[str, object] | None:
        row = self._database.fetchone(
            """SELECT telegram_id, username, first_name, last_name, phone_hint, last_sync_at
               FROM accounts WHERE telegram_id IS NOT NULL ORDER BY id LIMIT 1"""
        )
        return dict(row) if row is not None else None

    def mark_synced(self, telegram_id: int) -> None:
        now = utc_now()
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE accounts SET last_sync_at = ?, updated_at = ? WHERE telegram_id = ?",
                (now, now, telegram_id),
            )
