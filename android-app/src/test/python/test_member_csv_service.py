import asyncio
import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tg_observatory.services.member_csv_service import export_csv
from tg_observatory.exceptions import InvalidRequest


class Accounts:
    def save_profile(self, profile): pass
    def local_id(self, telegram_id): return 1


class Database:
    def fetchone(self, sql, args):
        return {"kind": "group", "title": "Group"} if args == (1, -12) else None


class Gateway:
    async def account_status(self, api_id, api_hash): return SimpleNamespace(telegram_id=1)
    async def iter_member_profiles(self, api_id, api_hash, dialog_id, include_profiles):
        assert include_profiles is False
        yield {"telegram_id": 7, "fields": {"first_name": {"state": "AVAILABLE", "value": "=malicious"}, "phone": {"state": "NOT_EXPOSED", "value": None}}}


class MembersCsvTests(unittest.TestCase):
    def params(self):
        return {"api_id": 1, "api_hash": "a" * 32, "expected_account_id": "1", "groups": [{"id": -12, "block": "Work"}]}

    def test_csv_contains_group_and_only_visible_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(export_csv(Gateway(), Database(), Accounts(), Path(tmp), "job123", self.params(), lambda _: None))
            self.assertEqual(result["members_exported"], 1)
            self.assertEqual(result["completeness"], "partial")
            with open(result["file_path"], encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["first_name"], "'=malicious")
            self.assertEqual(rows[0]["phone"], "")
            self.assertEqual(rows[0]["block group / plan"], "Work")
            self.assertEqual(rows[-1]["record_type"], "project")

    def test_interrupted_job_removes_partial_file(self):
        class Broken(Gateway):
            async def iter_member_profiles(self, *args, **kwargs):
                yield {"telegram_id": 7, "fields": {}}
                raise RuntimeError("network error")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "network error"):
                asyncio.run(export_csv(Broken(), Database(), Accounts(), Path(tmp), "job123", self.params(), lambda _: None))
            self.assertEqual(list(Path(tmp).rglob("*.csv")), [])
            self.assertEqual(list(Path(tmp).rglob("*.part")), [])

    def test_plan_is_combined_with_block_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            params = {**self.params(), "plan": "  Outreach  "}
            result = asyncio.run(export_csv(Gateway(), Database(), Accounts(), Path(tmp), "job123", params, lambda _: None))
            with open(result["file_path"], encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(next(csv.DictReader(stream))["block group / plan"], "Work / Outreach")
            params["plan"] = "x" * 201
            with self.assertRaises(InvalidRequest):
                asyncio.run(export_csv(Gateway(), Database(), Accounts(), Path(tmp), "job124", params, lambda _: None))

    def test_optional_profile_requests_are_capped_and_paced(self):
        seen = []
        class Enriched(Gateway):
            async def iter_member_profiles(self, *args, **kwargs):
                seen.append(kwargs)
                yield {"telegram_id": 7, "fields": {"bio": {"state": "AVAILABLE", "value": "hello"}, "personal_channel": {"state": "AVAILABLE", "value": {"telegram_channel_id": 42, "title": "News", "username": "news"}}}}
        with tempfile.TemporaryDirectory() as tmp:
            params = {**self.params(), "include_profiles": True}
            result = asyncio.run(export_csv(Enriched(), Database(), Accounts(), Path(tmp), "job123", params, lambda _: None))
            self.assertEqual(seen, [{"include_profiles": True, "max_profile_details": 100, "profile_delay": 3.0}])
            with open(result["file_path"], encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual((row["bio"], row["personal_channel_id"], row["personal_channel_username"]), ("hello", "42", "news"))
            self.assertEqual((row["bio_status"], row["personal_channel_status"], row["personal_channel_link"]), ("AVAILABLE", "AVAILABLE", "https://t.me/news"))

    def test_flood_wait_is_propagated_without_publishing_partial_csv(self):
        from telethon.errors import FloodWaitError
        class Limited(Gateway):
            async def iter_member_profiles(self, *args, **kwargs):
                yield {"telegram_id": 7, "fields": {}}
                raise FloodWaitError(request=None, capture=37)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FloodWaitError) as caught:
                asyncio.run(export_csv(Limited(), Database(), Accounts(), Path(tmp), "job123", self.params(), lambda _: None))
            self.assertEqual(caught.exception.seconds, 37)
            self.assertEqual(list(Path(tmp).rglob("*.csv")), [])

    def test_inaccessible_group_does_not_discard_other_groups(self):
        class TwoGroups(Database):
            def fetchone(self, sql, args): return {"kind": "group", "title": "Group"}
        class OneInaccessible(Gateway):
            async def iter_member_profiles(self, api_id, api_hash, ident, **kwargs):
                if ident == -13:
                    yield {"telegram_id": 99, "fields": {}}
                    raise PermissionError("private detail")
                yield {"telegram_id": 7, "fields": {}}
        with tempfile.TemporaryDirectory() as tmp, patch("tg_observatory.services.member_csv_service.asyncio.sleep", new=AsyncMock()):
            params = {**self.params(), "groups": [{"id": -12, "block": "Work"}, {"id": -13, "block": "Other"}]}
            result = asyncio.run(export_csv(OneInaccessible(), TwoGroups(), Accounts(), Path(tmp), "job123", params, lambda _: None))
            self.assertEqual(result["completed_group_ids"], [-12])
            self.assertEqual(result["failed_groups"], 1)
            self.assertEqual(result["failed_group_ids"], [-13])
            with open(result["file_path"], encoding="utf-8-sig", newline="") as stream:
                self.assertEqual([row["user_id"] for row in csv.DictReader(stream) if row["record_type"] == "member"], ["7"])
            self.assertNotIn("private detail", str(result))

    def test_all_inaccessible_groups_report_specific_error_without_publishing(self):
        class Inaccessible(Gateway):
            async def iter_member_profiles(self, *args, **kwargs):
                yield {"telegram_id": 99, "fields": {}}
                raise PermissionError("private detail")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(InvalidRequest) as caught:
                asyncio.run(export_csv(Inaccessible(), Database(), Accounts(), Path(tmp), "job123", self.params(), lambda _: None))
            self.assertEqual(caught.exception.code, "members_unavailable")
            self.assertEqual(list(Path(tmp).rglob("*.csv")), [])
            self.assertEqual(list(Path(tmp).rglob("*.part")), [])

    def test_gateway_limits_and_paces_profile_requests(self):
        from tg_observatory.telegram.client import TelegramGateway
        class Client:
            def __init__(self): self.calls = 0
            async def iter_participants(self, peer):
                for ident in (1, 2, 3):
                    yield SimpleNamespace(id=ident, username=None)
            async def __call__(self, request):
                self.calls += 1
                return SimpleNamespace(full_user=SimpleNamespace(about="profile"), users=[], chats=[])
        async def run():
            gateway = TelegramGateway("unused")
            client = Client()
            gateway._connected_client = AsyncMock(return_value=client)
            gateway._input_peer = AsyncMock(return_value=object())
            with patch("tg_observatory.telegram.client._user_authorized", new=AsyncMock(return_value=True)), \
                 patch("tg_observatory.telegram.client.asyncio.sleep", new=AsyncMock()) as sleep:
                result = [item async for item in gateway.iter_member_profiles(1, "a" * 32, -12, include_profiles=True, max_profile_details=2, profile_delay=3)]
            self.assertEqual(client.calls, 2)
            self.assertEqual(result[2]["fields"]["bio"]["state"], "NOT_FETCHED")
            self.assertIn(3, [call.args[0] for call in sleep.await_args_list])
            client.calls = 0
            with patch("tg_observatory.telegram.client._user_authorized", new=AsyncMock(return_value=True)):
                plain = [item async for item in gateway.iter_member_profiles(1, "a" * 32, -12, include_profiles=False)]
            self.assertEqual(client.calls, 0)
            self.assertEqual(len(plain), 3)
        asyncio.run(run())

    def test_gateway_does_not_hide_rate_limits_or_network_failures(self):
        from telethon import errors
        from tg_observatory.telegram.client import TelegramGateway
        class Client:
            def __init__(self, error): self.error = error
            async def iter_participants(self, peer):
                yield SimpleNamespace(id=1, username=None)
            async def __call__(self, request): raise self.error
        async def run(error):
            gateway = TelegramGateway("unused")
            gateway._connected_client = AsyncMock(return_value=Client(error))
            gateway._input_peer = AsyncMock(return_value=object())
            with patch("tg_observatory.telegram.client._user_authorized", new=AsyncMock(return_value=True)):
                return [item async for item in gateway.iter_member_profiles(1, "a" * 32, -12)]
        for error in (errors.FloodPremiumWaitError(request=None, capture=42), ConnectionError("offline")):
            with self.assertRaises(type(error)):
                asyncio.run(run(error))
        result = asyncio.run(run(errors.UserPrivacyRestrictedError(request=None)))
        self.assertEqual(result[0]["fields"]["bio"]["state"], "RETRIEVAL_FAILED")


if __name__ == "__main__":
    unittest.main()
