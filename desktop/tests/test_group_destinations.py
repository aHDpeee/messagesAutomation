import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from signal_archive import catalog
from signal_archive.direct_service import DirectService


class GroupDestinationTests(unittest.TestCase):
    def test_single_destination_and_csv_round_trip(self):
        state = {"group_semantics_version": 1, "dialogs": [
            {"id": "1", "title": "A", "type": "chat"},
            {"id": "2", "title": "B", "type": "channel"},
            {"id": "3", "title": "C", "type": "bot"}],
            "selected": [], "root_selected": [], "blocks": {}}
        first = catalog.create_block(state, "Work")
        second = catalog.create_block(state, "News")
        catalog.set_chat_group(state, "1", "ungrouped")
        catalog.set_chat_group(state, "2", first)
        catalog.set_chat_group(state, "2", second)
        self.assertEqual(state["selected"], ["1", "2"])
        self.assertEqual(catalog.chat_group(state, "2"), second)
        self.assertEqual(state["blocks"][first]["chat_ids"], [])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "catalog.csv"
            catalog.write_catalog(state, p)
            rows = list(csv.DictReader(io.StringIO(p.read_text(encoding="utf-8-sig"))))
            self.assertEqual([(r["blocks"], r["selected"]) for r in rows], [("Ungrouped", "yes"), ("News", "yes"), ("", "no")])
            restored = catalog.parse_catalog(p.read_bytes(), state)
            self.assertEqual(restored["selected"], ["1", "2"])
            self.assertEqual(catalog.chat_group(restored, "1"), "ungrouped")
            self.assertEqual(catalog.chat_group(restored, "2"), second)
            self.assertIsNone(catalog.chat_group(restored, "3"))

    def test_existing_selection_is_migrated_and_old_csv_backed_up(self):
        old = {"dialogs": [{"id": "1", "title": "A", "type": "chat"}, {"id": "2", "title": "B", "type": "group"}, {"id": "3", "title": "C", "type": "chat"}],
               "selected": ["1", "2"], "checkpoints": {},
               "blocks": {"a": {"name": "Work", "chat_ids": ["2", "3"]}, "b": {"name": "News", "chat_ids": ["2"]}}}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "state"
            home.mkdir()
            (home / "direct-state.json").write_text(json.dumps(old), encoding="utf-8")
            catalog.write_catalog(old, home / "chats.csv")
            before = (home / "chats.csv").read_bytes()
            service = DirectService(None, home, Path(tmp) / "exports")
            self.assertEqual(set(service.state["selected"]), {"1", "2"})
            self.assertEqual(catalog.chat_group(service.state, "1"), "ungrouped")
            self.assertEqual(catalog.chat_group(service.state, "2"), "a")
            self.assertIsNone(catalog.chat_group(service.state, "3"))
            self.assertIn(before, [p.read_bytes() for p in catalog.list_catalog_backups(home / "chats.csv")])
            again = DirectService(None, home, Path(tmp) / "exports")
            self.assertEqual(set(again.state["selected"]), {"1", "2"})

    def test_manual_csv_multiple_groups_rejected(self):
        state = {"group_semantics_version": 1, "dialogs": [{"id": "1", "title": "A", "type": "chat"}], "selected": [], "root_selected": [], "blocks": {}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "catalog.csv"
            catalog.write_catalog(state, p)
            rows = list(csv.DictReader(io.StringIO(p.read_text(encoding="utf-8-sig"))))
            rows[0]["blocks"] = "Work, Friends"
            s = io.StringIO(newline="")
            w = csv.DictWriter(s, fieldnames=catalog.CATALOG_FIELDS)
            w.writeheader(); w.writerows(rows)
            with self.assertRaisesRegex(ValueError, "only one group"):
                catalog.parse_catalog(s.getvalue(), state)
