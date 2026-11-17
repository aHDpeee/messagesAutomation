import asyncio
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


@unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
class MemberExportTests(unittest.TestCase):
    def test_optional_profiles_include_visible_phone_bio_and_channel(self):
        from signal_archive import telegram_ops
        class Group:
            pass
        class Client:
            async def get_entity(self, chat_id):
                return Group()
            async def iter_participants(self, entity, limit):
                yield SimpleNamespace(id=1, first_name="Alice", phone="+123")
                yield SimpleNamespace(id=2, first_name="Bob", phone=None)
            async def __call__(self, request):
                if request.id.id == 2:
                    from telethon import errors
                    raise errors.UserPrivacyRestrictedError(request=None)
                return SimpleNamespace(full_user=SimpleNamespace(about="=unsafe", personal_channel_id=42),
                                       chats=[SimpleNamespace(id=42, title="Channel", username="alice_channel")])
        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_ops, "Chat", Group), \
             patch.object(telegram_ops.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            result = asyncio.run(telegram_ops.export_group_members(Client(), 123, Path(tmp), enrich_profiles=True))
            with Path(result["path"]).open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
                rows = [row for row in rows if row.get("record_type") != "project"]
        self.assertEqual(rows[0]["phone"], "'+123")
        self.assertEqual(rows[0]["bio"], "'=unsafe")
        self.assertEqual(rows[0]["personal_channel_id"], "42")
        self.assertEqual(rows[0]["personal_channel_username"], "alice_channel")
        self.assertEqual(rows[1]["bio"], "")
        self.assertEqual(result["profile_details_attempted"], 2)
        self.assertEqual(result["profile_details_available"], 1)
        sleep.assert_awaited_once_with(telegram_ops.PROFILE_REQUEST_INTERVAL)

    def test_profile_requests_are_capped_and_flood_wait_propagates(self):
        from signal_archive import telegram_ops
        from telethon import errors
        class Group:
            pass
        class Client:
            def __init__(self, flood=False):
                self.requests = 0
                self.flood = flood
            async def get_entity(self, chat_id):
                return Group()
            async def iter_participants(self, entity, limit):
                for number in range(3):
                    yield SimpleNamespace(id=number)
            async def __call__(self, request):
                self.requests += 1
                if self.flood:
                    raise errors.FloodWaitError(request=None, capture=10)
                return SimpleNamespace(full_user=SimpleNamespace(about=None), chats=[])
        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_ops, "Chat", Group), \
             patch.object(telegram_ops, "PROFILE_DETAILS_LIMIT", 2), \
             patch.object(telegram_ops.asyncio, "sleep", new_callable=AsyncMock):
            client = Client()
            result = asyncio.run(telegram_ops.export_group_members(client, 123, Path(tmp), enrich_profiles=True))
            self.assertEqual(result["members_exported"], 3)
            self.assertEqual(client.requests, 2)
            with self.assertRaises(errors.FloodWaitError):
                asyncio.run(telegram_ops.export_group_members(Client(flood=True), 123, Path(tmp), enrich_profiles=True))

    def test_inaccessible_group_is_explained_before_file_creation(self):
        from telethon import errors
        from signal_archive.telegram_ops import export_group_members
        class Client:
            async def get_entity(self, chat_id):
                raise errors.ChannelPrivateError(request=None)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(PermissionError, "private or is not accessible"):
                asyncio.run(export_group_members(Client(), 123, Path(tmp)))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_real_telethon_participant_iterator_writes_csv(self):
        from telethon import TelegramClient
        from telethon.sessions import MemorySession
        from telethon.tl import types
        from signal_archive.telegram_ops import export_group_members
        async def exercise(root):
            client = TelegramClient(MemorySession(), 12345, "a" * 32)
            group = types.Channel(id=123, title="Group", photo=types.ChatPhotoEmpty(), date=None, megagroup=True)
            peer = types.InputPeerChannel(channel_id=123, access_hash=456)
            user = types.User(id=7, first_name="Alice", username="alice")
            participant = types.ChannelParticipant(user_id=7, date=None)
            pages = [types.channels.ChannelParticipants(count=1, participants=[participant], chats=[], users=[user]),
                     types.channels.ChannelParticipants(count=1, participants=[], chats=[], users=[])]
            offsets = []
            async def request(sender, request, **kwargs):
                offsets.append(request.offset)
                return pages.pop(0)
            with patch.object(client, "get_entity", new_callable=AsyncMock, return_value=group), \
                 patch.object(client, "get_input_entity", new_callable=AsyncMock, return_value=peer), \
                 patch.object(client, "_call", side_effect=request), \
                 patch("telethon.requestiter.asyncio.sleep", new_callable=AsyncMock):
                result = await export_group_members(client, -1000000000123, root)
            with Path(result["path"]).open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
                rows = [row for row in rows if row.get("record_type") != "project"]
            self.assertEqual([row["username"] for row in rows], ["alice"])
            self.assertEqual(offsets, [0, 1])
            self.assertFalse(result["complete"])
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))

    def test_member_export_reports_progress(self):
        from signal_archive import telegram_ops
        class Group:
            pass
        class Client:
            async def get_entity(self, chat_id):
                return Group()
            async def iter_participants(self, entity, limit):
                for number in range(205):
                    yield SimpleNamespace(id=number, first_name="Test")
        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_ops, "Chat", Group):
            progress = []
            result = asyncio.run(telegram_ops.export_group_members(Client(), 123, Path(tmp), on_progress=progress.append))
            self.assertEqual(progress, [100, 200])
            self.assertEqual(result["members_exported"], 205)

    def test_telethon_pagination_is_paced(self):
        from signal_archive.telegram_ops import pace_iterator
        iterator = SimpleNamespace(wait_time=None)
        self.assertIs(pace_iterator(iterator), iterator)
        self.assertEqual(iterator.wait_time, 3)

    def test_spreadsheet_formula_names_are_escaped(self):
        from signal_archive import telegram_ops
        class Group:
            pass
        class Client:
            async def get_entity(self, chat_id):
                return Group()
            async def iter_participants(self, entity, limit):
                yield SimpleNamespace(id=1, first_name="=1+1", last_name="  @command", username="normal")
        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_ops, "Chat", Group):
            result = asyncio.run(telegram_ops.export_group_members(Client(), 123, Path(tmp)))
            with Path(result["path"]).open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["first_name"], "'=1+1")
            self.assertEqual(row["last_name"], "'  @command")
            self.assertEqual(row["username"], "normal")

    def test_file_write_failure_removes_partial_csv(self):
        from signal_archive import telegram_ops
        class Group:
            pass
        class Client:
            async def get_entity(self, chat_id):
                return Group()
            async def iter_participants(self, entity, limit):
                yield SimpleNamespace(id=1, first_name="Test")
        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_ops, "Chat", Group), patch.object(telegram_ops.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                asyncio.run(telegram_ops.export_group_members(Client(), 123, Path(tmp)))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_cancel_removes_partial_csv(self):
        from signal_archive import telegram_ops
        class Group:
            pass
        class Client:
            async def get_entity(self, chat_id):
                return Group()
            async def iter_participants(self, entity, limit):
                yield SimpleNamespace(id=1, first_name="Test")
                raise asyncio.CancelledError()
        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_ops, "Chat", Group):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(telegram_ops.export_group_members(Client(), 123, Path(tmp)))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_all_visible_members_are_requested_and_exported(self):
        from signal_archive import telegram_ops

        class Group:
            pass

        class Client:
            async def get_entity(self, chat_id):
                return Group()

            async def iter_participants(self, entity, limit):
                self.requested_limit = limit
                for i in range(2):
                    yield SimpleNamespace(id=i, username=f"user{i}", first_name="Test")

        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_ops, "Chat", Group):
            client = Client()
            result = asyncio.run(telegram_ops.export_group_members(client, 123, Path(tmp)))
            with Path(result["path"]).open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
                rows = [row for row in rows if row.get("record_type") != "project"]
            self.assertIsNone(client.requested_limit)
            self.assertEqual([row["username"] for row in rows], ["user0", "user1"])
            self.assertFalse(result["complete"])
