"""Stable JSON request/response API exposed to Android and terminal frontends."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine

from tg_observatory.exceptions import InvalidRequest, ObservatoryError
from tg_observatory.models.health import HealthStatus
from tg_observatory.runtime.jobs import AsyncRuntime, JobManager
from tg_observatory.services.account_service import AccountService
from tg_observatory.services.dialog_service import DialogService
from tg_observatory.services.dashboard_service import DashboardService
from tg_observatory.services.message_service import MessageService
from tg_observatory.services.export_service import ExportService
from tg_observatory.services.participant_service import ParticipantService
from tg_observatory.services.analytics_service import AnalyticsService
from tg_observatory.services.media_service import MediaService
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.dialogs import DialogRepository
from tg_observatory.storage.repositories.messages import MessageRepository
from tg_observatory.storage.repositories.jobs import JobRepository
from tg_observatory.storage.repositories.exports import ExportRepository
from tg_observatory.storage.repositories.participants import ParticipantRepository
from tg_observatory.storage.repositories.analytics import AnalyticsRepository
from tg_observatory.storage.repositories.media import MediaRepository
from tg_observatory.telegram.client import TelegramGateway


def health_check() -> dict[str, str]:
    """Return basic runtime status without making network requests."""
    return HealthStatus(status="ok", runtime="python", version="3.14").to_dict()


async def _between_export_pages() -> None:
    await asyncio.sleep(0.5)


class ObservatoryApi:
    """Narrow operation allow-list and shared service composition root.

    Kotlin calls ``dispatch`` with JSON and receives JSON. Telegram operations are
    submitted to one persistent event loop and polled by job ID.
    """

    def __init__(
        self,
        data_dir: str,
        client_factory: Callable[..., Any] | None = None,
        session_store: Any | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._session_dir = self._data_dir / "telegram"
        self._session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._database = Database(self._data_dir / "observatory.sqlite")
        self._database.migrate()
        self._accounts = AccountRepository(self._database)
        self._dialogs = DialogRepository(self._database)
        self._messages = MessageRepository(self._database)
        self._participants = ParticipantRepository(self._database)
        self._analytics = AnalyticsRepository(self._database)
        self._media = MediaRepository(self._database)
        self._exports = ExportRepository(self._database)
        self._export_dir = self._data_dir / "exports"
        self._export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._media_dir = self._data_dir / "media"
        self._media_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._job_repository = JobRepository(self._database)
        self._job_repository.recover_interrupted()
        self._runtime = AsyncRuntime()
        self._jobs = JobManager(self._runtime, self._job_repository, self._data_dir / "telegram-cooldown.json")
        self._gateway = TelegramGateway(
            self._session_dir / "account", client_factory=client_factory,
            session_store=session_store,
        )

    def close(self) -> None:
        """Release the Telegram transport, asyncio loop and SQLite connection."""
        try:
            self._runtime.submit(self._gateway.disconnect()).result(timeout=5)
        finally:
            self._runtime.close()
            self._database.close()

    def close_if_idle(self) -> bool:
        """Close only after all submitted work has finished its cleanup.

        The caller must serialize this check with dispatching new jobs.
        """
        if not self._jobs.is_idle():
            return False
        self.close()
        return True

    def dispatch(self, request_json: str) -> str:
        """Execute an allow-listed request and return a JSON response envelope."""
        try:
            request = json.loads(request_json)
            if not isinstance(request, dict):
                raise InvalidRequest("Request must be a JSON object.")
            if type(request.get("schema_version", 1)) is not int or request.get("schema_version", 1) != 1:
                raise InvalidRequest("Unsupported bridge schema version. Expected 1.")
            operation = request.get("operation")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise InvalidRequest("Request params must be a JSON object.")
            response = self._dispatch(str(operation), params)
            return json.dumps({"schema_version": 1, "ok": True, "data": response}, separators=(",", ":"))
        except ObservatoryError as error:
            return _error_json(error.code, error.public_message)
        except (json.JSONDecodeError, TypeError, ValueError):
            return _error_json("invalid_request", "Request JSON is invalid.")
        except Exception:
            return _error_json("internal_error", "The request could not be completed.")

    def _dispatch(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if operation == "health.check":
            return health_check()
        if operation == "dashboard.get":
            return DashboardService(self._database, self._accounts, self._dialogs).get()
        if operation == "account.send_code":
            return self._submit("account.send_code", params)
        if operation in {"account.qr_start", "account.qr_wait"}:
            return self._submit(operation, params)
        if operation == "account.verify_code":
            return self._submit("account.verify_code", params)
        if operation == "account.verify_password":
            return self._submit("account.verify_password", params)
        if operation == "account.status":
            return self._submit("account.status", params)
        if operation == "account.logout":
            return self._submit("account.logout", params)
        if operation == "dialogs.sync":
            return self._submit("dialogs.sync", params)
        if operation == "dialogs.add":
            return self._submit("dialogs.add", params)
        if operation == "dialogs.remove":
            return self._submit("dialogs.remove", params)
        if operation == "dialogs.check_invite":
            return self._submit("dialogs.check_invite", params)
        if operation == "dialogs.folders":
            return self._submit("dialogs.folders", params)
        if operation == "messages.sync":
            return self._submit("messages.sync", params)
        if operation == "messages.preview_range":
            return self._submit("messages.preview_range", params)
        if operation == "messages.export_jsonl":
            return self._submit("messages.export_jsonl", params)
        if operation in {"messages.export", "members.export", "members.export_csv", "inventory.export", "analytics.export"}:
            return self._submit(operation, params)
        if operation == "participants.capture":
            return self._submit("participants.capture", params)
        if operation == "users.resolve":
            return self._submit("users.resolve", params)
        if operation == "media.download_dialog":
            return self._submit("media.download_dialog", params)
        if operation == "media.list":
            profile = self._accounts.get_profile()
            if profile is None:
                return {"items": [], "storage_bytes": 0}
            account_id = self._accounts.local_id(profile.telegram_id)
            if account_id is None:
                return {"items": [], "storage_bytes": 0}
            return {
                "items": self._media.list_completed(account_id),
                "storage_bytes": self._media.storage_usage(account_id),
            }
        if operation == "media.delete":
            profile = self._accounts.get_profile()
            account_id = self._accounts.local_id(profile.telegram_id) if profile is not None else None
            if account_id is None:
                raise InvalidRequest("No local Telegram account is available for media cleanup.")
            media_service = MediaService(
                self._gateway, self._accounts, self._media, self._media_dir
            )
            return media_service.delete_local(params.get("media_id"), account_id)
        if operation in {"dialogs.open", "dialogs.favorite"}:
            dialog_service = DialogService(self._gateway, self._accounts, self._dialogs)
            return dialog_service.interact(params, favorite=operation == "dialogs.favorite")
        if operation == "dialogs.list":
            dialog_service = DialogService(self._gateway, self._accounts, self._dialogs)
            return dialog_service.list_local(params)
        if operation == "messages.list":
            message_service = MessageService(
                self._gateway, self._database, self._accounts, self._dialogs, self._messages
            )
            return message_service.list_local(params)
        if operation == "messages.range":
            message_service = MessageService(
                self._gateway, self._database, self._accounts, self._dialogs, self._messages
            )
            return message_service.range_local(params)
        if operation == "participants.list":
            participant_service = ParticipantService(
                self._gateway, self._accounts, self._participants
            )
            return participant_service.latest(params)
        if operation == "groups.compare":
            participant_service = ParticipantService(
                self._gateway, self._accounts, self._participants
            )
            return participant_service.compare(params)
        if operation == "analytics.activity":
            analytics_service = AnalyticsService(self._database, self._accounts, self._analytics)
            return analytics_service.activity(params)
        if operation == "jobs.get":
            job_id = str(params.get("job_id", ""))
            snapshot = self._jobs.snapshot(job_id)
            if snapshot is None:
                raise InvalidRequest("Job was not found.")
            return snapshot
        if operation == "jobs.list":
            return {"items": self._jobs.list(_limit(params.get("limit", 100)))}
        if operation == "jobs.resume":
            job_id = str(params.get("job_id", ""))
            previous = self._job_repository.get(job_id)
            if previous is None or previous["job_type"] not in {
                "dialogs.sync", "messages.sync", "messages.export_jsonl", "messages.export"
            }:
                raise InvalidRequest("This job cannot be resumed.")
            resume_params = dict(previous["params"])
            if previous["job_type"] in {"dialogs.sync", "messages.sync"} or resume_params.get("fetch_source"):
                resume_params["api_id"] = params.get("api_id")
                resume_params["api_hash"] = params.get("api_hash")
            if previous["checkpoint"] is not None:
                resume_params["_checkpoint"] = previous["checkpoint"]
            return self._submit(previous["job_type"], resume_params, job_id=job_id, retry=True)
        if operation == "jobs.cancel":
            job_id = str(params.get("job_id", ""))
            if not self._jobs.cancel(job_id):
                raise InvalidRequest("Job cannot be cancelled.")
            return {"job_id": job_id, "state": "cancelling"}
        raise InvalidRequest("Unsupported operation.")

    def _submit(
        self, operation: str, params: dict[str, Any], *, job_id: str | None = None,
        retry: bool = False,
    ) -> dict[str, Any]:
        job_id = job_id or uuid.uuid4().hex

        def report(message: str) -> None:
            self._jobs.report_message(job_id, message)

        def checkpoint(value: dict[str, Any]) -> None:
            self._jobs.report_checkpoint(job_id, value)

        def export_progress(value: float, message: str | None) -> None:
            if message:
                self._jobs.report_message(job_id, message)
            self._jobs.report_progress(job_id, value)

        def media_progress(value: float) -> None:
            self._jobs.report_progress(job_id, value)

        accounts = AccountService(self._gateway, self._accounts, report)
        dialogs = DialogService(self._gateway, self._accounts, self._dialogs, report)
        messages = MessageService(
            self._gateway, self._database, self._accounts, self._dialogs, self._messages,
            report, checkpoint=checkpoint, initial_checkpoint=params.get("_checkpoint"),
        )
        exporter = ExportService(
            self._database,
            self._accounts,
            self._messages,
            self._exports,
            self._export_dir,
            report_message=report,
            report_progress=export_progress,
            save_checkpoint=checkpoint,
        )
        participants = ParticipantService(
            self._gateway, self._accounts, self._participants, report
        )
        media = MediaService(
            self._gateway,
            self._accounts,
            self._media,
            self._media_dir,
            report,
            report_progress=media_progress,
        )
        async def remove_dialog() -> dict[str, Any]:
            from tg_observatory.services.account_service import credentials_from
            api_id, api_hash = credentials_from(params)
            profile = await self._gateway.account_status(api_id, api_hash)
            if profile is None or str(profile.telegram_id) != str(params.get("expected_account_id", "")):
                raise InvalidRequest("The Telegram account changed; refresh the catalog first.")
            dialog_id = int(params.get("dialog_id", 0))
            if not dialog_id or dialog_id == profile.telegram_id:
                raise InvalidRequest("This chat cannot be removed here.")
            client = await self._gateway._connected_client(api_id, api_hash)
            peer = await self._gateway._input_peer(client, dialog_id)
            await client.delete_dialog(peer, revoke=False)
            return {"status": "removed", "dialog_id": dialog_id}

        async def fetch_export_source() -> None:
            saved = params.get("_checkpoint") or {}
            if params.get("fetch_source") and saved and saved.get("source_scope_version") != 1:
                raise InvalidRequest("This older export cannot be resumed safely. Start a new export.")
            if not params.get("fetch_source") or saved.get("bytes") is not None:
                return
            after_id = saved.get("source_after_id")
            fetched = int(saved.get("source_count", 0))
            while True:
                page = await messages.preview_range({**params, "after_id": after_id, "limit": 100})
                # Remember exactly what this Telegram fetch returned. Previously
                # cached or deleted messages must not leak into this export.
                with self._database.transaction() as connection:
                    connection.executemany(
                        "INSERT OR IGNORE INTO export_source_messages(export_job_id, telegram_message_id) VALUES (?, ?)",
                        [(job_id, int(item["message_id"])) for item in page["items"]],
                    )
                fetched += len(page["items"])
                next_id = page.get("next_after_id")
                checkpoint({"stage": "fetching", "source_after_id": next_id, "source_count": fetched, "source_scope_version": 1})
                report(f"Retrieved {fetched} messages from Telegram")
                if not page["has_more"]:
                    break
                if next_id is None or next_id == after_id:
                    raise InvalidRequest("Telegram did not advance the message cursor. Retry the export.")
                after_id = next_id
                # Pace successive Telegram pages while avoiding a fixed delay
                # after a chat whose selected range is empty or already complete.
                await _between_export_pages()

        async def export_messages() -> dict[str, Any]:
            await fetch_export_source()
            saved = params.get("_checkpoint") or {}
            return await exporter.export_dialog(job_id, params, saved if saved.get("bytes") is not None else None)

        async def add_dialog() -> dict[str, Any]:
            from tg_observatory.services.account_service import credentials_from
            from tg_observatory.telegram.chat_reference import resolve_and_join
            api_id, api_hash = credentials_from(params)
            profile = await self._gateway.account_status(api_id, api_hash)
            if profile is None:
                raise InvalidRequest("Sign in to Telegram first.")
            if str(profile.telegram_id) != str(params.get("expected_account_id", "")):
                raise InvalidRequest("The Telegram account changed; refresh the catalog first.")
            self._accounts.save_profile(profile)
            account_id = self._accounts.local_id(profile.telegram_id)
            local = []
            offset = 0
            while True:
                page = self._dialogs.list(account_id, limit=500, offset=offset)
                local.extend({"id": str(row["telegram_id"]), "title": row["title"], "username": row["username"], "type": row["kind"]} for row in page)
                if len(page) < 500:
                    break
                offset += len(page)
            client = await self._gateway._connected_client(api_id, api_hash)
            try:
                return await resolve_and_join(client, str(params.get("reference", "")), local, verify_membership=params.get("verify_membership") is True)
            except ValueError as error:
                raise InvalidRequest(str(error)) from error

        async def check_invite() -> dict[str, Any]:
            from tg_observatory.services.account_service import credentials_from
            from tg_observatory.telegram.chat_reference import check_invite_membership
            api_id, api_hash = credentials_from(params)
            profile = await self._gateway.account_status(api_id, api_hash)
            if profile is None or str(profile.telegram_id) != str(params.get("expected_account_id", "")):
                raise InvalidRequest("The Telegram account changed; refresh the catalog first.")
            client = await self._gateway._connected_client(api_id, api_hash)
            try:
                return await check_invite_membership(client, str(params.get("reference", "")))
            except ValueError as error:
                raise InvalidRequest(str(error)) from error

        async def list_folders() -> dict[str, Any]:
            from tg_observatory.services.account_service import credentials_from
            from tg_observatory.telegram.folders import discover
            api_id, api_hash = credentials_from(params)
            profile = await self._gateway.account_status(api_id, api_hash)
            if profile is None or str(profile.telegram_id) != str(params.get("expected_account_id", "")):
                raise InvalidRequest("The Telegram account changed; refresh the catalog first.")
            client = await self._gateway._connected_client(api_id, api_hash)
            return {"folders": await discover(client)}

        async def export_report() -> dict[str, Any]:
            if operation == "members.export_csv":
                from tg_observatory.services.member_csv_service import export_csv
                return await export_csv(self._gateway, self._database, self._accounts, self._export_dir, job_id, params, report)
            if operation == "members.export":
                from tg_observatory.services.member_export_service import MemberExportService
                service = MemberExportService(
                    self._gateway, self._database, self._accounts, self._participants,
                    self._export_dir, exports=self._exports, report_message=report, report_progress=export_progress,
                )
                return await service.export(job_id, params)
            if operation == "inventory.export":
                from tg_observatory.services.inventory_export_service import InventoryExportService
                inventory = InventoryExportService(self._database, self._accounts, self._dialogs,
                                                   self._export_dir, exports=self._exports,
                                                   report_progress=export_progress)
                return await inventory.export(job_id, params)
            from tg_observatory.services.analytics_export_service import AnalyticsExportService
            await fetch_export_source()
            analytics = AnalyticsExportService(self._database, self._accounts, self._messages,
                                               self._export_dir, exports=self._exports,
                                               report_progress=export_progress)
            return await analytics.export(job_id, params)

        coroutines: dict[str, Callable[[], Coroutine[Any, Any, Any]]] = {
            "account.send_code": lambda: accounts.request_code(params),
            "account.qr_start": lambda: accounts.start_qr(params),
            "account.qr_wait": lambda: accounts.finish_qr(),
            "account.verify_code": lambda: accounts.submit_code(params),
            "account.verify_password": lambda: accounts.submit_password(params),
            "account.status": lambda: accounts.status(params),
            "account.logout": lambda: accounts.logout(),
            "dialogs.sync": lambda: dialogs.sync(params),
            "dialogs.remove": remove_dialog,
            "dialogs.add": add_dialog,
            "dialogs.check_invite": check_invite,
            "dialogs.folders": list_folders,
            "messages.sync": lambda: messages.sync(params),
            "messages.preview_range": lambda: messages.preview_range(params),
            "messages.export_jsonl": lambda: exporter.export_dialog_jsonl(
                job_id, params, params.get("_checkpoint")
            ),
            "messages.export": export_messages,
            "members.export": export_report,
            "members.export_csv": export_report,
            "inventory.export": export_report,
            "analytics.export": export_report,
            "participants.capture": lambda: participants.capture(params),
            "users.resolve": lambda: participants.resolve_username(params),
            "media.download_dialog": lambda: media.download_dialog(params),
        }
        factory = coroutines.get(operation)
        if factory is None:
            raise InvalidRequest("Unsupported operation.")
        resumable = operation in {
            "dialogs.sync", "messages.sync", "messages.export_jsonl", "messages.export"
        }
        stored_params = _persistable_params(operation, params)
        self._jobs.submit(
            factory(), job_id=job_id, job_type=operation, params=stored_params,
            resumable=resumable, retry=retry,
        )
        return {"job_id": job_id, "state": "pending"}


def _persistable_params(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    """Persist only non-secret arguments needed to reconstruct sync work."""
    if operation == "dialogs.sync":
        return {key: params[key] for key in ("recent_only",) if key in params}
    report_keys = {
        "members.export": ("dialog_id", "include_profiles", "source"),
        "inventory.export": ("dialog_ids", "hide_channels", "include_hidden_channels", "include_categories"),
        "analytics.export": ("dialog_id", "start_message_id", "end_message_id", "period_start", "period_end", "stop_words", "min_word_length", "fetch_source"),
    }
    if operation in report_keys:
        return {key: params[key] for key in report_keys[operation] if key in params}
    if operation in {"messages.sync", "messages.export_jsonl", "messages.export"}:
        keys: tuple[str, ...]
        if operation == "messages.sync":
            keys = ("dialog_id", "limit")
        elif operation == "messages.export":
            keys = ("dialog_id", "format", "start_message_id", "end_message_id", "period_start", "period_end", "fetch_source", "expected_account_id")
        else:
            keys = ("dialog_id",)
        return {key: params[key] for key in keys if key in params}
    return {}


def _limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as error:
        raise InvalidRequest("limit must be an integer.") from error
    if not 1 <= limit <= 100:
        raise InvalidRequest("limit must be between 1 and 100.")
    return limit


def _error_json(code: str, message: str) -> str:
    return json.dumps(
        {"schema_version": 1, "ok": False, "error": {"code": code, "message": message}},
        separators=(",", ":"),
    )
