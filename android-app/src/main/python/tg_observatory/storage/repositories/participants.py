"""Participant snapshot persistence and local snapshot browsing."""

from __future__ import annotations

from typing import Any

from tg_observatory.exceptions import InvalidRequest
from tg_observatory.storage.database import Database
from tg_observatory.storage.time import utc_now


class ParticipantRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def begin_capture(self, account_id: int, dialog_telegram_id: int) -> tuple[int, str, int]:
        captured_at = utc_now()
        with self._database.transaction() as connection:
            dialog = connection.execute(
                "SELECT id, kind FROM dialogs WHERE account_id = ? AND telegram_id = ?",
                (account_id, dialog_telegram_id),
            ).fetchone()
            if dialog is None:
                raise InvalidRequest("Sync this dialog before collecting participants.")
            if dialog["kind"] not in {"group", "channel"}:
                raise InvalidRequest("Participants are available only for groups and channels.")
            cursor = connection.execute(
                """INSERT INTO participant_snapshots(dialog_id, captured_at, participant_count, complete)
                   VALUES (?, ?, 0, 0)""",
                (dialog["id"], captured_at),
            )
            snapshot_id = cursor.lastrowid
            if snapshot_id is None:
                raise RuntimeError("Participant snapshot insert did not return an ID.")
            return snapshot_id, captured_at, int(dialog["id"])

    def add_members(
        self, snapshot_id: int, dialog_id: int, captured_at: str, users: list[dict[str, Any]]
    ) -> int:
        added = 0
        with self._database.transaction() as connection:
            for user in users:
                telegram_id = int(user["telegram_id"])
                name = " ".join(filter(None, (user.get("first_name"), user.get("last_name"))))
                connection.execute(
                    """INSERT INTO users(telegram_id, username, display_name, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(telegram_id) DO UPDATE SET
                         username = excluded.username,
                         display_name = excluded.display_name,
                         updated_at = excluded.updated_at""",
                    (telegram_id, user.get("username"), name or None, captured_at),
                )
                row = connection.execute(
                    "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
                ).fetchone()
                user_id = int(row["id"])
                connection.execute(
                    "INSERT OR IGNORE INTO participants(dialog_id, user_id, observed_at) VALUES (?, ?, ?)",
                    (dialog_id, user_id, captured_at),
                )
                before = connection.total_changes
                connection.execute(
                    "INSERT OR IGNORE INTO participant_snapshot_members(snapshot_id, user_id) VALUES (?, ?)",
                    (snapshot_id, user_id),
                )
                added += connection.total_changes - before
            connection.execute(
                "UPDATE participant_snapshots SET participant_count = ? WHERE id = ? AND complete = 0",
                (self._snapshot_count(connection, snapshot_id), snapshot_id),
            )
        return added

    @staticmethod
    def _snapshot_count(connection: Any, snapshot_id: int) -> int:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM participant_snapshot_members WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return int(row["count"])

    def finish_capture(self, snapshot_id: int) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE participant_snapshots SET complete = 1 WHERE id = ? AND complete = 0",
                (snapshot_id,),
            )
            row = connection.execute(
                "SELECT complete FROM participant_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
            if row is None or not row["complete"]:
                raise InvalidRequest("Participant snapshot could not be completed.")

    def count(self, snapshot_id: int) -> int:
        row = self._database.fetchone(
            "SELECT participant_count FROM participant_snapshots WHERE id = ?", (snapshot_id,)
        )
        return int(row["participant_count"]) if row is not None else 0

    def capture(
        self, account_id: int, dialog_telegram_id: int, users: list[dict[str, Any]]
    ) -> dict[str, Any]:
        snapshot_id, captured_at, dialog_id = self.begin_capture(account_id, dialog_telegram_id)
        self.add_members(snapshot_id, dialog_id, captured_at, users)
        self.finish_capture(snapshot_id)
        return {
            "snapshot_id": snapshot_id,
            "captured_at": captured_at,
            "count": self.count(snapshot_id),
        }

    def latest(self, account_id: int, dialog_telegram_id: int) -> dict[str, Any]:
        dialog = self._database.fetchone(
            "SELECT id FROM dialogs WHERE account_id = ? AND telegram_id = ?",
            (account_id, dialog_telegram_id),
        )
        if dialog is None:
            return {"snapshot": None, "items": []}
        snapshot = self._database.fetchone(
            """SELECT id, captured_at, participant_count FROM participant_snapshots
               WHERE dialog_id = ? AND complete = 1 ORDER BY captured_at DESC, id DESC LIMIT 1""",
            (dialog["id"],),
        )
        if snapshot is None:
            return {"snapshot": None, "items": []}
        items = self._database.fetchall(
            """SELECT u.telegram_id, u.username, u.display_name
               FROM participant_snapshot_members AS sm
               JOIN users AS u ON u.id = sm.user_id
               WHERE sm.snapshot_id = ? ORDER BY u.display_name COLLATE NOCASE, u.telegram_id""",
            (snapshot["id"],),
        )
        return {"snapshot": dict(snapshot), "items": [dict(row) for row in items]}

    def compare_latest(self, account_id: int, dialog_telegram_ids: list[int]) -> dict[str, Any]:
        """Compare completed latest snapshots for groups owned by one local account."""
        placeholders = ",".join("?" for _ in dialog_telegram_ids)
        dialogs = self._database.fetchall(
            f"""SELECT d.telegram_id, d.title, d.kind, s.id AS snapshot_id,
                       s.captured_at, s.participant_count
                FROM dialogs AS d
                LEFT JOIN participant_snapshots AS s ON s.id = (
                    SELECT ps.id FROM participant_snapshots AS ps
                    WHERE ps.dialog_id = d.id AND ps.complete = 1
                    ORDER BY ps.captured_at DESC, ps.id DESC LIMIT 1
                )
                WHERE d.account_id = ? AND d.telegram_id IN ({placeholders})""",
            (account_id, *dialog_telegram_ids),
        )
        by_id = {int(row["telegram_id"]): dict(row) for row in dialogs}
        missing = [peer_id for peer_id in dialog_telegram_ids if peer_id not in by_id]
        if missing:
            raise InvalidRequest("One or more selected groups are not synced for this account.")
        invalid = [row["title"] for row in by_id.values() if row["kind"] not in {"group", "channel"}]
        if invalid:
            raise InvalidRequest("Participant comparisons only support groups and channels.")
        without_snapshot = [row["title"] for row in by_id.values() if row["snapshot_id"] is None]
        if without_snapshot:
            raise InvalidRequest("Capture a completed participant snapshot for every selected group first.")

        selected = [by_id[peer_id] for peer_id in dialog_telegram_ids]
        snapshot_to_dialog = {int(row["snapshot_id"]): int(row["telegram_id"]) for row in selected}
        member_rows = self._database.fetchall(
            f"""SELECT sm.snapshot_id, u.telegram_id, u.username, u.display_name
                FROM participant_snapshot_members AS sm
                JOIN users AS u ON u.id = sm.user_id
                WHERE sm.snapshot_id IN ({','.join('?' for _ in snapshot_to_dialog)})
                ORDER BY u.display_name COLLATE NOCASE, u.telegram_id""",
            tuple(snapshot_to_dialog),
        )
        memberships: dict[int, dict[str, Any]] = {}
        for row in member_rows:
            telegram_id = int(row["telegram_id"])
            item = memberships.setdefault(telegram_id, {
                "telegram_id": telegram_id,
                "username": row["username"],
                "display_name": row["display_name"],
                "group_ids": [],
            })
            item["group_ids"].append(snapshot_to_dialog[int(row["snapshot_id"])])
        members = []
        for item in memberships.values():
            group_ids = item["group_ids"]
            item["group_count"] = len(group_ids)
            item["shared"] = len(group_ids) > 1
            item["in_all_groups"] = len(group_ids) == len(selected)
            item["exclusive_to"] = group_ids[0] if len(group_ids) == 1 else None
            members.append(item)
        members.sort(key=lambda item: (-item["group_count"], (item["display_name"] or "").casefold(), item["telegram_id"]))
        exclusive_counts = {
            str(peer_id): sum(member["exclusive_to"] == peer_id for member in members)
            for peer_id in dialog_telegram_ids
        }
        return {
            "groups": [
                {
                    "telegram_id": int(row["telegram_id"]),
                    "title": row["title"],
                    "snapshot_id": int(row["snapshot_id"]),
                    "captured_at": row["captured_at"],
                    "participant_count": int(row["participant_count"]),
                }
                for row in selected
            ],
            "union_count": len(members),
            "shared_count": sum(member["shared"] for member in members),
            "intersection_count": sum(member["in_all_groups"] for member in members),
            "exclusive_counts": exclusive_counts,
            "members": members,
        }
