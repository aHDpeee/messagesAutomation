import zipfile
import asyncio
import importlib.util
import csv
import io
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from signal_archive.direct_service import DirectService


@unittest.skipUnless(importlib.util.find_spec('telethon'), 'Telethon is not installed')
class LegacyServiceTests(unittest.TestCase):
    def make_database(self, root):
        path = root / 'old.sqlite'
        con = sqlite3.connect(path)
        con.executescript('''
          CREATE TABLE chats (account_id INTEGER, id INTEGER, title TEXT, type TEXT, username TEXT);
          CREATE TABLE messages (account_id INTEGER, chat_id INTEGER, id INTEGER, date TEXT,
            sender_id INTEGER, sender_name TEXT, text TEXT, reply_to_msg_id INTEGER,
            edit_date TEXT, is_deleted INTEGER);
          INSERT INTO chats VALUES (1, -2, 'Old group', 'group', NULL);
          INSERT INTO messages VALUES (1,-2,3,'2026-01-01',4,'Alice','hello',NULL,NULL,0);
        ''')
        con.close()
        return path

    def test_local_export_works_during_telegram_cooldown(self):
        async def run(root):
            db = self.make_database(root)
            app = DirectService(None, root / 'state', root / 'exports')
            app.record_wait(120, job_name='connect')
            cooldown = app.state['cooldown_until']
            result = await app.export_legacy_chat(db, 1, -2)
            self.assertEqual(result['messages'], 1)
            self.assertEqual(next(csv.DictReader(io.StringIO(zipfile.ZipFile(result['path']).read(result['file']).decode('utf-8-sig'))))['text'], 'hello')
            self.assertEqual(app.state['cooldown_until'], cooldown)
            self.assertEqual(app.state['last_export_old_archive']['status'], 'success')
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_cancel_waits_for_worker_cleanup_and_records_interruption(self):
        async def run(root):
            from signal_archive import legacy_archive
            app = DirectService(None, root / 'state', root / 'exports')
            started = threading.Event()
            finished = threading.Event()
            def worker(*args, on_progress, cancel_event, **kwargs):
                on_progress(1000)
                started.set()
                if not cancel_event.wait(2):
                    raise AssertionError('cancellation did not reach worker')
                finished.set()
                raise InterruptedError('cancelled')
            with patch.object(legacy_archive, 'export_chat', worker):
                task = asyncio.create_task(app.export_legacy_chat(root / 'old.sqlite', 1, -2))
                async def wait_for_progress():
                    while not started.is_set() or app.state.get('job', {}).get('current_legacy_messages') != 1000:
                        await asyncio.sleep(0.02)
                await asyncio.wait_for(wait_for_progress(), 1)
                self.assertEqual(app.state['job']['current_legacy_messages'], 1000)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(finished.is_set())
            self.assertEqual(app.state['job']['status'], 'interrupted')
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))


if __name__ == '__main__':
    unittest.main()
