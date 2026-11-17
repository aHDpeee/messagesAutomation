"""Private, atomic storage of WebAuthn public keys and signature counters."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class DeviceStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("Device database must not be a symbolic link.")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            value = {}
        if not isinstance(value, dict):
            raise ValueError("Invalid device database.")
        for credential_id, record in value.items():
            if not isinstance(credential_id, str) or not isinstance(record, dict) or not isinstance(record.get("name"), str) or not isinstance(record.get("public_key"), str) or not isinstance(record.get("sign_count"), int) or record["sign_count"] < 0:
                raise ValueError("Invalid device database record.")
        self.records = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("Device database must not be a symbolic link.")
        fd, temporary = tempfile.mkstemp(prefix=".devices-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.records, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
