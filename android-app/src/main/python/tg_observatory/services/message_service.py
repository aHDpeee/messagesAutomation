"""Incremental message history synchronization and local search."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from tg_observatory.exceptions import AuthenticationRequired, InvalidRequest
from tg_observatory.services.account_service import credentials_from
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.dialogs import DialogRepository
from tg_observatory.storage.repositories.messages import MessageRepository
from tg_observatory.telegram.client import TelegramGateway


class MessageService:
    def __init__(self, gateway: TelegramGateway, database: Database,
                 accounts: AccountRepository, dialogs: DialogRepository,
                 messages: MessageRepository,
                 report_message: Callable[[str], None] | None = None,
                 checkpoint: Callable[[dict[str, Any]], None] | None = None,
                 initial_checkpoint: dict[str, Any] | None = None) -> None:
        self._gateway, self._database = gateway, database
        self._accounts, self._dialogs, self._messages = accounts, dialogs, messages
        self._report = report_message or (lambda _: None)
        self._checkpoint = checkpoint or (lambda _: None)
        self._initial_checkpoint = initial_checkpoint or {}

    async def sync(self, params: dict[str, Any]) -> dict[str, Any]:
        api_id, api_hash = credentials_from(params)
        account = await self._gateway.account_status(api_id, api_hash)
        if account is None:
            raise AuthenticationRequired()
        telegram_dialog_id = _nonzero_int(params.get("dialog_id"), "dialog_id")
        limit = _positive_int(params.get("limit", 1000), "limit")
        if limit > 100_000:
            raise InvalidRequest("Message sync limit cannot exceed 100000.")
        account_id = self._accounts.local_id(account.telegram_id)
        if account_id is None:
            raise RuntimeError("Account profile is not indexed.")
        dialog = self._database.fetchone(
            "SELECT id FROM dialogs WHERE account_id=? AND telegram_id=?",
            (account_id, telegram_dialog_id),
        )
        if dialog is None:
            raise InvalidRequest("Sync this dialog before indexing its messages.")
        local_id = int(dialog["id"])
        count = 0
        batch: list[dict[str, Any]] = []
        max_id = self._initial_checkpoint.get("max_id")
        async for item in self._gateway.iter_messages(
            api_id, api_hash, telegram_dialog_id, limit, max_id=max_id
        ):
            batch.append(item)
            if len(batch) == 100:
                count += self._messages.upsert_batch(local_id, batch)
                max_id = min(message["message_id"] for message in batch)
                self._checkpoint({"max_id": max_id})
                batch.clear()
                self._report(f"Indexed {count} messages")
        if batch:
            count += self._messages.upsert_batch(local_id, batch)
            max_id = min(message["message_id"] for message in batch)
            self._checkpoint({"max_id": max_id})
        return {"indexed": count, "dialog_id": telegram_dialog_id, "checkpoint": {"max_id": max_id} if max_id else None}

    def list_local(self, params: dict[str, Any]) -> dict[str, Any]:
        telegram_dialog_id = _nonzero_int(params.get("dialog_id"), "dialog_id")
        limit = _positive_int(params.get("limit", 100), "limit")
        if limit > 500:
            raise InvalidRequest("Page size cannot exceed 500.")
        profile = self._accounts.get_profile()
        if profile is None:
            return {"items": []}
        account_id = self._accounts.local_id(profile.telegram_id)
        dialog = self._database.fetchone(
            "SELECT id FROM dialogs WHERE account_id=? AND telegram_id=?",
            (account_id, telegram_dialog_id),
        )
        if dialog is None:
            return {"items": []}
        before = params.get("before_id")
        before_id = _positive_int(before, "before_id") if before is not None else None
        query = str(params.get("query", "")).strip().casefold()
        items = self._messages.list(
            int(dialog["id"]), limit=limit, before_id=before_id, query=query
        )
        return {"items": items}

    async def preview_range(self, params: dict[str, Any]) -> dict[str, Any]:
        """Preview accessible Telegram history without a separate indexing action."""
        api_id, api_hash = credentials_from(params)
        dialog_id = _nonzero_int(params.get("dialog_id"), "dialog_id")
        limit_value = params.get("limit", 100)
        after = params.get("after_id")
        if isinstance(limit_value, (bool, float)) or isinstance(after, (bool, float)):
            raise InvalidRequest("Preview limit and after_id must be integers.")
        limit = _positive_int(limit_value, "limit")
        if limit > 100:
            raise InvalidRequest("Preview page size cannot exceed 100.")
        after_id = _positive_int(after, "after_id") if after is not None else None
        start = _range_timestamp(params.get("period_start"), "period_start")
        end = _range_timestamp(params.get("period_end"), "period_end")
        if start is not None and end is not None and start >= end:
            raise InvalidRequest("Period start must be earlier than period end.")
        profile = await self._gateway.account_status(api_id, api_hash)
        if profile is None:
            raise AuthenticationRequired()
        expected = params.get("expected_account_id")
        if expected is not None and str(expected) != str(profile.telegram_id):
            raise InvalidRequest("The Telegram account changed; refresh and verify the catalog before exporting.")
        self._accounts.save_profile(profile)
        account_id = self._accounts.local_id(profile.telegram_id)
        dialog = self._database.fetchone(
            "SELECT id FROM dialogs WHERE account_id=? AND telegram_id=?", (account_id, dialog_id),
        )
        if dialog is None:
            raise InvalidRequest("Sync dialogs and select an accessible conversation first.")
        self._report("Loading message previews from Telegram")
        page = await self._gateway.preview_range_page(
            api_id, api_hash, dialog_id, after_id=after_id,
            period_start=start, period_end=end, limit=limit,
        )
        # Cache this bounded page for later export; viewing never claims full retrieval.
        self._messages.upsert_batch(int(dialog["id"]), page["items"])
        return page

    def range_local(self, params: dict[str, Any]) -> dict[str, Any]:
        if isinstance(params.get("dialog_id"), (bool, float)):
            raise InvalidRequest("dialog_id must be an integer.")
        dialog_id = _nonzero_int(params.get("dialog_id"), "dialog_id")
        limit_value = params.get("limit", 100)
        if isinstance(limit_value, (bool, float)):
            raise InvalidRequest("limit must be an integer.")
        limit = _positive_int(limit_value, "limit")
        if limit > 500:
            raise InvalidRequest("Page size cannot exceed 500.")
        after = params.get("after_id")
        if isinstance(after, (bool, float)):
            raise InvalidRequest("after_id must be an integer.")
        after_id = _positive_int(after, "after_id") if after is not None else None
        if after_id is not None and after_id >= 2**63:
            raise InvalidRequest("after_id must be a positive 64-bit integer.")
        start = _range_timestamp(params.get("period_start"), "period_start")
        end = _range_timestamp(params.get("period_end"), "period_end")
        if start is not None and end is not None and start >= end:
            raise InvalidRequest("Period start must be earlier than period end.")
        empty = {
            "items": [], "has_more": False, "total": 0, "earliest": None, "latest": None,
            "source": "local", "next_after_id": None,
        }
        profile = self._accounts.get_profile()
        if profile is None:
            return empty
        account_id = self._accounts.local_id(profile.telegram_id)
        dialog = self._database.fetchone(
            "SELECT id FROM dialogs WHERE account_id=? AND telegram_id=?", (account_id, dialog_id),
        )
        if dialog is None:
            return empty
        return self._messages.range_page(
            int(dialog["id"]), after_id=after_id, limit=limit,
            period_start=start.isoformat() if start else None,
            period_end=end.isoformat() if end else None,
        )


def _range_timestamp(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidRequest(f"{field} must be an ISO 8601 timestamp with a timezone.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise InvalidRequest(f"{field} must be an ISO 8601 timestamp with a timezone.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidRequest(f"{field} must include an explicit timezone.")
    return parsed.astimezone(UTC)


def _positive_int(value: Any, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise InvalidRequest(f"{field} must be an integer.") from error
    if parsed < 1:
        raise InvalidRequest(f"{field} must be positive.")
    return parsed


def _nonzero_int(value: Any, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise InvalidRequest(f"{field} must be an integer.") from error
    if parsed == 0 or not -(2**63) <= parsed < 2**63:
        raise InvalidRequest(f"{field} must be a nonzero 64-bit integer.")
    return parsed
