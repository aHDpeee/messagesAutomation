"""Create application launchers; the caller supplies an open-or-start command."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .autostart import _desktop_arg


def desktop_directory() -> Path:
    if os.name == 'nt':
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) != 0:
            raise OSError('Could not find the desktop folder.')
        return Path(buffer.value)
    return Path.home() / 'Desktop'


def launcher_command(home: Path, exports: Path, external_session: bool = False) -> list[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--open-existing', '--home', str(home), '--exports', str(exports)]
    # The legacy session is discovered automatically at startup, so shortcuts
    # always use the normal launcher and need no special session flag.
    launch = [sys.executable, '-m', 'signal_archive', '--home', str(home), 'telegram', '--exports', str(exports)]
    # A desktop shortcut starts in an arbitrary directory.  Change to the
    # source checkout before importing the launcher, so it and its child use
    # the current application rather than an older copy installed in the venv.
    bootstrap = ('import os,sys; os.chdir(sys.argv.pop(1)); '
                 'from signal_archive.shortcut_launcher import main; main()')
    return [sys.executable, '-c', bootstrap, str(application_directory()), '--home', str(home), '--', *launch]


def application_directory() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def ensure_application_shortcut(home: Path, exports: Path, external_session: bool = False) -> bool:
    """Best effort: never overwrite existing shortcuts or block startup."""
    try:
        create_shortcut(application_directory(), launcher_command(home, exports, external_session))
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def create_shortcut(directory: Path, command: list[str]) -> Path:
    """Create a desktop shortcut pointing to an existing executable and arguments.

    The caller is responsible for choosing an open-or-start launcher and for
    invoking this only when the user requests a shortcut in the target folder.
    """
    if not command or not command[0] or any('\x00' in part or '\n' in part or '\r' in part for part in command):
        raise ValueError('Invalid shortcut command.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        path = directory / 'Signal Archive.lnk'
        if path.exists() or path.is_symlink():
            raise FileExistsError(f'Shortcut already exists: {path}')
        script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:SIGNAL_SHORTCUT_PATH);"
                  "$s.TargetPath=$env:SIGNAL_SHORTCUT_TARGET;"
                  "$s.Arguments=$env:SIGNAL_SHORTCUT_ARGS;"
                  "$s.WorkingDirectory=$env:SIGNAL_SHORTCUT_WORKDIR;$s.Save()")
        env = os.environ.copy()
        env.update(SIGNAL_SHORTCUT_PATH=str(path), SIGNAL_SHORTCUT_TARGET=command[0],
                   SIGNAL_SHORTCUT_ARGS=subprocess.list2cmdline(command[1:]),
                   SIGNAL_SHORTCUT_WORKDIR=str(Path(command[0]).parent))
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                       env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
        return path
    path = directory / 'Signal Archive.desktop'
    if path.exists() or path.is_symlink():
        raise FileExistsError(f'Shortcut already exists: {path}')
    contents = ('[Desktop Entry]\nType=Application\nName=Signal Archive\n'
                'Comment=Open Signal Archive\nTerminal=false\nExec='
                + ' '.join(_desktop_arg(part) for part in command) + '\n')
    path.write_text(contents, encoding='utf-8')
    path.chmod(0o755)
    return path
