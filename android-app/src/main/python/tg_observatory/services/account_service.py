"""Telegram account authentication workflow."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from tg_observatory.exceptions import InvalidCredentials, InvalidRequest
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.telegram.client import TelegramGateway


def credentials_from(params: dict[str, Any]) -> tuple[int, str]:
    try:
        api_id = int(params["api_id"])
        api_hash = str(params["api_hash"]).strip()
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidCredentials() from error
    if api_id <= 0 or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise InvalidCredentials()
    return api_id, api_hash


def mask_phone(phone: str) -> str:
    digits = "".join(character for character in phone if character.isdigit())
    return f"…{digits[-4:]}" if digits else "hidden"


class AccountService:
    def __init__(
        self,
        gateway: TelegramGateway,
        accounts: AccountRepository,
        report_message: Callable[[str], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._accounts = accounts
        self._report_message = report_message or (lambda _: None)

    async def request_code(self, params: dict[str, Any]) -> dict[str, Any]:
        api_id, api_hash = credentials_from(params)
        phone = str(params.get("phone", "")).strip()
        if not phone.startswith("+") or len(phone) < 7:
            raise InvalidRequest("Enter a valid phone number with its country code.")
        self._report_message("Requesting Telegram verification code")
        result = await self._gateway.request_code(api_id, api_hash, phone)
        return {"status": "code_sent", "phone_hint": mask_phone(phone), **result}

    async def submit_code(self, params: dict[str, Any]) -> dict[str, Any]:
        api_id, api_hash = credentials_from(params)
        code = str(params.get("code", "")).strip()
        if not code:
            raise InvalidRequest("Enter the verification code.")
        self._report_message("Verifying Telegram code")
        status, profile = await self._gateway.sign_in_code(api_id, api_hash, code)
        if status == "password_required":
            return {"status": status}
        assert profile is not None
        self._accounts.save_profile(profile)
        return {"status": "authenticated", "account": profile.to_dict()}

    async def start_qr(self, params: dict[str, Any]) -> dict[str, Any]:
        api_id, api_hash = credentials_from(params)
        return await self._gateway.start_qr_login(api_id, api_hash)

    async def finish_qr(self) -> dict[str, Any]:
        status, profile = await self._gateway.finish_qr_login()
        if profile is not None:
            self._accounts.save_profile(profile)
        return {"status": status}

    async def submit_password(self, params: dict[str, Any]) -> dict[str, Any]:
        api_id, api_hash = credentials_from(params)
        password = str(params.get("password", ""))
        if not password:
            raise InvalidRequest("Enter your Telegram two-step verification password.")
        self._report_message("Verifying Telegram password")
        profile = await self._gateway.sign_in_password(api_id, api_hash, password)
        self._accounts.save_profile(profile)
        return {"status": "authenticated", "account": profile.to_dict()}

    async def status(self, params: dict[str, Any]) -> dict[str, Any]:
        api_id, api_hash = credentials_from(params)
        self._report_message("Checking Telegram session")
        profile = await self._gateway.account_status(api_id, api_hash)
        if profile is None:
            return {"status": "signed_out", "account": None}
        self._accounts.save_profile(profile)
        return {"status": "authenticated", "account": profile.to_dict()}

    async def logout(self) -> dict[str, str]:
        await self._gateway.logout()
        return {
            "status": "signed_out",
            "remote_revocation": "completed"
            if self._gateway.remote_logout_succeeded
            else "not_confirmed",
        }
