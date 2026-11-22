"""Export artifact metadata persistence."""

from __future__ import annotations

from tg_observatory.storage.database import Database
from tg_observatory.storage.time import utc_now


class ExportRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def record_file(
        self, job_id: str, relative_path: str, size_bytes: int, sha256: str
    ) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                """INSERT INTO export_files(export_job_id, relative_path, size_bytes, sha256, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(export_job_id, relative_path) DO UPDATE SET
                   size_bytes=excluded.size_bytes, sha256=excluded.sha256""",
                (job_id, relative_path, size_bytes, sha256, utc_now()),
            )
