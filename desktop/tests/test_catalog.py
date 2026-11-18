import csv
import tempfile
import unittest
from pathlib import Path

from signal_archive import catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.state = {"dialogs": [{"id": "1", "title": "Alice", "type": "chat", "username": "alice", "added_at": "yesterday", "added_via": "link", "members_exported_at": "today"}, {"id": "2", "title": "=Danger", "type": "group"}], "selected": ["1"]}

    def test_assigning_a_group_moves_chat_and_removal_keeps_catalog_record(self):
        a = catalog.create_block(self.state, "Work")
        b = catalog.create_block(self.state, "Friends")
        catalog.assign_block(self.state, a, ["1", "2", "1"])
        catalog.assign_block(self.state, b, ["1"])
        self.assertEqual(self.state["blocks"][a]["chat_ids"], ["2"])
        self.assertEqual(self.state["blocks"][b]["chat_ids"], ["1"])
        catalog.rename_block(self.state, b, "Family")
        with self.assertRaises(ValueError):
            catalog.rename_block(self.state, b, "work")
        with self.assertRaises(ValueError):
            catalog.assign_block(self.state, a, ["999"])
        catalog.delete_block(self.state, a)
        self.assertEqual(len(self.state["dialogs"]), 2)
        self.assertEqual(self.state["selected"], ["1"])

    def test_refresh_preserves_original_metadata(self):
        result = catalog.merge_dialogs(self.state, [{"id": "1", "title": "Updated"}, {"id": "3", "title": "New"}], added_at="now")
        self.assertEqual((result[0]["added_at"], result[0]["added_via"], result[0]["members_exported_at"]), ("yesterday", "link", "today"))
        self.assertEqual(result[1]["added_at"], "now")

    def test_missing_selected_or_block_chat_is_preserved_as_inaccessible(self):
        block = catalog.create_block(self.state, "Groups")
        catalog.assign_block(self.state, block, ["2"])
        result = catalog.merge_dialogs(self.state, [])
        self.assertEqual({d["id"] for d in result}, {"1", "2"})
        self.assertTrue(all(not d["accessible"] for d in result))

    def test_csv_and_txt_snapshots_are_readable_and_safe(self):
        block = catalog.create_block(self.state, "Friends")
        catalog.assign_block(self.state, block, ["1"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.csv"
            catalog.write_catalog(self.state, path)
            with path.open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["link"], "https://t.me/alice")
            self.assertEqual(rows[0]["blocks"], "Friends")
            self.assertEqual(rows[0]["selected"], "yes")
            self.assertEqual(rows[1]["title"], "'=Danger")
            selected = Path(tmp) / "chats.txt"
            catalog.write_selected_chats(self.state, selected)
            self.assertIn("Alice — 1 — https://t.me/alice", selected.read_text(encoding="utf-8-sig"))
            self.assertNotIn("Danger", selected.read_text(encoding="utf-8-sig"))
