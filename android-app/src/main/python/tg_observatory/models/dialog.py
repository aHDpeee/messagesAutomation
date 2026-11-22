"""Dialog summary shared by Android, TUI and export services."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class DialogSummary:
    telegram_id: int
    raw_telegram_id: int
    kind: str
    title: str
    username: str | None
    unread_count: int
    participant_count: int | None
    last_message_at: str | None
    last_message_present: bool | None = None
    last_message_out: bool | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return asdict(self)
