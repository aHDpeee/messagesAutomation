"""In-memory Telethon session with a platform-owned encrypted persistence boundary."""

from pathlib import Path
from typing import Any, cast


def protected_session(store: Any, legacy_path: str) -> Any:
    """Keep authorization bytes in memory; the Android store encrypts every persisted value.

    The store exposes load/save/clear and must fail closed on decryption or write errors.
    A legacy SQLite session is removed only after its encrypted replacement is durable.
    """
    from telethon.sessions import SQLiteSession, StringSession  # type: ignore[import-untyped]

    class ProtectedSession(StringSession):  # type: ignore[misc]  # Telethon ships no type marker.
        def __init__(self, serialized: str) -> None:
            super().__init__(serialized)
            self._deleted = False

        def save(self) -> str:
            serialized = cast(str, StringSession.save(self))
            if not self._deleted:
                store.save(serialized)
            return serialized

        def delete(self) -> None:
            self._deleted = True
            self.auth_key = None
            store.clear()

    serialized = str(store.load() or "")
    legacy_file = Path(f"{legacy_path}.session")
    if legacy_file.exists():
        if not serialized:
            legacy = SQLiteSession(legacy_path)
            try:
                serialized = StringSession.save(legacy)
                # Commit before deleting any legacy state, including an empty session.
                store.save(serialized)
            finally:
                legacy.close()
        restored = ProtectedSession(serialized)
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(f"{legacy_file}{suffix}").unlink(missing_ok=True)
        return restored
    return ProtectedSession(serialized)
