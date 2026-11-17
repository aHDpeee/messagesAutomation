"""Conservative retention for explicitly registered application archives."""
from __future__ import annotations

import re
import stat
import time
from pathlib import Path

# Only names recorded when the app creates a bundle may be deleted. A matching
# name in the directory by itself is never sufficient evidence of ownership.
_BUNDLE = re.compile(r"(?:chats_(?:[\w -]{1,50})_(?:new|[0-9]{4}-[0-9]{2}-[0-9]{2}_to_[0-9]{4}-[0-9]{2}-[0-9]{2})|chats|members|old_archive)_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}\.zip\Z")


def remove_expired(exports: Path, registered_names: list[str], *, enabled: bool = False,
                   days: int = 0, active_names: tuple[str, ...] = (), now: float | None = None) -> list[str]:
    """Delete expired registered bundles, returning their names.

    Call only while no export is running, and do not pass names of bundles in
    use. Names, not paths, are accepted to prevent traversal outside exports.
    """
    if not enabled:
        return []
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 36500:
        raise ValueError("Retention must be between 1 and 36500 days.")
    folder = Path(exports)
    if folder.is_symlink() or not folder.is_dir():
        return []
    cutoff = (time.time() if now is None else float(now)) - days * 86400
    active = set(active_names)
    removed = []
    for name in dict.fromkeys(registered_names):
        if not isinstance(name, str) or not _BUNDLE.fullmatch(name) or name in active:
            continue
        path = folder / name
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff:
                continue
            path.unlink()
            removed.append(name)
        except FileNotFoundError:
            continue
    return removed
