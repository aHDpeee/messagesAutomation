import tempfile
import unittest
import asyncio
import csv
from pathlib import Path

from tg_observatory.models.account import AccountProfile
from tg_observatory.models.dialog import DialogSummary
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.dialogs import DialogRepository
from tg_observatory.storage.repositories.messages import MessageRepository


class TimestampPrecisionTests(unittest.TestCase):
    def test_live_export_excludes_cached_messages_missing_from_fetch(self):
        from tg_observatory.services.export_service import ExportService
        from tg_observatory.storage.repositories.jobs import JobRepository
        from tg_observatory.storage.repositories.exports import ExportRepository
        from tg_observatory.exceptions import EmptyMessageRange
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite")
            try:
                database.migrate()
                accounts = AccountRepository(database)
                accounts.save_profile(AccountProfile(1, None, "User", None, None))
                DialogRepository(database).sync(accounts.local_id(1), [DialogSummary(12, 12, "private", "Chat", None, 0, None, None)])
                local_id = database.fetchone("SELECT id FROM dialogs WHERE telegram_id=12")["id"]
                messages = MessageRepository(database)
                messages.upsert_batch(local_id, [
                    {"message_id": 1, "date_utc": "2026-01-01T00:00:00Z", "text": "cached only"},
                    {"message_id": 2, "date_utc": "2026-01-01T00:00:01Z", "text": "still visible"},
                ])
                jobs = JobRepository(database)
                jobs.create("live", "messages.export", {}, True)
                jobs.create("empty", "messages.export", {}, True)
                with database.transaction() as connection:
                    connection.execute("INSERT INTO export_source_messages VALUES (?, ?)", ("live", 2))
                service = ExportService(database, accounts, messages, ExportRepository(database), Path(directory) / "exports")
                params = {"dialog_id": 12, "format": "csv", "fetch_source": True}
                result = asyncio.run(service.export_dialog("live", params))
                with open(result["file_path"], encoding="utf-8-sig", newline="") as stream:
                    self.assertEqual([row["text"] for row in csv.DictReader(stream)], ["still visible"])
                with self.assertRaises(EmptyMessageRange):
                    asyncio.run(service.export_dialog("empty", params))
                self.assertEqual(messages.count(local_id), 2)  # Local archive remains intact.
            finally:
                database.close()

    def test_missing_sender_details_do_not_erase_previously_seen_values(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite")
            try:
                database.migrate()
                accounts = AccountRepository(database)
                accounts.save_profile(AccountProfile(1, None, "User", None, None))
                DialogRepository(database).sync(accounts.local_id(1), [DialogSummary(12, 12, "private", "Chat", None, 0, None, None)])
                local_id = database.fetchone("SELECT id FROM dialogs WHERE telegram_id=12")["id"]
                messages = MessageRepository(database)
                initial = {"message_id": 1, "date_utc": "2026-01-01T00:00:00Z", "text": "hello", "sender_id": 42, "sender_name": "Alice", "sender_username": "alice"}
                messages.upsert_batch(local_id, [initial])
                messages.upsert_batch(local_id, [{**initial, "sender_name": None, "sender_username": ""}])
                messages.upsert_batch(local_id, [{**initial, "sender_id": None}])
                item = messages.export_page(local_id)[0]
                self.assertEqual((item["sender_id"], item["sender_name"], item["sender_username"]), (42, "Alice", "alice"))
            finally:
                database.close()

    def test_microsecond_boundary_does_not_skip_or_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite")
            try:
                database.migrate()
                accounts = AccountRepository(database)
                accounts.save_profile(AccountProfile(1, None, "User", None, None))
                account_id = accounts.local_id(1)
                DialogRepository(database).sync(account_id, [DialogSummary(12, 12, "private", "Chat", None, 0, None, None)])
                local_id = database.fetchone("SELECT id FROM dialogs WHERE telegram_id=12")["id"]
                messages = MessageRepository(database)
                messages.upsert_batch(local_id, [
                    {"message_id": 1, "date_utc": "2026-01-01T00:00:00Z", "text": "first"},
                    {"message_id": 2, "date_utc": "2026-01-01T00:00:01Z", "text": "second"},
                ])
                boundary = "2026-01-01T00:00:00.000100Z"
                before = messages.export_page(local_id, period_end=boundary)
                after = messages.export_page(local_id, period_start=boundary)
                self.assertEqual([item["message_id"] for item in before], [1])
                self.assertEqual([item["message_id"] for item in after], [2])
                self.assertEqual(database.fetchone("SELECT timestamp_us(?) = timestamp_us(?) AS same", ("2026-01-01T03:00:00+03:00", "2026-01-01T00:00:00Z"))["same"], 1)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
