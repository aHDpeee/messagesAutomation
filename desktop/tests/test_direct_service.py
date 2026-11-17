import asyncio
import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from signal_archive.direct_service import DirectService, PartialExportError, selection_fingerprint
from signal_archive.direct_export import ChatLookupError


@unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
class DirectServiceTests(unittest.TestCase):
    def test_default_export_bounds_start_and_remembers_selection(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            service.state["dialogs"] = [{"id": "1"}]
            service.select(["1"])
            times = [datetime(2026, 1, 4, tzinfo=timezone.utc), datetime(2026, 1, 5, tzinfo=timezone.utc)]
            calls = []
            async def fake_export(client, chat_id, out_dir, **kwargs):
                calls.append((kwargs["since"], kwargs["until"], kwargs["after_id"]))
                return {"last_id": 0, "path": None, "messages": 0}
            with patch("signal_archive.direct_service._now", side_effect=lambda: times[0]), patch("signal_archive.direct_service.export_chat", fake_export):
                await service.export_selected()
                times.pop(0)
                await service.export_selected()
            self.assertEqual(calls[0], (datetime(2026, 1, 3, tzinfo=timezone.utc), datetime(2026, 1, 4, tzinfo=timezone.utc), 0))
            self.assertEqual(calls[1], (datetime(2026, 1, 4, tzinfo=timezone.utc), datetime(2026, 1, 5, tzinfo=timezone.utc), 0))
            self.assertEqual(service.state["checkpoints"], {})
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_add_chat_selects_it_and_preserves_last_activity(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            service.state["dialogs"] = [{"id": "1", "title": "Old", "last_message_date": "2026-01-02T00:00:00+00:00"}]
            record = {"id": "1", "title": "Chat", "type": "chat", "last_message_date": ""}
            join = {"status": "added", "message": "Чат добавлен.", "problem": False}
            with patch("signal_archive.chat_add.resolve_and_join", new_callable=AsyncMock, return_value=(record, "username", join)):
                await service.add_chat("@example")
                await service.add_chat("@example")
            restored = DirectService(None, root, root / "exports")
            self.assertEqual(restored.state["selected"], ["1"])
            self.assertEqual(restored.state["dialogs"][0]["last_message_date"], "2026-01-02T00:00:00+00:00")
            self.assertIn("yes", (root / "chats.csv").read_text(encoding="utf-8-sig"))
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_add_chat_records_join_problem_and_clears_it_on_success(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            service.state["dialogs"] = []
            pending = {"status": "requested", "message": "Ожидает одобрения.", "problem": True}
            with patch("signal_archive.chat_add.resolve_and_join",
                       new_callable=AsyncMock, return_value=(None, "invite", pending)) as resolve:
                result = await service.add_chat("https://t.me/+abc")
            resolve.assert_awaited_once()
            self.assertTrue(result["problem"])
            self.assertEqual(result["status"], "requested")
            self.assertEqual(result["title"], "https://t.me/+abc")
            self.assertEqual(service.state["problem_chats"]["https://t.me/+abc"]["reason"], "requested")
            self.assertEqual(service.state["selected"], [])

            record = {"id": "-1001", "title": "Approved group", "type": "group", "last_message_date": ""}
            joined = {"status": "joined", "message": "Вы вступили.", "problem": False}
            with patch("signal_archive.chat_add.resolve_and_join",
                       new_callable=AsyncMock, return_value=(record, "invite", joined)):
                result = await service.add_chat("https://t.me/+abc")
            self.assertFalse(result["problem"])
            self.assertEqual(service.state["selected"], ["-1001"])
            self.assertFalse(service.state["problem_chats"])
            self.assertIn("-1001", [str(item["id"]) for item in service.state["dialogs"]])
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_date_export_remembers_success_for_selection_without_changing_checkpoints(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            service.state["dialogs"] = [{"id": "1"}]
            service.select(["1"])
            service.state["checkpoints"] = {"1": 12}
            start = datetime.now(timezone.utc) - timedelta(days=1)
            end = datetime.now(timezone.utc)
            async def fake_export(client, chat_id, out_dir, **kwargs):
                self.assertEqual(kwargs["since"], start)
                self.assertEqual(kwargs["until"], end)
                return {"last_id": 15, "path": None, "messages": 0}
            with patch("signal_archive.direct_service.export_chat", fake_export):
                await service.export_selected(start=start, end=end)
            self.assertEqual(service.state["checkpoints"], {"1": 12})
            restored = DirectService(None, root, root / "exports")
            self.assertIn(selection_fingerprint(["1"]), restored.state["last_selection_exports"])
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_export_rejects_selection_changed_in_another_tab(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            service.state["dialogs"] = [{"id": "1"}, {"id": "2"}]
            service.select(["1"])
            page_selection = selection_fingerprint(service.state["selected"])
            service.select(["2"])
            with patch("signal_archive.direct_service.export_chat", new_callable=AsyncMock) as export:
                with self.assertRaisesRegex(ValueError, "selection changed"):
                    await service.export_selected(expected_selection=page_selection)
                export.assert_not_awaited()
            self.assertEqual(service.state["job"]["status"], "failed")
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_text_encoding_failure_stops_run_instead_of_claiming_chat_is_inaccessible(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            service.state["dialogs"] = [{"id": "1"}, {"id": "2"}]
            service.select(["1", "2"])
            calls = []
            async def fake_export(client, chat_id, out_dir, **kwargs):
                calls.append(chat_id)
                raise UnicodeEncodeError("utf-8", "x", 0, 1, "invalid")
            with patch("signal_archive.direct_service.export_chat", fake_export):
                with self.assertRaises(UnicodeEncodeError):
                    await service.export_selected(incremental=True)
            self.assertEqual(calls, [1])
            self.assertEqual(service.state["job"]["status"], "failed")
            self.assertNotIn("failures", service.state["job"])
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_inaccessible_chat_does_not_block_other_selected_chats(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            service.state["dialogs"] = [{"id": "1", "title": "Unavailable"}, {"id": "2", "title": "Available"}]
            service.select(["1", "2"])
            calls = []
            async def fake_export(client, chat_id, out_dir, **kwargs):
                calls.append(chat_id)
                if chat_id == 1:
                    raise ChatLookupError("Could not find this chat")
                file = out_dir / "completed.jsonl"
                file.write_text("{}")
                return {"last_id": 10, "path": str(file), "messages": 3}
            with patch("signal_archive.direct_service.export_chat", fake_export), patch("signal_archive.direct_service.asyncio.sleep", new_callable=AsyncMock) as sleep:
                with self.assertRaises(PartialExportError):
                    await service.export_selected(incremental=True)
                sleep.assert_awaited_once_with(3)
            restored = DirectService(None, root, root / "exports")
            self.assertEqual(calls, [1, 2])
            self.assertEqual(restored.state["checkpoints"], {"2": 10})
            self.assertEqual(restored.state["job"]["status"], "partial")
            self.assertEqual(restored.state["job"]["failures"][0]["chat_id"], "1")
            self.assertTrue(restored.state["job"]["results"][0]["path"].endswith(".zip"))
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_recent_export_does_not_change_incremental_checkpoint(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            service.state["dialogs"] = [{"id": "1"}]
            service.select(["1"])
            service.state["checkpoints"] = {"1": 10}
            calls = []
            async def fake_export(client, chat_id, out_dir, **kwargs):
                calls.append(kwargs)
                file = out_dir / "recent.jsonl"
                file.write_text("{}")
                return {"last_id": 25, "path": str(file)}
            with patch("signal_archive.direct_service.export_chat", fake_export):
                await service.export_selected(days=7)
            self.assertEqual(service.state["checkpoints"], {"1": 10})
            self.assertEqual(calls[0]["after_id"], 0)
            self.assertEqual((calls[0]["until"] - calls[0]["since"]).days, 7)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_account_binding_rejects_different_or_unverified_checkpoint_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DirectService(None, root, root / "exports")
            service.bind_account(42)
            self.assertEqual(DirectService(None, root, root / "exports").state["account_id"], 42)
            with self.assertRaisesRegex(RuntimeError, "different Telegram account"):
                service.bind_account(43)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DirectService(None, root, root / "exports")
            service.state["checkpoints"] = {"12": 100}
            with self.assertRaisesRegex(RuntimeError, "no verified Telegram account"):
                service.bind_account(42)

    def test_snapshot_reads_from_start_without_advancing_checkpoints(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            service.state["dialogs"] = [{"id": "1"}]
            service.select(["1"])
            service.state["checkpoints"] = {"1": 10}
            calls = []
            async def fake_export(client, chat_id, out_dir, *, after_id, chat_info, on_progress, skip_empty, since, until, limit):
                calls.append(after_id)
                file = out_dir / "snapshot.jsonl"
                file.write_text("{}")
                return {"last_id": 25, "path": str(file)}
            with patch("signal_archive.direct_service.export_chat", fake_export):
                await service.export_selected(snapshot=True)
            self.assertEqual(calls, [0])
            self.assertEqual(service.state["checkpoints"], {"1": 10})
            self.assertEqual(service.state["last_export_snapshot"]["status"], "success")
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_cancellation_records_interrupted_job(self):
        async def run(root):
            service = DirectService(None, root, root / "exports")
            async def cancel():
                raise asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await service._run("test", cancel)
            self.assertEqual(DirectService(None, root, root / "exports").state["job"]["status"], "interrupted")
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_checkpoints_only_completed_chats_and_resumes(self):
        async def run(root):
            service = DirectService(None, root / "state", root / "exports")
            service.state["dialogs"] = [{"id": "1"}, {"id": "2"}]
            service.select(["1", "2"])
            calls = []

            async def fake_export(client, chat_id, out_dir, *, after_id, chat_info, on_progress, skip_empty, since, until, limit):
                calls.append((chat_id, after_id))
                if chat_id == 2:
                    raise RuntimeError("offline")
                file = out_dir / "done.jsonl"
                file.write_text("{}")
                return {"last_id": 42, "path": str(file)}

            with patch("signal_archive.direct_service.export_chat", fake_export), patch("signal_archive.direct_service.asyncio.sleep", new_callable=AsyncMock):
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    await service.export_selected(incremental=True)
            restored = DirectService(None, root / "state", root / "exports")
            self.assertEqual(restored.state["checkpoints"], {"1": 42})
            self.assertEqual(restored.state["job"]["status"], "failed")
            self.assertEqual(restored.state["job"]["completed"], 1)
            self.assertTrue(restored.state["job"]["results"][0]["path"].endswith(".zip"))
            self.assertEqual(calls, [(1, 0), (2, 0)])

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_flood_wait_is_persisted_and_blocks_following_jobs(self):
        from telethon.errors import FloodWaitError, FloodPremiumWaitError, SlowModeWaitError, FloodTestPhoneWaitError

        async def run(root, error_type):
            service = DirectService(None, root, root / "exports")

            async def flood():
                raise error_type(request=None, capture=120)

            with self.assertRaisesRegex(RuntimeError, "pause until"):
                await service._run("test", flood)
            restored = DirectService(None, root, root / "exports")
            with self.assertRaisesRegex(RuntimeError, "pause until"):
                await restored._run("test", flood)
            self.assertEqual(restored.state["job"]["status"], "waiting")

        for error_type in (FloodWaitError, FloodPremiumWaitError, SlowModeWaitError, FloodTestPhoneWaitError):
            with self.subTest(error=error_type.__name__), tempfile.TemporaryDirectory() as tmp:
                asyncio.run(run(Path(tmp), error_type))
