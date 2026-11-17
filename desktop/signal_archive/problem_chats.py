"""Derive the "problem chats" lists from the catalog and join failures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


STALE_TYPES = {"chat", "group", "channel", "bot"}


def problem_days(state: dict) -> int:
    """Return the configured threshold, defaulting to 30 days."""
    try:
        value = int(state.get("problem_chat_days", 30))
    except (TypeError, ValueError):
        return 30
    return min(3650, max(1, value))


def _last_date(record: dict) -> datetime | None:
    text = str(record.get("last_message_date") or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _groups(state: dict, ident: str) -> list[str]:
    return [str(block.get("name", "")) for block in state.get("blocks", {}).values()
            if ident in [str(item) for item in block.get("chat_ids", [])] and block.get("name")]


def _entry(state: dict, record: dict, reason: str, message: str) -> dict[str, Any]:
    ident = str(record.get("id", ""))
    return {
        "id": ident,
        "title": str(record.get("title") or ident or "Без названия"),
        "type": str(record.get("type") or "chat"),
        "reason": reason,
        "message": message,
        "groups": _groups(state, ident),
        # Only numeric group IDs can be used for the bulk member export.
        "selectable": str(record.get("type")) == "group" and ident.lstrip("-").isdigit(),
    }


def classify_problems(state: dict, *, now: datetime | None = None) -> dict[str, Any]:
    """Return urgent and stale chats; stale chats never duplicate urgent rows."""
    now = now or datetime.now(timezone.utc)
    days = problem_days(state)
    cutoff = now - timedelta(days=days)
    dialogs = {str(d.get("id")): d for d in state.get("dialogs", [])}
    urgent: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    seen: set[str] = set()

    for ident, stored in state.get("problem_chats", {}).items():
        key = str(ident)
        dialog = dialogs.get(key)
        # A later refresh with message history means the problem was resolved.
        if dialog and _last_date(dialog) is not None:
            continue
        record = dict(dialog or {})
        record.setdefault("id", key)
        record.setdefault("title", stored.get("title") or key)
        record.setdefault("type", stored.get("type") or "group")
        reason = str(stored.get("reason") or "join_problem")
        message = str(stored.get("message") or "Требует внимания.")
        urgent.append(_entry(state, record, reason, message))
        seen.add(key)

    for ident, dialog in dialogs.items():
        if ident in seen or not dialog.get("title"):
            continue
        last = _last_date(dialog)
        kind = str(dialog.get("type") or "chat")
        if kind == "bot" and dialog.get("last_message_out") is True:
            urgent.append(_entry(state, dialog, "bot_awaiting_reply", "Последнее сообщение отправлено боту; ответа после него нет."))
            seen.add(ident)
        elif last is None and dialog.get("history_known") is True:
            if kind == "bot":
                urgent.append(_entry(state, dialog, "bot_no_reply", "Бот не ответил: сообщений нет."))
            else:
                urgent.append(_entry(state, dialog, "no_messages", "Нет сообщений."))
            seen.add(ident)
        elif last is not None and kind in STALE_TYPES and last < cutoff:
            stale.append(_entry(state, dialog, "stale", f"Сообщений нет уже {days} или более дней."))
            seen.add(ident)

    return {"days": days, "urgent": urgent, "stale": stale, "all": urgent + stale}


def record_problem(state: dict, ident: Any, *, title: str, kind: str,
                   reason: str, message: str) -> None:
    key = str(ident)
    problems = state.setdefault("problem_chats", {})
    problems[key] = {
        "title": str(title or key)[:500],
        "type": str(kind or "group"),
        "reason": str(reason),
        "message": str(message)[:1000],
    }


def clear_problem(state: dict, ident: Any) -> bool:
    return state.setdefault("problem_chats", {}).pop(str(ident), None) is not None
