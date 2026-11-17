#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname -- "$0")"
PYTHON="${SIGNAL_ARCHIVE_PYTHON:-.venv-direct/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  printf 'Install the direct runtime first or set SIGNAL_ARCHIVE_PYTHON to a Python with Telethon and keyring.\n' >&2
  exit 1
fi
"$PYTHON" -c 'import telethon, keyring; from importlib.metadata import version; assert version("Telethon") == "1.45.0"; assert version("keyring") == "25.7.0"' || { printf 'Telethon 1.45.0 and keyring 25.7.0 are required for verification.\n' >&2; exit 1; }
"$PYTHON" -m py_compile manager.py telegram_manager.py secret_runner.py install_runtime.py package_direct.py signal_archive/*.py
bash -n update-existing-linux.sh rollback-linux.sh uninstall-linux.sh install-linux.sh start-linux.sh run-linux.sh start-existing-session-linux.sh start-old-archive-linux.sh
"$PYTHON" -m unittest discover -s tests -v
printf '\nLocal checks passed. Live Telegram, desktop startup and Windows integration still require separate testing.\n'
