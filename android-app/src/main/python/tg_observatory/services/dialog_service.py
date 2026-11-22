"""Dialog synchronization and local dialog browsing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tg_observatory.exceptions import AuthenticationRequired, InvalidRequest
from tg_observatory.services.account_service import credentials_from
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.dialogs import DialogRepository
from tg_observatory.telegram.client import TelegramGateway


class DialogService:
    def __init__(
        self,
        gateway: TelegramGateway,
        accounts: AccountRepository,
        dialogs: DialogRepository,
        report_message: Callable[[str], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._accounts = accounts
        self._dialogs = dialogs
        self._report_message = report_message or (lambda _: None)

    async def sync(self, params: dict[str, Any]) -> dict[str, Any]:
        recent_only = params.get("recent_only", False)
        if not isinstance(recent_only, bool):
            raise InvalidRequest("recent_only must be a boolean.")
        api_id, api_hash = credentials_from(params)
        profile = await self._gateway.account_status(api_id, api_hash)
        if profile is None:
            raise AuthenticationRequired()
        self._accounts.save_profile(profile)
        account_id = self._accounts.local_id(profile.telegram_id)
        if account_id is None:
            raise RuntimeError("Account profile could not be saved")

        # The first automatic pass must index the full account. Later automatic passes
        # need only the newest dialogs; explicit user syncs still perform a full pass.
        account = self._accounts.dashboard_record()
        needs_initial_index = recent_only and (
            self._dialogs.count(account_id) == 0 or account is None or account["last_sync_at"] is None
        )
        recent_only = recent_only and not needs_initial_index

        batch = []
        total = 0
        synced_ids: list[int] = []
        empty_ids: list[int] = []
        unanswered_bot_ids: list[int] = []
        self._report_message("Refreshing recent Telegram dialogs" if recent_only else "Reading Telegram dialogs")
        iterator = (self._gateway.iter_dialogs(api_id, api_hash, limit=100) if recent_only
                    else self._gateway.iter_dialogs(api_id, api_hash))
        async for dialog in iterator:
            batch.append(dialog)
            synced_ids.append(dialog.telegram_id)
            if dialog.last_message_present is False:
                empty_ids.append(dialog.telegram_id)
            elif dialog.kind == "bot" and dialog.last_message_out is True:
                unanswered_bot_ids.append(dialog.telegram_id)
            if len(batch) >= 100:
                total += self._dialogs.sync(account_id, batch)
                batch.clear()
                self._report_message(f"Indexed {total} dialogs")
        if batch:
            total += self._dialogs.sync(account_id, batch)
        if not recent_only:
            self._accounts.mark_synced(profile.telegram_id)
        return {"synced": total, "synced_ids": synced_ids, "empty_ids": empty_ids, "unanswered_bot_ids": unanswered_bot_ids}

    def list_local(self, params: dict[str, Any]) -> dict[str, Any]:
        profile = self._accounts.get_profile()
        if profile is None:
            return {"items": [], "total_by_kind": {}}
        account_id = self._accounts.local_id(profile.telegram_id)
        if account_id is None:
            return {"items": [], "total_by_kind": {}}
        kind = params.get("kind")
        if kind not in (None, "", "private", "group", "channel", "bot", "unknown"):
            raise InvalidRequest("Unsupported dialog type filter.")
        list_name = params.get("list_name")
        if list_name not in (None, "recent", "personal", "communities"):
            raise InvalidRequest("Unsupported explorer list.")
        hide_channels = params.get("hide_channels", False)
        if not isinstance(hide_channels, bool):
            raise InvalidRequest("hide_channels must be a boolean.")
        limit = _integer(params.get("limit", 100), "limit", minimum=1)
        offset = _integer(params.get("offset", 0), "offset", minimum=0)
        return {
            "items": self._dialogs.list(
                account_id,
                query=str(params.get("query", "")).strip(),
                kind=str(kind) if kind else None,
                sort=str(params.get("sort", "activity")),
                limit=limit,
                offset=offset,
                list_name=list_name,
                hide_channels=hide_channels,
            ),
            "total_by_kind": self._dialogs.count_by_kind(account_id),
        }

    def interact(self, params: dict[str, Any], *, favorite: bool = False) -> dict[str, Any]:
        profile = self._accounts.get_profile()
        account_id = self._accounts.local_id(profile.telegram_id) if profile else None
        if account_id is None:
            raise InvalidRequest("No local Telegram account is available.")
        dialog_id = params.get("dialog_id")
        if dialog_id is None or isinstance(dialog_id, bool):
            raise InvalidRequest("dialog_id must be an integer.")
        try:
            dialog_id = int(dialog_id)
        except (TypeError, ValueError) as error:
            raise InvalidRequest("dialog_id must be an integer.") from error
        if favorite:
            value = params.get("favorite")
            if not isinstance(value, bool):
                raise InvalidRequest("favorite must be a boolean.")
            updated = self._dialogs.set_favorite(account_id, dialog_id, value)
        else:
            updated = self._dialogs.mark_opened(account_id, dialog_id)
        if not updated:
            raise InvalidRequest("Dialog is not indexed for this account.")
        return {"dialog_id": dialog_id, "updated": True, "provenance": "observatory"}


def _integer(value: Any, field: str, *, minimum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise InvalidRequest(f"{field} must be an integer.") from error
    if result < minimum:
        raise InvalidRequest(f"{field} must be at least {minimum}.")
    return result
