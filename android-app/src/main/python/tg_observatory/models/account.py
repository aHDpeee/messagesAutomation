"""Public account profile data returned to clients."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class AccountProfile:
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    phone_hint: str | None

    def to_dict(self) -> dict[str, int | str | None]:
        return asdict(self)
