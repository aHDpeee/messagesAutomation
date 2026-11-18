"""Timing shown in the interface, matching the local daily scheduler."""
from __future__ import annotations

from datetime import datetime, timedelta


def summary(state: dict, now: datetime | None = None) -> dict[str, str | None]:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    schedule = state.get("schedule", {})
    next_at = None
    if schedule.get("enabled") and state.get("selected"):
        try:
            hour, minute = map(int, schedule.get("time", "03:15").split(":"))
            today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            latest = today if now >= today else today - timedelta(days=1)
            last_day = schedule.get("last_day")
            failed_day = schedule.get("failed_day")
            if latest.date().isoformat() not in (last_day, failed_day):
                next_at = now
            else:
                next_at = today if now < today else today + timedelta(days=1)
        except (TypeError, ValueError, AttributeError):
            next_at = None
    finished = []
    for name in ("last_export_messages", "last_export_recent", "last_export_snapshot",
                 "last_export_group_members", "last_export_old_archive"):
        try:
            value = datetime.fromisoformat(state.get(name, {}).get("finished_at", ""))
            if value.tzinfo is not None:
                finished.append(value)
        except (TypeError, ValueError, AttributeError):
            continue
    return {"next_at": next_at.isoformat() if next_at else None,
            "last_at": max(finished).isoformat() if finished else None}
