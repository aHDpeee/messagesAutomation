import asyncio
import tempfile
import unittest
from pathlib import Path

from tg_observatory.storage.database import Database


class DatabaseCancellationTests(unittest.TestCase):
    def test_cancel_rolls_back_and_next_transaction_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite")
            try:
                database.migrate()
                with self.assertRaises(asyncio.CancelledError):
                    with database.transaction() as connection:
                        connection.execute("INSERT INTO schema_migrations(version) VALUES (9999)")
                        raise asyncio.CancelledError()
                self.assertIsNone(database.fetchone("SELECT version FROM schema_migrations WHERE version=9999"))
                with database.transaction() as connection:
                    connection.execute("INSERT INTO schema_migrations(version) VALUES (9998)")
                self.assertEqual(database.fetchone("SELECT version FROM schema_migrations WHERE version=9998")[0], 9998)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
