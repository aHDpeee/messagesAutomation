"""A single persistent asyncio loop and lightweight long-running job manager."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Coroutine
from pathlib import Path

from tg_observatory.storage.repositories.jobs import JobRepository


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class Job:
    job_id: str
    job_type: str = "operation"
    resumable: bool = False
    requires_network: bool = False
    state: JobState = JobState.PENDING
    progress: float = 0.0
    result: Any = None
    error: dict[str, Any] | None = None
    message: str | None = None
    task: asyncio.Task[Any] | None = field(default=None, repr=False)
    future: Future[Any] | None = field(default=None, repr=False)


class AsyncRuntime:
    """Own one event loop on a daemon thread for the process lifetime."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="observatory-asyncio", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._ready.set()
        # The short timer services submissions on restricted selectors and gives
        # close() a bounded shutdown latency.
        while not self._stopping:
            await asyncio.sleep(0.02)

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
        if self._loop is None:
            raise RuntimeError("Async runtime has not started")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def close(self, timeout: float = 5.0) -> None:
        self._stopping = True
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=max(0.0, timeout))


class _CooldownActive(Exception):
    pass


class JobManager:
    def __init__(self, runtime: AsyncRuntime, repository: JobRepository | None = None, cooldown_path: Path | None = None) -> None:
        self._runtime = runtime
        self._repository = repository
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(1)
        self._cooldown_path = Path(cooldown_path) if cooldown_path is not None else None
        self._cooldown_until = 0.0
        if self._cooldown_path is not None and self._cooldown_path.exists():
            try:
                value = float(json.loads(self._cooldown_path.read_text(encoding="utf-8"))["until"])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("invalid cooldown")
                self._cooldown_until = value
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise RuntimeError("Telegram cooldown state cannot be read; retry after checking the local data.") from exc

    def _record_cooldown(self, seconds: int) -> None:
        self._cooldown_until = max(self._cooldown_until, time.time() + max(1, int(seconds)))
        if self._cooldown_path is None:
            return
        self._cooldown_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".cooldown-", suffix=".tmp", dir=self._cooldown_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"until": self._cooldown_until}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._cooldown_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def submit(
        self, operation: Coroutine[Any, Any, Any], *, job_id: str | None = None,
        job_type: str = "operation", params: dict[str, Any] | None = None,
        resumable: bool = False, retry: bool = False,
    ) -> str:
        job_id = job_id or uuid.uuid4().hex
        if self._repository is not None:
            if retry:
                if self._repository.retry(job_id) is None:
                    operation.close()
                    raise ValueError("Job is not eligible for resume")
            else:
                self._repository.create(job_id, job_type, params or {}, resumable)
        network_jobs = {"account.send_code", "account.verify_code", "account.verify_password", "account.qr_start", "account.qr_wait", "account.status", "account.logout", "dialogs.sync", "dialogs.add", "dialogs.check_invite", "dialogs.folders", "messages.sync", "messages.preview_range", "participants.capture", "users.resolve", "media.download_dialog", "members.export_csv"}
        requires_network = job_type in network_jobs or (job_type == "members.export" and (params or {}).get("source", "telegram") == "telegram") or (job_type in {"messages.export", "analytics.export"} and bool((params or {}).get("fetch_source")))
        job = Job(job_id=job_id, job_type=job_type, resumable=resumable, requires_network=requires_network)
        with self._lock:
            self._jobs[job_id] = job
        job.future = self._runtime.submit(self._execute(job, operation))
        return job_id

    async def _execute(self, job: Job, operation: Coroutine[Any, Any, Any]) -> None:
        job.task = asyncio.current_task()
        operation_started = False
        try:
            async with self._semaphore:
                if job.requires_network and self._cooldown_until > time.time():
                    operation.close()
                    raise _CooldownActive()
                job.state = JobState.RUNNING
                if self._repository is not None:
                    self._repository.mark_running(job.job_id)
                operation_started = True
                job.result = await operation
                job.progress = 1.0
                job.state = (JobState.PARTIAL if isinstance(job.result, dict)
                             and job.result.get("completeness") == "partial" else JobState.COMPLETED)
                if self._repository is not None:
                    # The QR URL is an authorization secret: keep it only in memory.
                    result = None if job.job_type == "account.qr_start" else job.result
                    self._repository.finish(job.job_id, job.state.value, result=result)
        except asyncio.CancelledError:
            if not operation_started:
                operation.close()
            job.state = JobState.CANCELLED
            if self._repository is not None:
                self._repository.finish(job.job_id, job.state.value)
            raise
        except Exception as exc:
            if isinstance(exc, _CooldownActive):
                seconds = max(1, math.ceil(self._cooldown_until - time.time()))
                job.error = {"code": "flood_wait", "message": f"Telegram requested a pause. Retry in {seconds} seconds.", "retry_after_seconds": seconds}
            elif type(exc).__name__ in {"FloodWaitError", "FloodPremiumWaitError", "FloodTestPhoneWaitError", "SlowModeWaitError"}:
                seconds = max(1, int(getattr(exc, "seconds", 1)))
                try:
                    self._record_cooldown(seconds)
                    message = f"Telegram requested a pause. Retry in {seconds} seconds."
                except OSError:
                    message = f"Telegram requested a pause of {seconds} seconds. Keep the app open: the pause could not be saved."
                job.error = {
                    "code": "flood_wait",
                    "message": message,
                    "retry_after_seconds": seconds,
                }
            else:
                code = getattr(exc, "code", type(exc).__name__)
                known_errors = {
                    "PhoneCodeInvalidError": ("invalid_code", "The verification code is incorrect."),
                    "PhoneCodeExpiredError": ("expired_code", "The verification code expired. Request a new one."),
                    "PhoneNumberInvalidError": ("invalid_phone", "Enter the phone number with its country code."),
                    "PhoneNumberBannedError": ("phone_banned", "Telegram has banned this phone number."),
                    "SendCodeUnavailableError": ("code_unavailable", "Telegram cannot currently deliver a verification code."),
                    "UpdateAppToLoginError": ("official_app_required", "Telegram requires an official app for this login."),
                    "PhonePasswordFloodError": ("login_rate_limited", "Telegram temporarily limited login attempts."),
                    "AuthKeyDuplicatedError": ("session_duplicated", "This Telegram session is no longer valid."),
                    "PasswordHashInvalidError": ("invalid_password", "The two-step verification password is incorrect."),
                    "ApiIdInvalidError": ("invalid_api_credentials", "Check the Telegram API ID and API hash."),
                    "ChannelPrivateError": ("chat_private", "This chat is private or inaccessible to the current account."),
                    "ChannelInvalidError": ("chat_unavailable", "Telegram could not find this chat."),
                    "UsernameInvalidError": ("chat_unavailable", "Telegram could not find this username."),
                    "UsernameNotOccupiedError": ("chat_unavailable", "Telegram could not find this username."),
                    "InviteHashInvalidError": ("invite_invalid", "The invitation link is invalid or expired."),
                    "InviteHashExpiredError": ("invite_invalid", "The invitation link is invalid or expired."),
                    "UserBannedInChannelError": ("chat_banned", "This account is blocked in the chat."),
                    "ConnectionError": ("network_unavailable", "Could not connect to Telegram. Check your connection and retry."),
                    "TimeoutError": ("network_unavailable", "The Telegram connection timed out. Retry later."),
                }
                known = known_errors.get(type(exc).__name__)
                if known is not None:
                    code, message = known
                else:
                    message = getattr(exc, "public_message", "Operation failed")
                job.error = {"code": str(code), "message": str(message), "exception_type": type(exc).__name__}
            job.state = JobState.FAILED
            if self._repository is not None:
                self._repository.finish(job.job_id, job.state.value, error=job.error)

    def report_progress(self, job_id: str, progress: float) -> bool:
        """Update a known job progress value, clamped to the 0–1 range."""
        job = self.get(job_id)
        if job is None or job.state is not JobState.RUNNING:
            return False
        job.progress = min(1.0, max(0.0, progress))
        if self._repository is not None:
            self._repository.set_progress(job_id, job.progress, job.message)
        return True

    def report_message(self, job_id: str, message: str) -> bool:
        job = self.get(job_id)
        if job is None or job.state is not JobState.RUNNING:
            return False
        job.message = message
        if self._repository is not None:
            self._repository.set_message(job_id, message)
        return True

    def report_checkpoint(self, job_id: str, checkpoint: dict[str, Any]) -> bool:
        job = self.get(job_id)
        if job is None or job.state is not JobState.RUNNING or self._repository is None:
            return False
        self._repository.set_checkpoint(job_id, checkpoint)
        return True

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if job is None:
            return self._repository.get(job_id) if self._repository is not None else None
        snapshot = {
            "job_id": job.job_id,
            "state": job.state.value,
            "job_type": job.job_type,
            "resumable": job.resumable,
            "progress": job.progress,
            "message": job.message,
            "result": job.result,
            "error": job.error,
        }
        if self._repository is not None:
            persisted = self._repository.get(job_id)
            if persisted is not None:
                snapshot["checkpoint"] = persisted["checkpoint"]
                snapshot["attempts"] = persisted["attempts"]
        return snapshot

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def is_idle(self) -> bool:
        """A terminal state alone is insufficient: its final database write may still run."""
        with self._lock:
            return all(job.future is not None and job.future.done()
                       and job.task is not None and job.task.done()
                       for job in self._jobs.values())

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.state not in {JobState.PENDING, JobState.RUNNING}:
            return False
        if job.task is None and job.future is not None:
            if not job.future.cancel():
                self._runtime.submit(self._cancel_future_job(job))
                return True
            job.state = JobState.CANCELLED
            if self._repository is not None:
                self._repository.finish(job.job_id, job.state.value)
        elif job.task is not None:
            self._runtime.submit(self._cancel(job.task))
        else:
            return False
        return True

    async def _cancel_future_job(self, job: Job) -> None:
        while job.task is None and job.state in {JobState.PENDING, JobState.RUNNING}:
            await asyncio.sleep(0.01)
        if job.task is not None and not job.task.done():
            job.task.cancel()

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._repository is not None:
            return self._repository.list(limit)
        with self._lock:
            job_ids = list(self._jobs)[-limit:]
        snapshots: list[dict[str, Any]] = []
        for job_id in job_ids:
            snapshot = self.snapshot(job_id)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    @staticmethod
    async def _cancel(task: asyncio.Task[Any]) -> None:
        task.cancel()
