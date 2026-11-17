import asyncio
import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class MemberExportIntegrationTests(unittest.TestCase):
    def test_service_exports_visible_members_in_one_zip(self):
        from signal_archive import telegram_ops
        from signal_archive.direct_service import DirectService

        class Group:
            pass

        class Client:
            async def get_entity(self, chat_id):
                return Group()

            async def iter_participants(self, entity, limit):
                yield SimpleNamespace(id=12, username="alice", first_name="Alice")
                yield SimpleNamespace(id=13, username="bob", first_name="Bob")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = DirectService(Client(), root / "state", root / "exports")
            service.state["dialogs"] = [{"id": "123", "type": "group"}]
            with patch.object(telegram_ops, "Chat", Group), patch("signal_archive.completion_music.play_on_completion"):
                result = asyncio.run(service.export_members("123"))
            self.assertEqual(result["members_exported"], 2)
            self.assertEqual(service.state["job"]["status"], "success")
            self.assertEqual(list((root / "exports").glob("*")), [Path(result["path"])])
            with zipfile.ZipFile(result["path"]) as archive:
                self.assertEqual(len(archive.namelist()), 1)
                rows = list(csv.DictReader(io.StringIO(archive.read(archive.namelist()[0]).decode("utf-8-sig"))))
                rows = [row for row in rows if row.get("record_type") != "project"]
            self.assertEqual([row["username"] for row in rows], ["alice", "bob"])
