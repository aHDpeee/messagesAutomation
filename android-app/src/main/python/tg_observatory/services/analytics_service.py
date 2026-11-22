"""Analytics over local data only; no Telegram network calls are made."""

from __future__ import annotations

import re
from typing import Any

from tg_observatory.exceptions import InvalidRequest
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.analytics import AnalyticsRepository

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AnalyticsService:
    def __init__(
        self, database: Database, accounts: AccountRepository, analytics: AnalyticsRepository
    ) -> None:
        self._database = database
        self._accounts = accounts
        self._analytics = analytics

    def activity(self, params: dict[str, Any]) -> dict[str, Any]:
        profile = self._accounts.get_profile()
        if profile is None:
            return _empty()
        account_id = self._accounts.local_id(profile.telegram_id)
        if account_id is None:
            return _empty()
        dialog_id = params.get("dialog_id")
        if dialog_id is not None:
            try:
                dialog_id = int(dialog_id)
            except (TypeError, ValueError) as error:
                raise InvalidRequest("dialog_id must be an integer.") from error
            if dialog_id == 0:
                raise InvalidRequest("dialog_id must be a nonzero Telegram ID.")
        start_date = _date(params.get("start_date"), "start_date")
        end_date = _date(params.get("end_date"), "end_date")
        if start_date and end_date and start_date > end_date:
            raise InvalidRequest("start_date must not be after end_date.")
        return self._analytics.activity(
            account_id,
            dialog_telegram_id=dialog_id,
            start_date=start_date,
            end_date=end_date,
        )


def _date(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    result = str(value)
    if not _DATE.fullmatch(result):
        raise InvalidRequest(f"{field} must use YYYY-MM-DD format.")
    try:
        from datetime import date

        date.fromisoformat(result)
    except ValueError as error:
        raise InvalidRequest(f"{field} is not a valid date.") from error
    return result


def _empty() -> dict[str, Any]:
    return {
        "total_messages": 0,
        "unique_senders": 0,
        "replies": 0,
        "forwards": 0,
        "daily": [],
        "by_hour": [],
        "top_senders": [],
        "media": [],
        "source": "local_index",
    }
