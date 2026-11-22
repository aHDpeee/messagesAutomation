import asyncio
import tempfile
import unittest
from pathlib import Path

from tg_observatory.models.account import AccountProfile
from tg_observatory.models.dialog import DialogSummary
from tg_observatory.services.dialog_service import DialogService
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.dialogs import DialogRepository


class SyncIdsTests(unittest.TestCase):
    def test_sync_reports_only_dialogs_seen_this_time(self):
        profile = AccountProfile(123, None, "User", None, None)
        class Gateway:
            async def account_status(self, *_): return profile
            async def iter_dialogs(self, *_, **__):
                yield DialogSummary(-1002, 2, "channel", "Current", None, 0, None, None)
                yield DialogSummary(50, 50, "bot", "Bot", None, 0, None, "2026-01-01T00:00:00Z", True, True)
                yield DialogSummary(51, 51, "private", "Empty", None, 0, None, None, False, None)
        with tempfile.TemporaryDirectory() as path:
            database = Database(Path(path) / "db.sqlite")
            try:
                database.migrate()
                accounts = AccountRepository(database)
                accounts.save_profile(profile)
                dialogs = DialogRepository(database)
                dialogs.sync(accounts.local_id(123), [DialogSummary(-1001, 1, "channel", "Old", None, 0, None, None)])
                service = DialogService(Gateway(), accounts, dialogs)
                result = asyncio.run(service.sync({"api_id": 123, "api_hash": "a" * 32}))
                self.assertEqual(result["synced_ids"], [-1002, 50, 51])
                self.assertEqual(result["unanswered_bot_ids"], [50])
                self.assertEqual(result["empty_ids"], [51])
                self.assertEqual(len(dialogs.list(accounts.local_id(123))), 4)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
