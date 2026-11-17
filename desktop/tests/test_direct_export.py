import asyncio
import importlib.util
import csv
import io
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from signal_archive.direct_export import ChatLookupError, export_chat, message_record
from signal_archive.core import Archive


class PeerLookup:
    async def get_input_entity(self, chat_id):
        return chat_id


class Client(PeerLookup):
    def __init__(self, fail=False):
        self.fail = fail

    async def iter_messages(self, chat_id, **kwargs):
        for number in (2, 3):
            yield types.SimpleNamespace(id=number, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                        message="hello", media="must not be exported")
            if self.fail:
                raise RuntimeError("connection lost")


class DirectExportTests(unittest.TestCase):
    def test_csv_preserves_multiline_unicode_and_escapes_spreadsheet_formulas(self):
        class Messages(PeerLookup):
            async def iter_messages(self, chat_id, **kwargs):
                yield types.SimpleNamespace(id=1, date=None, message='Привет, "мир"\nстрока')
                yield types.SimpleNamespace(id=2, date=None, message=' =HYPERLINK("bad")')
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(export_chat(Messages(), -123, Path(tmp), chat_info={"title": "@unsafe", "type": "group"}))
            self.assertEqual(Path(result["path"]).suffix, ".csv")
            with io.StringIO(Path(result["path"]).read_text(encoding="utf-8-sig"), newline="") as stream:
                rows = list(csv.DictReader(stream))
                rows = [row for row in rows if row.get("record_type") != "project"]
            self.assertEqual(rows[0]["text"], 'Привет, "мир"\nстрока')
            self.assertEqual(rows[1]["text"], '\' =HYPERLINK("bad")')
            self.assertEqual(rows[0]["chat_title"], "'@unsafe")
            self.assertEqual(rows[0]["chat_id"], "-123")

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_recent_export_builds_expected_real_telethon_history_request(self):
        from telethon import TelegramClient
        from telethon.sessions import MemorySession
        from telethon.tl import types
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        async def exercise(root):
            client = TelegramClient(MemorySession(), 12345, "a" * 32)
            peer = types.InputPeerChat(chat_id=123)
            response = types.messages.Messages(messages=[], topics=[], chats=[], users=[])
            with patch.object(client, "get_input_entity", new_callable=AsyncMock, return_value=peer), \
                 patch.object(client, "_call", new_callable=AsyncMock, return_value=response) as call:
                result = await export_chat(client, -123, root, since=start, until=start + timedelta(days=1))
                request = call.await_args.args[1]
                self.assertEqual(request.offset_date, start - timedelta(seconds=1))
                self.assertEqual(request.add_offset, -100)
                self.assertEqual(request.peer, peer)
                self.assertIsNone(result["path"])
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_real_telethon_iterator_exports_messages_in_recent_interval(self):
        from telethon import TelegramClient
        from telethon.sessions import MemorySession
        from telethon.tl import types
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        async def exercise(root):
            client = TelegramClient(MemorySession(), 12345, "a" * 32)
            peer = types.InputPeerChat(chat_id=123)
            messages = [types.Message(id=i, peer_id=types.PeerChat(123), date=date, message=f"m{i}")
                        for i, date in [(4, end + timedelta(seconds=1)), (3, end), (2, start), (1, start - timedelta(seconds=1))]]
            response = types.messages.Messages(messages=messages, topics=[], chats=[], users=[])
            with patch.object(client, "get_input_entity", new_callable=AsyncMock, return_value=peer), \
                 patch.object(client, "_call", new_callable=AsyncMock, return_value=response):
                result = await export_chat(client, -123, root, since=start, until=end)
            rows = list(csv.DictReader(io.StringIO(Path(result["path"]).read_text(encoding="utf-8-sig"), newline="")))
            rows = [row for row in rows if row.get("record_type") != "project"]
            self.assertEqual([int(row["id"]) for row in rows], [2, 3])
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))

    def test_lookup_failure_is_distinct_and_creates_no_file(self):
        class Missing:
            async def get_input_entity(self, chat_id):
                raise ValueError("unknown chat")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ChatLookupError, "unknown chat"):
                asyncio.run(export_chat(Missing(), 123, Path(tmp)))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_cached_sender_details_and_anonymous_signature(self):
        sender = types.SimpleNamespace(first_name="Ada", last_name="Lovelace", username="ada")
        message = types.SimpleNamespace(id=1, message="hello", sender=sender, post_author="Editor", reply_to_msg_id=17)
        record = message_record(message, -123)
        self.assertEqual(record["sender_name"], "Ada Lovelace")
        self.assertEqual(record["sender_username"], "ada")
        self.assertEqual(record["post_author"], "Editor")
        self.assertEqual(record["sender"], "Ada Lovelace")
        self.assertEqual(record["reply_to_message_id"], 17)
        self.assertIsNone(message_record(types.SimpleNamespace(id=2), -123)["sender_name"])

    def test_exported_sender_and_reply_fields_match_existing_importer(self):
        sender = types.SimpleNamespace(first_name="Ada", last_name=None, username="ada")
        record = message_record(types.SimpleNamespace(id=9, message="hello", sender=sender, reply_to_msg_id=4), -123)
        imported = Archive.normalize_record(record, "fallback")
        self.assertEqual(imported["source_id"], "-123")
        self.assertEqual(imported["external_message_id"], "9")
        self.assertEqual(imported["sender"], "Ada")
        self.assertEqual(imported["reply_to"], "4")
        self.assertEqual(imported["media"], [])

    def test_recent_export_filters_exact_message_date_bounds(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        class Messages(PeerLookup):
            async def iter_messages(self, chat_id, **kwargs):
                self.offset = kwargs["offset_date"]
                for number, date in enumerate([start - timedelta(seconds=1), start, end, end + timedelta(seconds=1)], 1):
                    yield types.SimpleNamespace(id=number, date=date, message="text")
        with tempfile.TemporaryDirectory() as tmp:
            client = Messages()
            result = asyncio.run(export_chat(client, 123, Path(tmp), since=start, until=end))
            rows = list(csv.DictReader(io.StringIO(Path(result["path"]).read_text(encoding="utf-8-sig"), newline="")))
            rows = [row for row in rows if row.get("record_type") != "project"]
            self.assertEqual([int(r["id"]) for r in rows], [2, 3])
            self.assertEqual(client.offset, start - timedelta(seconds=1))

    def test_empty_incremental_run_creates_no_file_but_snapshot_can(self):
        class Empty(PeerLookup):
            async def iter_messages(self, chat_id, **kwargs):
                if False:
                    yield None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = asyncio.run(export_chat(Empty(), 123, root, after_id=42))
            self.assertIsNone(result["path"])
            self.assertEqual(result["last_id"], 42)
            self.assertEqual(list(root.iterdir()), [])
            snapshot = asyncio.run(export_chat(Empty(), 123, root, skip_empty=False))
            rows = list(csv.DictReader(io.StringIO(Path(snapshot["path"]).read_text(encoding="utf-8-sig"), newline="")))
            self.assertEqual([row["record_type"] for row in rows], ["project"])
            self.assertIn("Signal Archive", rows[0]["text"])

    def test_reports_progress_for_large_chat(self):
        class ManyMessages(PeerLookup):
            async def iter_messages(self, chat_id, **kwargs):
                for number in range(1, 206):
                    yield types.SimpleNamespace(id=number, date=None, message="text")
        with tempfile.TemporaryDirectory() as tmp:
            progress = []
            result = asyncio.run(export_chat(ManyMessages(), 123, Path(tmp), on_progress=progress.append))
            self.assertEqual(progress, [100, 200])
            self.assertEqual(result["messages"], 205)

    def test_exports_text_and_returns_checkpoint_without_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(export_chat(Client(), -123, Path(tmp), after_id=1, chat_info={"title": "Friends", "type": "group"}))
            records = list(csv.DictReader(io.StringIO(Path(result["path"]).read_text(encoding="utf-8-sig"), newline="")))
            records = [row for row in records if row.get("record_type") != "project"]
            self.assertEqual([int(r["id"]) for r in records], [2, 3])
            self.assertEqual(result["last_id"], 3)
            self.assertNotIn("media", records[0])
            self.assertEqual(records[0]["chat_title"], "Friends")
            self.assertEqual(records[0]["chat_type"], "group")

    def test_interruption_leaves_no_complete_or_temporary_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "connection lost"):
                asyncio.run(export_chat(Client(fail=True), 123, Path(tmp)))
            self.assertEqual(list(Path(tmp).iterdir()), [])
