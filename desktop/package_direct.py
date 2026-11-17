#!/usr/bin/env python3
"""Build an allowlisted source ZIP for the direct desktop workspace."""
from __future__ import annotations

import argparse
import uuid
import zipfile
from pathlib import Path

FILES = (
    "pyproject.toml", "LICENSE", "install_runtime.py", "install-linux.sh",
    "install-windows.bat", "start-linux.sh", "start-windows.bat",
    "run-linux.sh", "run-windows.bat", "start-existing-session-linux.sh", "start-old-archive-linux.sh",
    "signal_archive/__init__.py", "signal_archive/__main__.py", "signal_archive/cli.py",
    "signal_archive/core.py", "signal_archive/web.py", "signal_archive/autostart.py",
    "signal_archive/direct_app.py", "signal_archive/direct_export.py",
    "signal_archive/direct_service.py", "signal_archive/telegram_ops.py", "signal_archive/legacy_archive.py", "signal_archive/catalog.py", "signal_archive/chat_add.py", "signal_archive/attribution.py",
    "signal_archive/export_notifications.py", "signal_archive/encrypted_bundle.py", "signal_archive/shortcuts.py", "signal_archive/shortcut_launcher.py", "signal_archive/browser_app.py",
    "signal_archive/browser_login.py", "signal_archive/retention.py", "signal_archive/lan_gateway.py", "signal_archive/lan_pairing.py", "signal_archive/lan_sessions.py", "signal_archive/lan_tls.py", "signal_archive/lan_webauthn.py", "signal_archive/lan_device_store.py", "windows_entry.py", "SignalArchive.spec", "build-windows-exe.bat", "WINDOWS_BUILD.md", "CODEX_ARCHIVE_GUIDE.md",
)


def build(source: Path, output: Path) -> None:
    source = source.resolve()
    entries = [(source / "DIRECT_README.md", "README.md")]
    entries.extend((source / name, name) for name in FILES)
    for path, _ in entries:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError(f"Missing or unsafe package input: {path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("xb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path, name in entries:
                    archive.write(path, "signal-archive/" + name)
    except FileExistsError:
        raise
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New ZIP path; existing files are never overwritten")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    output = args.out or source.parent / "dist" / f"signal-archive-direct-{uuid.uuid4().hex[:8]}.zip"
    build(source, output)
    print(output.resolve())


if __name__ == "__main__":
    main()
