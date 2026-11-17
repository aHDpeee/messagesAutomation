import csv
import io
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from signal_archive import legacy_archive


class LegacyArchiveTests(unittest.TestCase):
    def test_configured_legacy_location_matches_installer_conventions(self):
        with patch.dict(os.environ, {"TELEGRAM_ARCHIVE_HOME": "/tmp/custom-archive", "XDG_DATA_HOME": "/tmp/xdg"}):
            self.assertEqual(legacy_archive.default_database(), Path('/tmp/custom-archive/data/backups/telegram_backup.db'))
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/xdg"}):
            with patch.dict(os.environ, {"TELEGRAM_ARCHIVE_HOME": ""}):
                self.assertEqual(legacy_archive.default_database(), Path('/tmp/xdg/TelegramArchive/data/backups/telegram_backup.db'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "old archive #1.sqlite"
        self.con = sqlite3.connect(self.db)
        self.con.executescript('''
            CREATE TABLE chats (account_id INTEGER, id INTEGER, title TEXT, type TEXT, username TEXT);
            CREATE TABLE messages (account_id INTEGER, chat_id INTEGER, id INTEGER, date TEXT,
                sender_id INTEGER, sender_name TEXT, text TEXT, reply_to_msg_id INTEGER,
                edit_date TEXT, is_deleted INTEGER, raw_data TEXT);
            INSERT INTO chats VALUES (1, -123, 'Friends', 'group', NULL);
            INSERT INTO chats VALUES (2, -123, 'Other account', 'group', NULL);
            INSERT INTO messages VALUES (1, -123, 1, '2026-01-01 00:00:00', 5, 'Alice', 'one', NULL, NULL, 0, '{"media":"secret"}');
            INSERT INTO messages VALUES (1, -123, 2, '2026-01-02 00:00:00', 5, 'Alice', 'two', 1, NULL, 0, '{"media":"secret"}');
            INSERT INTO messages VALUES (2, -123, 1, '2026-01-01 00:00:00', 9, 'Bob', 'other account', NULL, NULL, 0, '{}');
        ''')
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_lists_accounts_and_exports_only_selected_account_without_media(self):
        sources = legacy_archive.list_chats(self.db)
        self.assertEqual({(row['account_id'], row['id']) for row in sources}, {(1, '-123'), (2, '-123')})
        before = self.db.read_bytes()
        result = legacy_archive.export_chat(self.db, self.root / 'exports', 1, -123)
        rows = list(csv.DictReader(io.StringIO(Path(result['path']).read_text(encoding='utf-8-sig'), newline='')))
        rows = [row for row in rows if row.get("record_type") != "project"]
        self.assertEqual([row['text'] for row in rows], ['one', 'two'])
        self.assertEqual(rows[1]['reply_to_message_id'], '1')
        self.assertNotIn('raw_data', rows[0])
        self.assertNotIn('media', rows[0])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(Path(result['path']).stat().st_mode & 0o777, 0o600)

    def test_recent_date_bounds_are_inclusive_and_account_specific(self):
        start = datetime(2026, 1, 2, tzinfo=timezone.utc)
        result = legacy_archive.export_chat(self.db, self.root / 'exports', 1, -123, since=start, until=start)
        records = list(csv.DictReader(io.StringIO(Path(result['path']).read_text(encoding='utf-8-sig'), newline='')))
        records = [row for row in records if row.get("record_type") != "project"]
        self.assertEqual([r['id'] for r in records], ['2'])
        with self.assertRaises(ValueError):
            legacy_archive.export_chat(self.db, self.root / 'exports', 4, -123)

    def test_write_failure_cleans_partial_files(self):
        with patch.object(legacy_archive.os, 'fsync', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                legacy_archive.export_chat(self.db, self.root / 'exports', 1, -123)
        self.assertEqual(list((self.root / 'exports').iterdir()), [])

    def test_cancelled_export_cleans_partial_files(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(InterruptedError, 'cancelled'):
            legacy_archive.export_chat(self.db, self.root / 'exports', 1, -123, cancel_event=cancel)
        self.assertEqual(list((self.root / 'exports').iterdir()), [])

    def test_current_wal_is_read_not_an_immutable_snapshot(self):
        self.con.execute('PRAGMA journal_mode=WAL')
        self.con.execute("INSERT INTO messages VALUES (1,-123,3,'2026-01-03',5,'Alice','in WAL',NULL,NULL,0,'{}')")
        self.con.commit()
        result = legacy_archive.export_chat(self.db, self.root / 'exports', 1, -123)
        self.assertEqual(result['messages'], 3)

    def test_unsupported_schema_fails_without_output(self):
        other = self.root / 'unsupported.sqlite'
        sqlite3.connect(other).close()
        with self.assertRaisesRegex(ValueError, 'Unsupported Telegram Archive'):
            legacy_archive.list_chats(other)
        self.assertFalse((self.root / 'exports').exists())

    def test_ambiguous_chat_rows_do_not_export(self):
        self.con.execute("INSERT INTO chats VALUES (1, -123, 'Duplicate', 'group', NULL)")
        self.con.commit()
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            legacy_archive.export_chat(self.db, self.root / 'exports', 1, -123)
        self.assertFalse((self.root / 'exports').exists())


if __name__ == '__main__':
    unittest.main()
