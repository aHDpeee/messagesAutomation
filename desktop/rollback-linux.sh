#!/usr/bin/env bash
set -Eeuo pipefail
APP_HOME="${TELEGRAM_ARCHIVE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/TelegramArchive}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
[[ "${EUID:-$(id -u)}" -ne 0 ]] || { echo 'Run as your normal user.' >&2; exit 1; }
latest="$(find "$APP_HOME" -maxdepth 1 -type d -name 'rollback-*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)"
[[ -n "$latest" && -d "$latest" ]] || { echo 'No rollback snapshot found.' >&2; exit 1; }
for service in telegram-archive.service telegram-viewer.service telegram-manager.service telegram-archive-export.timer telegram-archive-export.service; do systemctl --user stop "$service" >/dev/null 2>&1 || true; done
for file in manager.py telegram_manager.py secret_runner.py; do
  [[ ! -f "$latest/$file" ]] || install -m 700 "$latest/$file" "$APP_HOME/$file"
done
[[ ! -f "$latest/env-before" ]] || install -m 600 "$latest/env-before" "$APP_HOME/.env"
if [[ -f "$APP_HOME/secret_runner.py" && -x "$APP_HOME/.venv/bin/python" && -f "$APP_HOME/.env" ]]; then
  "$APP_HOME/.venv/bin/python" "$APP_HOME/secret_runner.py" --restore-legacy-env "$APP_HOME/.env"
fi
[[ ! -f "$latest/runtime.lock" ]] || install -m 600 "$latest/runtime.lock" "$APP_HOME/runtime.lock"
if [[ -s "$latest/requirements-before.txt" ]]; then "$APP_HOME/.venv/bin/python" -m pip install -r "$latest/requirements-before.txt"; fi
if [[ -f "$latest/database-before.sqlite" ]]; then
  cp -f "$latest/database-before.sqlite" "$APP_HOME/data/backups/telegram_backup.db"
  chmod 600 "$APP_HOME/data/backups/telegram_backup.db"
fi
if [[ -d "$latest/units" ]]; then
  mkdir -p "$UNIT_DIR"
  for file in telegram-archive.service telegram-viewer.service telegram-manager.service telegram-archive-export.service telegram-archive-export.timer; do
    if [[ -f "$latest/units/$file" ]]; then cp -a "$latest/units/$file" "$UNIT_DIR/$file"; else rm -f "$UNIT_DIR/$file"; fi
  done
  systemctl --user daemon-reload
fi
systemctl --user start telegram-archive.service telegram-viewer.service telegram-manager.service || true
systemctl --user --no-pager status telegram-manager.service || true
printf 'Restored application, dependencies and database snapshot from %s\n' "$latest"
