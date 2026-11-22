"""UTC timestamp helpers for persisted records."""

from datetime import UTC, datetime


def utc_now() -> str:
    """Return an ISO 8601 UTC timestamp with a Z suffix."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
