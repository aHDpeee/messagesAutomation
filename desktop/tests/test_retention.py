import os
import tempfile
import unittest
from pathlib import Path

from signal_archive.retention import remove_expired


class RetentionTests(unittest.TestCase):
    def test_readable_bundle_name_is_eligible(self):
        from signal_archive.direct_service import _new_bundle
        from datetime import datetime, timezone
        import os
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            bundle = _new_bundle(folder, "chats", label="Friends", since=datetime(2026, 1, 1, tzinfo=timezone.utc), until=datetime(2026, 1, 2, tzinfo=timezone.utc))
            self.assertIn("Friends_2026-01-01_to_2026-01-02", bundle.name)
            bundle.write_bytes(b"zip")
            os.utime(bundle, (1, 1))
            self.assertEqual(remove_expired(folder, [bundle.name], enabled=True, days=1), [bundle.name])
    def test_only_expired_registered_regular_nonactive_archives_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expired = "chats_20260101T000000Z_1234abcd.zip"
            active = "members_20260101T000000Z_1234abcd.zip"
            recent = "old_archive_20260101T000000Z_1234abcd.zip"
            unregistered = "chats_20260102T000000Z_1234abcd.zip"
            other = "personal.zip"
            for name in (expired, active, recent, unregistered, other):
                (root / name).write_bytes(b"test")
            for name in (expired, active, unregistered, other):
                os.utime(root / name, (100, 100))
            names = [expired, active, recent, other, "../personal.zip"]
            self.assertEqual(remove_expired(root, names, days=1, now=200000), [])
            removed = remove_expired(root, names, enabled=True, days=1, now=200000, active_names=(active,))
            self.assertEqual(removed, [expired])
            for name in (active, recent, unregistered, other):
                self.assertTrue((root / name).exists())

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "data"
            target.write_text("safe")
            link = root / "chats_20260101T000000Z_1234abcd.zip"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks unavailable")
            self.assertEqual(remove_expired(root, [link.name], enabled=True, days=1, now=200000), [])
            self.assertEqual(target.read_text(), "safe")

    def test_invalid_retention_is_rejected(self):
        for days in (0, -1, True, 1.5, 36501):
            with self.assertRaises(ValueError):
                remove_expired(Path("."), [], enabled=True, days=days)
