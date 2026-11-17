"""Discovery and safe reuse of an older Telegram Archive session.

The application keeps its own session in its workspace.  When an older
Telegram Archive installation is present, its single session file can be used
automatically instead of prompting for another login.  Selecting "switch
session" in the interface writes a marker; the running process clears the
local login when it exits so the next start uses the browser sign-in form.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


SWITCH_MARKER = "session-switch.requested"


def legacy_session_directories() -> list[Path]:
    """Return candidate directories containing legacy Telegram Archive sessions."""
    directories: list[Path] = []
    configured = os.environ.get("TELEGRAM_ARCHIVE_HOME")
    if configured:
        directories.append(Path(configured).expanduser() / "data" / "session")
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if not xdg_data:
        xdg_data = str(Path.home() / ".local" / "share")
    directories.append(Path(xdg_data).expanduser() / "TelegramArchive" / "data" / "session")
    if os.name == "nt":
        for variable in ("APPDATA", "LOCALAPPDATA"):
            configured = os.environ.get(variable)
            if configured:
                directories.append(Path(configured).expanduser() / "TelegramArchive" / "data" / "session")
    unique: list[Path] = []
    for directory in directories:
        if directory not in unique:
            unique.append(directory)
    return unique


def discover_existing_session() -> Path | None:
    """Find the one regular, non-symlink legacy session, if it exists."""
    found: list[Path] = []
    for directory in legacy_session_directories():
        try:
            candidates = [p for p in directory.glob("*.session") if p.is_file() and not p.is_symlink()]
        except OSError:
            continue
        found.extend(candidates)
    if len(found) > 1:
        raise RuntimeError("Найдено несколько старых сессий Telegram Archive. Укажите нужную сессию явно; новый код не запрашивался.")
    if len(found) == 1:
        return found[0]
    return None


def _check_windows_file_unused(path: Path) -> None:
    """Refuse a legacy session that another Windows process has open.

    The exclusive handle is a best-effort preflight; users must still not start
    the old application while this app is using the same session.
    """
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise RuntimeError("Старая сессия используется другим процессом или недоступна для проверки.")
    close(handle)


def prepare_existing_session(path: Path) -> None:
    """Stop the legacy service and verify that its session file is free."""
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or not path.is_file() or path.suffix != ".session":
        raise RuntimeError("Старый файл сессии не найден или недопустим.")
    if os.name == "nt":
        _check_windows_file_unused(path)
        return
    try:
        bus = subprocess.run(["systemctl", "--user", "show-environment"],
                             capture_output=True, timeout=10, check=False)
        if bus.returncode != 0:
            raise RuntimeError("Не удалось проверить старую службу systemd.")
        stopped = subprocess.run(["systemctl", "--user", "stop", "telegram-archive.service"],
                                 capture_output=True, timeout=20, check=False)
        if stopped.returncode != 0:
            raise RuntimeError("Не удалось остановить старую службу Telegram Archive.")
        check = subprocess.run(["fuser", "-s", str(path)],
                               capture_output=True, timeout=20, check=False)
        if check.returncode == 0 or check.stderr.strip() or check.returncode != 1:
            raise RuntimeError("Старая сессия ещё используется или её нельзя проверить.")
    except FileNotFoundError as exc:
        raise RuntimeError("Для старой сессии нужны systemctl и fuser.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Проверка старой сессии заняла слишком много времени.") from exc


def resolve_startup_session(state: dict, session_file: Path | None,
                            require_existing_auth: bool) -> tuple[Path | None, bool]:
    """Choose the session source, preferring an existing legacy session."""
    if session_file is not None:
        return session_file, require_existing_auth
    if state.get("session_source") == "own":
        return None, False
    candidate = discover_existing_session()
    if candidate is None:
        return None, False
    prepare_existing_session(candidate)
    return candidate, True


def switch_requested(home: Path) -> bool:
    return (Path(home) / SWITCH_MARKER).is_file()


def request_session_switch(home: Path) -> None:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = home / SWITCH_MARKER
    fd, temporary = tempfile.mkstemp(prefix=".session-switch-", suffix=".tmp", dir=home)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("switch\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
        os.chmod(marker, 0o600)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def clear_session_switch(home: Path) -> None:
    (Path(home) / SWITCH_MARKER).unlink(missing_ok=True)
