"""Cross-component workflow without accessing a Telegram account."""
import asyncio
import csv
import importlib.util
import json
import tempfile
import unittest
import zipfile
import io
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from signal_archive.direct_service import DirectService


@unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
class DirectWorkflowTests(unittest.TestCase):
    def setUp(self):
        music = patch("signal_archive.completion_music.play_on_completion")
        music.start()
        self.addCleanup(music.stop)

    def test_password_reaches_all_bundle_entries_without_being_saved(self):
        class Client:
            async def get_input_entity(self, chat_id):
                return chat_id
            async def iter_messages(self, chat_id, **kwargs):
                yield SimpleNamespace(id=1, date=None, message="hello")
        async def exercise(root):
            app = DirectService(Client(), root / "state", root / "exports")
            app.state["dialogs"] = [{"id": "1", "title": "A"}]
            app.select(["1"])
            calls = []
            def encrypted(bundle, source, password, *, arcname=None):
                calls.append((source.name, password))
                bundle.write_bytes(b"mock encrypted zip")
            with patch("signal_archive.encrypted_bundle.add_encrypted_file", encrypted):
                await app.export_selected(password="test-secret")
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(password == "test-secret" for _, password in calls))
            self.assertNotIn("test-secret", app.state_path.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))

    def test_crash_after_file_finalization_does_not_skip_uncheckpointed_messages(self):
        class Client:
            async def get_input_entity(self, chat_id):
                return chat_id
            async def iter_messages(self, chat_id, *, min_id, reverse, wait_time, limit=None):
                if min_id < 1:
                    yield SimpleNamespace(id=1, date=None, message="test")
        class SimulatedCrash(BaseException):
            pass
        async def exercise(root):
            client = Client()
            service = DirectService(client, root / "state", root / "exports")
            service.bind_account(99)
            service.state["dialogs"] = [{"id": "1", "title": "Example"}]
            service.select(["1"])
            save = service.save
            def crash_before_checkpoint_write():
                if service.state["checkpoints"].get("1") == 1:
                    raise SimulatedCrash()
                save()
            service.save = crash_before_checkpoint_write
            with self.assertRaises(SimulatedCrash):
                await service.export_selected()
            self.assertEqual(len(list((root / "exports").glob("*.zip"))), 1)
            restored = DirectService(client, root / "state", root / "exports")
            self.assertEqual(restored.state["job"]["status"], "interrupted")
            self.assertNotIn("1", restored.state["checkpoints"])
            await restored.export_selected()
            files = list((root / "exports").glob("*.zip"))
            self.assertEqual(len(files), 2)  # Duplicate is preferable to a skipped message.
            self.assertEqual([[row["id"] for row in csv.DictReader(io.StringIO(zipfile.ZipFile(p).read(zipfile.ZipFile(p).namelist()[0]).decode("utf-8-sig"))) if row.get("record_type") != "project"] for p in files], [["1"], ["1"]])
            self.assertEqual(restored.state["checkpoints"], {"1": 1})
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))

    def test_failed_zip_publication_preserves_existing_archive_and_checkpoint(self):
        class Client:
            async def get_input_entity(self, chat_id):
                return chat_id
            async def iter_messages(self, chat_id, *, min_id, reverse, wait_time, limit=None):
                yield SimpleNamespace(id=1, date=None, message=f"chat {chat_id}")
        async def exercise(root):
            from signal_archive import direct_service
            app = DirectService(Client(), root / "state", root / "exports")
            app.state["dialogs"] = [{"id": "1"}, {"id": "2"}]
            app.select(["1", "2"])
            real = direct_service._add_to_bundle
            def fail_second(bundle, source, *, password=None):
                if bundle.exists():
                    raise OSError("disk full")
                real(bundle, source)
            with patch.object(direct_service, "_add_to_bundle", fail_second):
                with self.assertRaisesRegex(OSError, "disk full"):
                    await app.export_selected()
            self.assertEqual(app.state["checkpoints"], {"1": 1})
            bundles = list((root / "exports").iterdir())
            self.assertEqual(len(bundles), 1)
            with zipfile.ZipFile(bundles[0]) as archive:
                self.assertEqual(len(archive.namelist()), 1)
                self.assertEqual(next(csv.DictReader(io.StringIO(archive.read(archive.namelist()[0]).decode("utf-8-sig"))))["chat_id"], "1")
        with tempfile.TemporaryDirectory() as tmp, patch("signal_archive.direct_service.asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(exercise(Path(tmp)))

    def test_large_chat_resumes_from_completed_segment_after_flood_wait(self):
        from telethon.errors import FloodWaitError
        class Client:
            def __init__(self):
                self.flood = True
                self.requests = []
            async def get_input_entity(self, chat_id):
                return chat_id
            async def iter_messages(self, chat_id, *, min_id, reverse, wait_time, limit=None):
                self.requests.append((min_id, limit))
                for ident in range(min_id + 1, min(1001, min_id + limit) + 1):
                    yield SimpleNamespace(id=ident, date=None, message=f"message {ident}")
                    if self.flood and min_id == 1000:
                        raise FloodWaitError(request=None, capture=120)
        async def exercise(root):
            client = Client()
            app = DirectService(client, root / "state", root / "exports")
            app.bind_account(99)
            app.state["dialogs"] = [{"id": "1", "title": "Long chat"}]
            app.select(["1"])
            with self.assertRaisesRegex(RuntimeError, "pause until"):
                await app.export_selected()
            self.assertEqual(app.state["checkpoints"], {"1": 1000})
            self.assertEqual(app.state["job"]["completed"], 0)
            files = list((root / "exports").glob("*.zip"))
            self.assertEqual(len(files), 1)
            self.assertEqual(len(zipfile.ZipFile(files[0]).read(zipfile.ZipFile(files[0]).namelist()[0]).decode("utf-8-sig").splitlines()), 1002)
            self.assertFalse(list((root / "exports").glob("*.part")))
            app = DirectService(client, root / "state", root / "exports")
            app.state["cooldown_until"] = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
            app.save()
            client.flood = False
            results = await app.export_selected()
            self.assertEqual(client.requests, [(0, 1000), (1000, 1000), (1000, 1000)])
            self.assertEqual(app.state["checkpoints"], {"1": 1001})
            self.assertEqual(app.state["job"]["completed"], 1)
            self.assertEqual([row["id"] for row in csv.DictReader(io.StringIO(zipfile.ZipFile(results[0]["path"]).read(results[0]["file"]).decode("utf-8-sig"))) if row.get("record_type") != "project"], ["1001"])
        with tempfile.TemporaryDirectory() as tmp, patch("signal_archive.direct_service.asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(exercise(Path(tmp)))

    def test_flood_wait_stops_run_and_resume_keeps_completed_checkpoint(self):
        from telethon.errors import FloodWaitError
        class Client:
            def __init__(self):
                self.flood = True
                self.requests = []
            async def get_input_entity(self, chat_id):
                return chat_id
            async def iter_messages(self, chat_id, *, min_id, reverse, wait_time, limit=None):
                self.requests.append((chat_id, min_id))
                if min_id < 1:
                    yield SimpleNamespace(id=1, date=datetime(2026, 1, 1, tzinfo=timezone.utc), message=f"chat {chat_id}")
                if chat_id == 2 and self.flood:
                    raise FloodWaitError(request=None, capture=120)

        async def exercise(root):
            client = Client()
            app = DirectService(client, root / "state", root / "exports")
            app.bind_account(99)
            app.state["dialogs"] = [{"id": str(i), "title": str(i)} for i in (1, 2, 3)]
            app.select(["1", "2", "3"])
            with self.assertRaisesRegex(RuntimeError, "pause until"):
                await app.export_selected()
            self.assertEqual(client.requests, [(1, 0), (2, 0)])
            self.assertEqual(app.state["checkpoints"], {"1": 1})
            self.assertEqual(len(list((root / "exports").glob("*.zip"))), 1)
            self.assertFalse(list((root / "exports").glob("*.part")))
            app = DirectService(client, root / "state", root / "exports")
            with self.assertRaisesRegex(RuntimeError, "pause until"):
                await app.export_selected()
            self.assertEqual(client.requests, [(1, 0), (2, 0)])
            # Simulate the passage of the server-requested cooldown.
            app.state["cooldown_until"] = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
            app.save()
            client.flood = False
            results = await app.export_selected()
            self.assertEqual(client.requests[2:], [(1, 1), (2, 0), (3, 0)])
            self.assertIsNone(results[0]["path"])
            self.assertEqual(app.state["checkpoints"], {"1": 1, "2": 1, "3": 1})
            self.assertEqual(len(list((root / "exports").glob("*.zip"))), 2)

        with tempfile.TemporaryDirectory() as tmp, patch("signal_archive.direct_service.asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(exercise(Path(tmp)))

    def test_discover_select_export_restart_incremental_and_members(self):
        from signal_archive import telegram_ops
        class Group:
            title = "Family"
            username = None
        class User:
            bot = True
            first_name = "Helper"
            last_name = None
            username = "helper"
        class Client:
            def __init__(self):
                self.messages = {-123: [1, 2], 456: [5]}
            async def get_input_entity(self, chat_id):
                return chat_id
            async def iter_dialogs(self):
                for ident, entity in [(-123, Group()), (456, User())]:
                    yield SimpleNamespace(id=ident, entity=entity, date=None)
            async def iter_messages(self, chat_id, *, min_id, reverse, wait_time, limit=None):
                assert reverse and wait_time == 3
                for ident in self.messages[chat_id]:
                    if ident > min_id:
                        yield SimpleNamespace(id=ident, date=datetime(2026, 1, 1, tzinfo=timezone.utc), message=f"message {ident}", media="not exported")
            async def get_entity(self, chat_id):
                assert chat_id == -123
                return Group()
            async def iter_participants(self, entity, limit):
                assert limit is None
                yield SimpleNamespace(id=7, first_name="Alice", username="alice")

        async def exercise(root):
            client = Client()
            app = DirectService(client, root / "state", root / "exports")
            app.bind_account(99)
            dialogs = await app.refresh_dialogs()
            self.assertEqual([d["type"] for d in dialogs], ["group", "bot"])
            app.select(["-123", "456"])
            initial = await app.export_selected()
            self.assertEqual(initial[0]["path"], initial[1]["path"])
            with zipfile.ZipFile(initial[0]["path"]) as archive:
                self.assertEqual(set(archive.namelist()), {initial[0]["file"], initial[1]["file"], "chats.txt"})
                self.assertIsNone(archive.testzip())
            self.assertEqual([p.suffix for p in (root / "exports").iterdir()], [".zip"])
            def records(result):
                return [row for row in csv.DictReader(io.StringIO(zipfile.ZipFile(result["path"]).read(result["file"]).decode("utf-8-sig"))) if row.get("record_type") != "project"]
            self.assertEqual([row["id"] for row in records(initial[0])], ["1", "2"])
            self.assertEqual(records(initial[0])[0]["chat_title"], "Family")
            self.assertNotIn("media", records(initial[0])[0])
            client.messages[-123].append(3)
            app = DirectService(client, root / "state", root / "exports")
            app.bind_account(99)
            incremental = await app.export_selected()
            self.assertEqual([row["id"] for row in records(incremental[0])], ["3"])
            self.assertIsNone(incremental[1]["path"])
            self.assertEqual(incremental[1]["messages"], 0)
            self.assertEqual(app.state["checkpoints"], {"-123": 3, "456": 5})
            members = await app.export_members("-123")
            with io.StringIO(zipfile.ZipFile(members["path"]).read(members["file"]).decode("utf-8-sig"), newline="") as stream:
                self.assertEqual(next(csv.DictReader(stream))["username"], "alice")
            self.assertFalse(members["complete"])

        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_ops, "Chat", Group), patch.object(telegram_ops, "User", User), patch("signal_archive.direct_service.asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(exercise(Path(tmp)))
