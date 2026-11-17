import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from signal_archive import catalog
from signal_archive.direct_service import DirectService


class CatalogEditingTests(unittest.TestCase):
    def setup_service(self, root):
        service = DirectService(None, root / "state", root / "exports")
        service.state["dialogs"] = [{"id": "-1001", "title": "One", "type": "group", "accessible": True}]
        service.state["selected"] = ["-1001"]
        service.state["blocks"] = {"a": {"name": "Friends", "chat_ids": ["-1001"]}}
        service.save()
        catalog.write_catalog(service.state, service.catalog_path)
        return service

    def edit_row(self, text, **fields):
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        rows[0].update(fields)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    def edit_title(self, text, title):
        return self.edit_row(text, title=title)

    def test_import_backup_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, digest = service.catalog_content()
            backup = service.import_catalog(self.edit_title(text, "Edited"), expected_hash=digest)
            self.assertEqual(service.state["dialogs"][0]["title"], "Edited")
            self.assertEqual(service.state["blocks"]["a"]["chat_ids"], ["-1001"])
            self.assertIn(backup, service.catalog_backups())
            service.restore_catalog(backup)
            self.assertEqual(service.state["dialogs"][0]["title"], "One")
            self.assertEqual(len(service.catalog_backups()), 2)

    def test_external_group_change_applies_without_rewriting_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = self.setup_service(root)
            content, _ = service.catalog_content()
            changed = content.replace('Friends', 'Family')
            service.catalog_path.write_text(changed, encoding='utf-8')
            before = service.catalog_path.read_bytes()
            self.assertTrue(service.sync_catalog())
            self.assertEqual(service.catalog_path.read_bytes(), before)
            self.assertTrue(any(b['name'] == 'Family' and '-1001' in b['chat_ids'] for b in service.state['blocks'].values()))
            previous = service.catalog_backups()
            self.assertTrue(previous)
            reopened = DirectService(None, root / 'state', root / 'exports')
            self.assertTrue(any(b['name'] == 'Family' and '-1001' in b['chat_ids'] for b in reopened.state['blocks'].values()))

    def test_invalid_external_catalog_is_preserved_and_not_applied(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = self.setup_service(root)
            service.catalog_path.write_bytes(b'invalid,contents\n')
            reopened = DirectService(None, root / 'state', root / 'exports')
            self.assertFalse(reopened.catalog_ready)
            self.assertTrue(reopened.catalog_sync_error)
            self.assertEqual(reopened.catalog_path.read_bytes(), b'invalid,contents\n')
            self.assertEqual(reopened.state['dialogs'][0]['title'], 'One')

    def test_external_edits_are_never_overwritten_implicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, digest = service.catalog_content()
            changed = self.edit_title(text, "External")
            service.catalog_path.write_text(changed, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed"):
                catalog.write_catalog(service.state, service.catalog_path)
            with self.assertRaisesRegex(ValueError, "changed"):
                service.import_catalog(text, expected_hash=digest)
            self.assertEqual(service.catalog_path.read_bytes(), changed.encode("utf-8"))
            service.import_catalog(changed)
            self.assertEqual(service.state["dialogs"][0]["title"], "External")

    def test_invalid_input_leaves_catalog_and_state_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            before = service.catalog_path.read_bytes()
            with self.assertRaises(ValueError):
                service.import_catalog("id,title\n1,Oops\n")
            self.assertEqual(service.catalog_path.read_bytes(), before)
            self.assertEqual(service.state["dialogs"][0]["title"], "One")
            self.assertEqual(service.catalog_backups(), [])

    def test_invalid_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, _ = service.catalog_content()
            with self.assertRaisesRegex(ValueError, "Invalid date"):
                service.import_catalog(self.edit_row(text, last_message_date="yesterday-ish"))

    def test_spreadsheet_date_is_normalized_to_iso(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, _ = service.catalog_content()
            service.import_catalog(self.edit_row(text, last_message_date="11/15/26 4:15"))
            self.assertEqual(service.state["dialogs"][0]["last_message_date"], "2026-11-15T04:15:00+00:00")
            current, _ = service.catalog_content()
            self.assertIn("2026-11-15T04:15:00+00:00", current)

    def test_spreadsheet_date_with_ampm_and_seconds_is_normalized(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, _ = service.catalog_content()
            service.import_catalog(self.edit_row(text, last_message_date="11/15/2026 4:15:30 PM"))
            self.assertEqual(service.state["dialogs"][0]["last_message_date"], "2026-11-15T16:15:30+00:00")

    def test_all_date_fields_accept_spreadsheet_formats(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, _ = service.catalog_content()
            service.import_catalog(self.edit_row(text, added_at="11/15/26", members_exported_at="11/15/2026 4:15"))
            dialog = service.state["dialogs"][0]
            self.assertEqual(dialog["added_at"], "2026-11-15T00:00:00+00:00")
            self.assertEqual(dialog["members_exported_at"], "2026-11-15T04:15:00+00:00")
            serial = (datetime(2026, 11, 15, tzinfo=timezone.utc) - datetime(1899, 12, 30, tzinfo=timezone.utc)).days
            service.import_catalog(self.edit_row(text, last_message_date=str(serial)))
            self.assertEqual(service.state["dialogs"][0]["last_message_date"], "2026-11-15T00:00:00+00:00")

    def test_external_spreadsheet_dates_and_groups_are_adopted_by_sync(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = self.setup_service(root)
            text, _ = service.catalog_content()
            service.catalog_path.write_text(
                self.edit_row(text, last_message_date="11/15/26 4:15", blocks="Family"),
                encoding="utf-8",
            )
            self.assertTrue(service.sync_catalog())
            self.assertEqual(service.state["dialogs"][0]["last_message_date"], "2026-11-15T04:15:00+00:00")
            self.assertTrue(any(b["name"] == "Family" and "-1001" in b["chat_ids"] for b in service.state["blocks"].values()))
            reopened = DirectService(None, root / "state", root / "exports")
            self.assertEqual(reopened.state["dialogs"][0]["last_message_date"], "2026-11-15T04:15:00+00:00")

    def test_impossible_calendar_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, _ = service.catalog_content()
            with self.assertRaisesRegex(ValueError, "Invalid date in last_message_date"):
                service.import_catalog(self.edit_row(text, last_message_date="13/15/26 4:15"))

    def test_csv_removes_groups_that_are_not_in_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            service.mutate_catalog(lambda state: catalog.create_block(state, "Old"))
            self.assertEqual(len(service.state["blocks"]), 2)
            text, _ = service.catalog_content()
            service.import_catalog(self.edit_row(text, blocks="Work"))
            self.assertEqual([b["name"] for b in service.state["blocks"].values()], ["Work"])
            self.assertEqual(next(iter(service.state["blocks"].values()))["chat_ids"], ["-1001"])

    def test_group_id_is_stable_when_csv_keeps_its_name(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, _ = service.catalog_content()
            service.import_catalog(self.edit_row(text, blocks="Friends"))
            self.assertEqual(service.state["blocks"]["a"]["chat_ids"], ["-1001"])

    def test_selection_adopts_external_csv_before_saving(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, _ = service.catalog_content()
            service.catalog_path.write_text(self.edit_title(text, "External"), encoding="utf-8")
            service.select([])
            self.assertEqual(service.state["dialogs"][0]["title"], "External")
            self.assertEqual(service.state["selected"], [])
            restarted = DirectService(None, Path(folder) / "state", Path(folder) / "exports")
            self.assertEqual(restarted.state["selected"], [])
            self.assertTrue(restarted.catalog_ready)

    def test_import_preserves_exact_external_edits_as_well_as_applied_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, _ = service.catalog_content()
            external = b"not valid csv\nuser's unfinished changes\n"
            service.catalog_path.write_bytes(external)
            service.import_catalog(self.edit_title(text, "Uploaded"))
            backups = catalog.list_catalog_backups(service.catalog_path)
            self.assertEqual(len(backups), 2)
            self.assertIn(external, [p.read_bytes() for p in backups])
            self.assertEqual(service.state["dialogs"][0]["title"], "Uploaded")

    def test_restore_rejects_stale_current_file_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            text, digest = service.catalog_content()
            backup = service.import_catalog(self.edit_title(text, "Changed"), expected_hash=digest)
            _, current_hash = service.catalog_content()
            external = b"unfinished external edit\n"
            service.catalog_path.write_bytes(external)
            with self.assertRaisesRegex(ValueError, "changed"):
                service.restore_catalog(backup, expected_hash=current_hash)
            self.assertEqual(service.catalog_path.read_bytes(), external)

    def test_interrupted_state_write_recovers_on_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = self.setup_service(root)
            text, _ = service.catalog_content()
            original = service.state_path
            from signal_archive import direct_service
            real_write = direct_service._write_state
            def fail_state(path, state):
                if path == original:
                    raise OSError("disk failure")
                return real_write(path, state)
            with patch.object(direct_service, "_write_state", side_effect=fail_state):
                with self.assertRaises(OSError):
                    service.import_catalog(self.edit_title(text, "Recovered"))
            self.assertTrue(service.catalog_journal.exists())
            with self.assertRaises(RuntimeError):
                catalog.write_catalog(service.state, service.catalog_path)
            recovered = DirectService(None, root / "state", root / "exports")
            self.assertEqual(recovered.state["dialogs"][0]["title"], "Recovered")
            self.assertFalse(recovered.catalog_journal.exists())

    def test_group_name_with_comma_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            service.mutate_catalog(lambda state: state["blocks"]["a"].update(name="Friends, family"))
            text, digest = service.catalog_content()
            service.import_catalog(text, expected_hash=digest)
            self.assertEqual(service.state["blocks"]["a"]["name"], "Friends, family")
            self.assertEqual(service.state["blocks"]["a"]["chat_ids"], ["-1001"])

    def test_group_name_starting_with_bracket_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.setup_service(Path(folder))
            service.mutate_catalog(lambda state: state["blocks"]["a"].update(name="[Work]"))
            text, digest = service.catalog_content()
            service.import_catalog(text, expected_hash=digest)
            self.assertEqual(service.state["blocks"]["a"]["name"], "[Work]")
            self.assertEqual(service.state["blocks"]["a"]["chat_ids"], ["-1001"])

    def test_existing_markerless_external_edit_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = self.setup_service(root)
            text, _ = service.catalog_content()
            external = self.edit_title(text, "External")
            service.catalog_path.write_text(external, encoding="utf-8")
            service.catalog_path.with_name("chats.csv.sha256").unlink()
            reopened = DirectService(None, root / "state", root / "exports")
            self.assertTrue(reopened.catalog_ready)
            self.assertEqual(reopened.state["dialogs"][0]["title"], "External")
            self.assertEqual(reopened.catalog_path.read_bytes(), external.encode("utf-8"))
