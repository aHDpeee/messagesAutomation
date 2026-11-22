"""SQLite connection and explicit, transactional schema migrations."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence, Any, cast

from tg_observatory.storage.migrations import MIGRATIONS
from tg_observatory.storage.search import dialog_rank


def timestamp_us(value: str | None) -> int | None:
    """Compare ISO timestamps without SQLite julianday's millisecond rounding."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    delta = parsed.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


class Database:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.create_function("dialog_rank", 4, dialog_rank, deterministic=True)
        self._connection.create_function("timestamp_us", 1, timestamp_us, deterministic=True)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()

    def migrate(self) -> None:
        connection = self._connection
        with self._lock:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY)"
            )
            applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            for version, statements in MIGRATIONS:
                if version in applied:
                    continue
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                    )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a group of statements atomically, serialized across runtime threads."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def fetchone(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return cast(sqlite3.Row | None, self._connection.execute(sql, parameters).fetchone())

    def fetchall(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchall()

    def close(self) -> None:
        with self._lock:
            self._connection.close()
