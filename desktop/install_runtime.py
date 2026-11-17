#!/usr/bin/env python3
"""Install the direct runtime while preserving an existing environment on failure."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


def install(root: Path, python: str = sys.executable, runner=subprocess.run) -> Path | None:
    root = root.resolve()
    target = root / ".venv-direct"
    backup = None
    if target.exists() or target.is_symlink():
        backup = root / (".venv-direct.saved-" + uuid.uuid4().hex)
        target.rename(backup)
    try:
        runner([python, "-m", "venv", str(target)], cwd=root, check=True)
        runtime = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        runner([str(runtime), "-m", "pip", "install", ".[lan]"], cwd=root, check=True)
        # Check the installed package from outside the source directory. The
        # working tree must not mask a missing or incomplete installation.
        runner([str(runtime), "-I", "-c", "import telethon, keyring, signal_archive.direct_app; from importlib.metadata import version; assert version('signal-archive')"], cwd=target, check=True)
    except BaseException:
        # The target was created in this attempt; the backup is never deleted.
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        if backup is not None:
            backup.rename(target)
        raise
    return backup


def main() -> int:
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required.", file=sys.stderr)
        return 1
    root = Path(__file__).resolve().parent
    try:
        backup = install(root)
    except Exception as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        print("If there was a previous runtime, the installer attempted to restore it.", file=sys.stderr)
        return 1
    print("Installation complete. Start the app with start-linux.sh or start-windows.bat.")
    if backup:
        print(f"The previous runtime was preserved at {backup} for manual recovery.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
