"""Health response model."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class HealthStatus:
    status: str
    runtime: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
