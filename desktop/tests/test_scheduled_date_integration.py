"""Scheduler regression: a fresh workspace must not export all history."""
import asyncio
import csv
import io
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from signal_archive.direct_app import scheduler
from signal_archive.direct_service import DirectService


class SchedulerDateIntegrationTests(unittest.TestCase):
    def test_valid_external_catalog_edit_is_adopted_and_invalid_one_blocks_export(self):
        async def run(root):
            client = SimpleNamespace()
            service = DirectService(client, root / "state", root / "exports")
            service.state["dialogs"] = [{"id": "1", "title": "Example", "type": "chat"}]
            service.select(["1"])
            path = service.catalog_path
            altered = path.read_text(encoding="utf-8-sig").replace("Example", "Changed")
            path.write_text(altered, encoding="utf-8-sig")
            source = root / "source.csv"
            source.write_text("message\n", encoding="utf-8")
            async def fake_export(*args, **kwargs):
                return {"path": str(source), "messages": 0, "last_id": 0}
            with patch("signal_archive.direct_service.export_chat", side_effect=fake_export) as export:
                await service.export_selected()
                self.assertEqual(export.call_count, 1)
            self.assertEqual(service.state["dialogs"][0]["title"], "Changed")
            self.assertIn("Changed", path.read_text(encoding="utf-8-sig"))

            path.write_bytes(b"not a catalog\n")
            with patch("signal_archive.direct_service.export_chat") as export:
                with self.assertRaises(ValueError):
                    await service.export_selected()
                export.assert_not_called()
            self.assertEqual(path.read_bytes(), b"not a catalog\n")
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_scheduler_skips_changed_catalog_without_marking_day(self):
        async def run(root):
            service = DirectService(None, root / "state", root / "exports")
            service.state["dialogs"] = [{"id": "1", "title": "Example"}]
            service.select(["1"])
            service.state["schedule"] = {"enabled": True, "time": "00:00"}
            service.catalog_path.write_text("changed", encoding="utf-8")
            runtime = SimpleNamespace(service=service, future=None, scheduled_task=None, scheduled_cancel_requested=False)
            waits = []
            async def stop(seconds):
                waits.append(seconds)
                raise asyncio.CancelledError()
            with patch("signal_archive.direct_app.asyncio.sleep", stop), \
                 patch.object(service, "export_selected") as export:
                with self.assertRaises(asyncio.CancelledError):
                    await scheduler(runtime)
                export.assert_not_called()
            self.assertEqual(waits, [30])
            self.assertNotIn("failed_day", service.state["schedule"])
            self.assertNotIn("last_day", service.state["schedule"])
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_first_scheduled_run_exports_only_last_day(self):
        now = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)

        class Client:
            async def get_input_entity(self, chat_id):
                return chat_id

            async def iter_messages(self, peer, **kwargs):
                self.options = kwargs
                yield SimpleNamespace(id=1, date=now - timedelta(days=20), message="old")
                yield SimpleNamespace(id=2, date=now - timedelta(hours=2), message="recent")

        async def run(root):
            client = Client()
            service = DirectService(client, root / "state", root / "exports")
            service.state["dialogs"] = [{"id": "1", "title": "Example"}]
            service.select(["1"])
            service.state["schedule"] = {"enabled": True, "time": "00:00"}
            runtime = SimpleNamespace(service=service, future=None, scheduled_task=None, scheduled_cancel_requested=False)

            async def stop_after_one_iteration(seconds):
                raise asyncio.CancelledError()

            with patch("signal_archive.direct_service._now", return_value=now), \
                 patch("signal_archive.direct_app.asyncio.sleep", stop_after_one_iteration), \
                 patch("signal_archive.export_notifications.notify_export_completed"):
                with self.assertRaises(asyncio.CancelledError):
                    await scheduler(runtime)

            self.assertEqual(client.options["offset_date"], now - timedelta(days=1, seconds=1))
            files = list((root / "exports").glob("*.zip"))
            self.assertEqual(len(files), 1)
            with zipfile.ZipFile(files[0]) as archive:
                name = next(name for name in archive.namelist() if name.endswith(".csv"))
                rows = list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig"))))
            self.assertEqual([r["text"] for r in rows if r.get("record_type") == "message"], ["recent"])
            self.assertNotIn("1", service.state["checkpoints"])

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))
