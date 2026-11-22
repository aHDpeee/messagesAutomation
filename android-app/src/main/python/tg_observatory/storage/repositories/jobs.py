"""Durable job state, sanitized parameters and resumable checkpoints."""

from __future__ import annotations

import json
from typing import Any

from tg_observatory.storage.database import Database
from tg_observatory.storage.time import utc_now


class JobRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, job_id: str, job_type: str, params: dict[str, Any], resumable: bool) -> None:
        now = utc_now()
        with self._database.transaction() as connection:
            connection.execute(
                """INSERT INTO export_jobs(
                   id, state, created_at, updated_at, job_type, params_json,
                   progress, attempts, resumable, started_at, completed_at,
                   error_json, result_json
                   ) VALUES (?, 'pending', ?, ?, ?, ?, 0, 0, ?, NULL, NULL, NULL, NULL)""",
                (job_id, now, now, job_type, _json(params), int(resumable)),
            )

    def retry(self, job_id: str) -> dict[str, Any] | None:
        """Requeue a terminal resumable job while preserving its checkpoint."""
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM export_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None or not row["resumable"] or row["state"] not in {"failed", "cancelled"}:
                return None
            now = utc_now()
            connection.execute(
                """UPDATE export_jobs SET state='pending', progress=0, updated_at=?,
                   started_at=NULL, completed_at=NULL, error_json=NULL, result_json=NULL,
                   progress_message=NULL, attempts=attempts+1 WHERE id=?""",
                (now, job_id),
            )
            return _record(row)

    def recover_interrupted(self) -> int:
        """Mark pending/running jobs interrupted when their owning process exited."""
        now = utc_now()
        error = _json({
            "code": "process_interrupted",
            "message": "The app stopped before this job finished.",
        })
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE export_jobs SET state='failed', updated_at=?, completed_at=?,
                   error_json=? WHERE state IN ('pending', 'running')""",
                (now, now, error),
            )
            return cursor.rowcount

    def mark_running(self, job_id: str) -> None:
        now = utc_now()
        with self._database.transaction() as connection:
            connection.execute(
                """UPDATE export_jobs SET state='running', started_at=?, updated_at=?
                   WHERE id=? AND state='pending'""",
                (now, now, job_id),
            )

    def set_progress(self, job_id: str, progress: float, message: str | None) -> None:
        now = utc_now()
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE export_jobs SET progress=?, progress_message=COALESCE(?, progress_message), updated_at=? WHERE id=?",
                (progress, message, now, job_id),
            )

    def set_message(self, job_id: str, message: str) -> None:
        now = utc_now()
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE export_jobs SET progress_message=?, updated_at=? WHERE id=?",
                (message, now, job_id),
            )

    def set_checkpoint(self, job_id: str, checkpoint: dict[str, Any]) -> None:
        now = utc_now()
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE export_jobs SET checkpoint_json=?, updated_at=? WHERE id=?",
                (_json(checkpoint), now, job_id),
            )

    def finish(
        self, job_id: str, state: str, *, result: Any = None, error: dict[str, Any] | None = None
    ) -> None:
        now = utc_now()
        with self._database.transaction() as connection:
            connection.execute(
                """UPDATE export_jobs SET state=?,
                   progress=CASE WHEN ? IN ('completed', 'partial') THEN 1.0 ELSE progress END,
                   result_json=?, error_json=?,
                   updated_at=?, completed_at=? WHERE id=?""",
                (state, state,
                 _json(result) if result is not None else None,
                 _json(error) if error is not None else None,
                 now, now, job_id),
            )
            if state in {"completed", "partial"}:
                connection.execute("DELETE FROM export_source_messages WHERE export_job_id=?", (job_id,))

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self._database.fetchone("SELECT * FROM export_jobs WHERE id=?", (job_id,))
        return _record(row) if row is not None else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._database.fetchall(
            "SELECT * FROM export_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_record(row) for row in rows]


def _record(row: Any) -> dict[str, Any]:
    checkpoint = json.loads(row["checkpoint_json"]) if row["checkpoint_json"] else None
    return {
        "job_id": row["id"],
        "state": row["state"],
        "job_type": row["job_type"],
        "params": json.loads(row["params_json"] or "{}"),
        "progress": float(row["progress"]),
        "message": row["progress_message"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error": json.loads(row["error_json"]) if row["error_json"] else None,
        "checkpoint": checkpoint,
        "attempts": int(row["attempts"]),
        "resumable": bool(row["resumable"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
