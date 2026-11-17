"""Opt-in login autostart for the current desktop user."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NAME = "SignalArchiveDirect"


def _command(home: Path, exports: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--home", str(home), "--exports", str(exports), "--no-browser", "--wait-for-cooldown"]
    return [sys.executable, "-m", "signal_archive", "--home", str(home), "telegram", "--exports", str(exports), "--no-browser", "--wait-for-cooldown"]


def _desktop_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "signal-archive-direct.desktop"


def _desktop_arg(arg: str) -> str:
    # Desktop Entry Exec quoting, not shell quoting. Percent introduces field codes.
    return '"' + arg.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'


def enabled() -> bool:
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, NAME)
            return True
        except FileNotFoundError:
            return False
    return _desktop_path().exists()


def configure(enable: bool, home: Path, exports: Path) -> None:
    command = _command(home, exports)
    if os.name == "nt":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enable:
                winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            else:
                try:
                    winreg.DeleteValue(key, NAME)
                except FileNotFoundError:
                    pass
        return
    path = _desktop_path()
    if not enable:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = "[Desktop Entry]\nType=Application\nName=Signal Archive\nTerminal=true\nExec=" + " ".join(map(_desktop_arg, command)) + "\n"
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(contents, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
