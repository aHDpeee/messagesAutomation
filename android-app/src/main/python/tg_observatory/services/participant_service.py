"""Telegram participant collection, public username lookup and snapshots."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tg_observatory.exceptions import AuthenticationRequired, InvalidRequest
from tg_observatory.services.account_service import credentials_from
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.participants import ParticipantRepository
from tg_observatory.telegram.client import TelegramGateway


class ParticipantService:
    def __init__(
        self,
        gateway: TelegramGateway,
        accounts: AccountRepository,
        participants: ParticipantRepository,
        report_message: Callable[[str], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._accounts = accounts
        self._participants = participants
        self._report = report_message or (lambda _: None)

    async def capture(self, params: dict[str, Any]) -> dict[str, Any]:
        api_id, api_hash = credentials_from(params)
        dialog_id = _positive_or_signed_id(params.get("dialog_id"))
        profile = await self._gateway.account_status(api_id, api_hash)
        if profile is None:
            raise AuthenticationRequired()
        self._accounts.save_profile(profile)
        account_id = self._accounts.local_id(profile.telegram_id)
        if account_id is None:
            raise RuntimeError("Account profile could not be saved")
        self._report("Reading participants allowed by Telegram")
        snapshot_id, captured_at, local_dialog_id = self._participants.begin_capture(account_id, dialog_id)
        batch: list[dict[str, Any]] = []
        async for user in self._gateway.iter_participants(api_id, api_hash, dialog_id):
            batch.append(user)
            if len(batch) == 200:
                self._participants.add_members(snapshot_id, local_dialog_id, captured_at, batch)
                batch.clear()
                self._report(f"Collected {self._participants.count(snapshot_id)} participants")
        if batch:
            self._participants.add_members(snapshot_id, local_dialog_id, captured_at, batch)
        self._participants.finish_capture(snapshot_id)
        count = self._participants.count(snapshot_id)
        self._report(f"Saved participant snapshot with {count} members")
        return {"snapshot_id": snapshot_id, "captured_at": captured_at, "count": count}

    async def resolve_username(self, params: dict[str, Any]) -> dict[str, Any]:
        api_id, api_hash = credentials_from(params)
        username = str(params.get("username", "")).strip()
        if not username or len(username) > 64:
            raise InvalidRequest("Enter a public Telegram username.")
        profile = await self._gateway.account_status(api_id, api_hash)
        if profile is None:
            raise AuthenticationRequired()
        return await self._gateway.resolve_username(api_id, api_hash, username)

    def latest(self, params: dict[str, Any]) -> dict[str, Any]:
        profile = self._accounts.get_profile()
        if profile is None:
            return {"snapshot": None, "items": []}
        account_id = self._accounts.local_id(profile.telegram_id)
        if account_id is None:
            return {"snapshot": None, "items": []}
        return self._participants.latest(account_id, _positive_or_signed_id(params.get("dialog_id")))

    def compare(self, params: dict[str, Any]) -> dict[str, Any]:
        values = params.get("dialog_ids")
        if not isinstance(values, list) or not 2 <= len(values) <= 20:
            raise InvalidRequest("Select between 2 and 20 groups to compare.")
        dialog_ids = [_positive_or_signed_id(value) for value in values]
        if len(set(dialog_ids)) != len(dialog_ids):
            raise InvalidRequest("Each group can only be selected once.")
        profile = self._accounts.get_profile()
        if profile is None:
            raise AuthenticationRequired()
        account_id = self._accounts.local_id(profile.telegram_id)
        if account_id is None:
            raise AuthenticationRequired()
        return self._participants.compare_latest(account_id, dialog_ids)


def _positive_or_signed_id(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise InvalidRequest("dialog_id must be an integer.") from error
    if result == 0:
        raise InvalidRequest("dialog_id must be a nonzero Telegram ID.")
    return result
