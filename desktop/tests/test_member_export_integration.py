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
    def test_batch_uses_externally_edited_catalog_group_labels(self):
        from signal_archive import telegram_ops
        from signal_archive.direct_service import DirectService

        class Group:
            pass

        class Client:
            async def get_entity(self, chat_id):
                return Group()

            async def iter_participants(self, entity):
                yield SimpleNamespace(id=12, username="alice", first_name="Alice")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = DirectService(Client(), root / "state", root / "exports")
            def setup(state):
                state["dialogs"] = [{"id": "123", "title": "Group", "type": "group"}]
                state["blocks"] = {"old": {"name": "Old", "chat_ids": ["123"]}}
            service.mutate_catalog(setup)
            catalog = service.catalog_path
            catalog.write_text(catalog.read_text(encoding="utf-8-sig").replace(",Old,", ",New,"), encoding="utf-8-sig")
            with patch.object(telegram_ops, "Chat", Group):
                result = asyncio.run(service.export_members_batch(["123"]))
            with zipfile.ZipFile(result["path"]) as archive:
                rows = csv.DictReader(io.StringIO(archive.read(archive.namelist()[0]).decode("utf-8-sig")))
                member = next(row for row in rows if row["record_type"] == "member")
            self.assertEqual(member["block group / plan"], "New")

    def test_batch_flood_wait_is_not_treated_as_partial_success(self):
        from telethon import errors
        from signal_archive import telegram_ops

        class Client:
            async def get_entity(self, chat_id):
                raise errors.FloodWaitError(None, capture=60)

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(errors.FloodWaitError):
                asyncio.run(telegram_ops.export_group_members_batch(
                    Client(), [{"id": "123", "title": "Group"}], Path(temporary)))
            self.assertEqual(list(Path(temporary).iterdir()), [])

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
            with patch.object(telegram_ops, "Chat", Group):
                result = asyncio.run(service.export_members("123"))
            self.assertEqual(result["members_exported"], 2)
            self.assertEqual(service.state["job"]["status"], "success")
            self.assertEqual(list((root / "exports").glob("*")), [Path(result["path"])])
            with zipfile.ZipFile(result["path"]) as archive:
                self.assertEqual(len(archive.namelist()), 1)
                rows = list(csv.DictReader(io.StringIO(archive.read(archive.namelist()[0]).decode("utf-8-sig"))))
                rows = [row for row in rows if row.get("record_type") != "project"]
            self.assertEqual([row["username"] for row in rows], ["alice", "bob"])

    def test_batch_export_labels_source_groups_blocks_and_plan(self):
        from signal_archive import telegram_ops
        from signal_archive.direct_service import DirectService

        class Group:
            pass

        class NotGroup:
            pass

        class Client:
            async def get_entity(self, chat_id):
                return Group()

            async def iter_participants(self, entity):
                yield SimpleNamespace(id=12, username="alice", first_name="Alice")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = DirectService(Client(), root / "state", root / "exports")
            service.state["dialogs"] = [
                {"id": "123", "title": "Group one", "type": "group"},
                {"id": "456", "title": "Group two", "type": "group"},
                {"id": "789", "title": "A channel", "type": "channel"},
            ]
            service.state["blocks"] = {
                "a": {"name": "Work", "chat_ids": ["123"]},
                "b": {"name": "Friends", "chat_ids": ["123", "456"]},
            }
            with patch.object(telegram_ops, "Chat", Group), patch.object(telegram_ops, "Channel", NotGroup):
                result = asyncio.run(service.export_members_batch(
                    ["123", "456", "789", "123"], plan="July", enrich_profiles=False))
            self.assertEqual(result["members_exported"], 2)
            self.assertEqual(result["groups_exported"], 2)
            with zipfile.ZipFile(result["path"]) as archive:
                self.assertEqual(len(archive.namelist()), 1)
                raw = archive.read(archive.namelist()[0]).decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(raw))
            self.assertIn("source_group_id", reader.fieldnames)
            self.assertIn("source_group_title", reader.fieldnames)
            self.assertIn("block group / plan", reader.fieldnames)
            rows = [row for row in reader if row["record_type"] == "member"]
            self.assertEqual([row["source_group_id"] for row in rows], ["123", "456"])
            self.assertEqual([row["source_group_title"] for row in rows], ["Group one", "Group two"])
            self.assertEqual([row["block group / plan"] for row in rows],
                             ["Work; Friends / July", "Friends / July"])
