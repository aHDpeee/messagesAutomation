#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_HOME="${TELEGRAM_ARCHIVE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/TelegramArchive}"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA="$APP_HOME/data"
VENV="$APP_HOME/.venv"
VENV_PY="$VENV/bin/python"
ENVFILE="$APP_HOME/.env"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '\n==> %s\n' "$*"; }

[[ "${EUID:-$(id -u)}" -ne 0 ]] || fail "Run as your normal user, not root."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required."
systemctl --user show-environment >/dev/null 2>&1 || fail "The systemd user manager is unavailable. Run from your normal desktop session."
[[ -f "$HERE/manager.py" ]] || fail "manager.py not found beside this script."

python_version() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true; }
version_ok() { [[ "$1" =~ ^([0-9]+)\.([0-9]+)$ ]] && (( BASH_REMATCH[1] > 3 || (BASH_REMATCH[1] == 3 && BASH_REMATCH[2] >= 14) )); }
find_python() {
  local candidate version
  for candidate in python3.14 python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    version="$(python_version "$(command -v "$candidate")")"
    if version_ok "$version"; then printf '%s\n' "$(command -v "$candidate")"; return 0; fi
  done
  return 1
}

PYTHON="$(find_python || true)"
if [[ -z "$PYTHON" ]] && command -v pacman >/dev/null 2>&1; then
  command -v sudo >/dev/null 2>&1 || fail "Python 3.14+ is required. Install with: sudo pacman -Syu --needed python python-pip"
  info "Installing Python using pacman"
  sudo pacman -Syu --needed python python-pip
  PYTHON="$(find_python || true)"
fi
[[ -n "$PYTHON" ]] || fail "Python 3.14+ not found. On Arch: sudo pacman -Syu --needed python python-pip"

info "Preparing application directories"
install -d -m 700 "$APP_HOME" "$DATA/backups" "$DATA/session" "$APP_HOME/exports" "$APP_HOME/logs" "$UNIT_DIR"
chmod 700 "$APP_HOME" "$DATA" "$DATA/backups" "$DATA/session" "$APP_HOME/exports" "$APP_HOME/logs"
find "$DATA" -type d -exec chmod 700 {} +
find "$DATA" -type f -exec chmod 600 {} +

# This release's manager and migration rollback currently target the default SQLite database.
# Refuse custom DB locations rather than silently backing up or exporting the wrong file.
if [[ -f "$ENVFILE" ]]; then
  for key in DATABASE_URL DATABASE_PATH DATABASE_DIR DB_PATH; do
    if grep -qE "^${key}=.+" "$ENVFILE"; then fail "Custom $key is configured. This release has not been validated for custom database locations; do not update until the database path is handled explicitly."; fi
  done
  configured_backup="$(sed -n 's/^BACKUP_PATH=//p' "$ENVFILE" | tail -1)"
  if [[ -n "$configured_backup" && "$configured_backup" != "$DATA/backups" ]]; then fail "Custom BACKUP_PATH is configured ($configured_backup). This release currently expects $DATA/backups; refusing to risk exporting or backing up the wrong database."; fi
fi


if [[ ! -x "$VENV_PY" ]] || ! version_ok "$(python_version "$VENV_PY")"; then
  if [[ -e "$VENV" ]]; then
    backup="$APP_HOME/.venv.backup.$(date +%Y%m%d-%H%M%S)"
    info "Preserving old/incompatible environment at $backup"
    mv -- "$VENV" "$backup"
  fi
  info "Creating Python virtual environment"
  "$PYTHON" -m venv "$VENV" || fail "Could not create virtual environment. Check python and python-pip packages."
fi

# Stop all processes that can touch the Telegram database/session before changing code or dependencies.
for service in telegram-archive.service telegram-viewer.service telegram-manager.service telegram-archive-export.timer telegram-archive-export.service; do
  systemctl --user stop "$service" >/dev/null 2>&1 || true
done
ROLLBACK_DIR="$APP_HOME/rollback-$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$ROLLBACK_DIR/units"
chmod 700 "$ROLLBACK_DIR"
"$VENV_PY" -m pip freeze > "$ROLLBACK_DIR/requirements-before.txt" 2>/dev/null || :
[[ ! -f "$APP_HOME/runtime.lock" ]] || cp -a "$APP_HOME/runtime.lock" "$ROLLBACK_DIR/runtime.lock"
[[ ! -f "$ENVFILE" ]] || cp -a "$ENVFILE" "$ROLLBACK_DIR/env-before"
DBFILE="$DATA/backups/telegram_backup.db"
DB_EXISTED=0
if [[ -f "$DBFILE" ]]; then
  DB_EXISTED=1
  "$VENV_PY" - "$DBFILE" "$ROLLBACK_DIR/database-before.sqlite" <<'PYDB'
import sqlite3, sys
src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2])
try: src.backup(dst)
finally: dst.close(); src.close()
PYDB
  chmod 600 "$ROLLBACK_DIR/database-before.sqlite"
fi

# Preserve the currently installed scripts before touching them. If this update fails,
# restore them automatically; data, session and .env are never overwritten here.
for file in manager.py telegram_manager.py secret_runner.py; do
  [[ ! -f "$APP_HOME/$file" ]] || cp -a "$APP_HOME/$file" "$ROLLBACK_DIR/$file"
done
for file in telegram-archive.service telegram-viewer.service telegram-manager.service telegram-archive-export.service telegram-archive-export.timer; do
  [[ ! -f "$UNIT_DIR/$file" ]] || cp -a "$UNIT_DIR/$file" "$ROLLBACK_DIR/units/$file"
done
rollback_on_error() {
  status=$?
  if (( status != 0 )); then
    info "Update failed; restoring the previous application scripts and service definitions"
    for file in manager.py telegram_manager.py secret_runner.py; do [[ ! -f "$ROLLBACK_DIR/$file" ]] || cp -a "$ROLLBACK_DIR/$file" "$APP_HOME/$file"; done
    for file in telegram-archive.service telegram-viewer.service telegram-manager.service telegram-archive-export.service telegram-archive-export.timer; do
      if [[ -f "$ROLLBACK_DIR/units/$file" ]]; then cp -a "$ROLLBACK_DIR/units/$file" "$UNIT_DIR/$file"; else rm -f "$UNIT_DIR/$file"; fi
    done
    if [[ -f "$ROLLBACK_DIR/database-before.sqlite" && -f "$DBFILE" ]]; then cp -f "$ROLLBACK_DIR/database-before.sqlite" "$DBFILE"; chmod 600 "$DBFILE"; elif [[ "$DB_EXISTED" == 0 ]]; then rm -f "$DBFILE"; fi
    if [[ -f "$ROLLBACK_DIR/env-before" ]]; then cp -a "$ROLLBACK_DIR/env-before" "$ENVFILE"; elif [[ "${NEW_INSTALL:-0}" == 1 ]]; then rm -f "$ENVFILE"; "$VENV_PY" "$APP_HOME/secret_runner.py" --delete-api-hash >/dev/null 2>&1 || true; fi
    if [[ -s "$ROLLBACK_DIR/requirements-before.txt" ]]; then "$VENV_PY" -m pip install -r "$ROLLBACK_DIR/requirements-before.txt" >/dev/null 2>&1 || true; fi
    if [[ -f "$ROLLBACK_DIR/runtime.lock" ]]; then cp -a "$ROLLBACK_DIR/runtime.lock" "$APP_HOME/runtime.lock"; else rm -f "$APP_HOME/runtime.lock"; fi
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    systemctl --user start telegram-archive.service telegram-viewer.service telegram-manager.service >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap rollback_on_error EXIT


info "Installing pinned Telegram Archive runtime"
"$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
if [[ -s "$APP_HOME/runtime.lock" ]]; then
  "$VENV_PY" -m pip install -r "$APP_HOME/runtime.lock"
else
  "$VENV_PY" -m pip install 'telegram-archive==9.7.0' 'uvicorn==0.38.0' 'keyring==25.7.0'
  "$VENV_PY" -m pip freeze > "$APP_HOME/runtime.lock"
  chmod 600 "$APP_HOME/runtime.lock"
fi
"$VENV_PY" -c 'import telegram_archive, uvicorn, keyring; from importlib.metadata import version; assert version("telegram-archive")=="9.7.0"; assert version("uvicorn")=="0.38.0"' || fail "Pinned Telegram Archive dependencies did not install correctly."

install -m 700 "$HERE/secret_runner.py" "$APP_HOME/secret_runner.py"

# First-time configuration. Existing credentials are never overwritten.
if [[ ! -f "$ENVFILE" ]]; then
  NEW_INSTALL=1
  info "First-time Telegram setup"
  cat <<'MSG'
A Telegram API application is required. Open https://my.telegram.org -> API development tools.
API ID: numeric application identifier (not your Telegram user ID).
API Hash: 32-character hexadecimal secret. It will be stored in your OS keyring, not .env.
Phone: your own Telegram account number in international format, e.g. +79991234567.
The Telegram login code and optional Two-Step Verification password are entered later in the local authorization prompt only.
The Telethon session remains a sensitive local file; use full-disk encryption.
MSG
  read -r -p 'Telegram API ID (digits only): ' api_id
  read -r -s -p 'Telegram API Hash (32 hex characters, hidden): ' api_hash; printf '\n'
  read -r -p 'Telegram phone (+country code and number, e.g. +79991234567): ' phone
  [[ "$api_id" =~ ^[0-9]+$ ]] || fail "Telegram API ID must contain digits only."
  [[ ${#api_hash} -ge 20 && "$api_hash" != *$'\n'* ]] || fail "Telegram API Hash is invalid or too short."
  [[ "$phone" =~ ^\+[0-9]{7,}$ ]] || fail "Phone number must start with + and contain at least 7 digits."
  viewer_password="$("$VENV_PY" -c 'import secrets; print(secrets.token_urlsafe(24))')"
  cat > "$ENVFILE" <<ENV
TELEGRAM_API_ID=$api_id
TELEGRAM_PHONE=$phone
VIEWER_USERNAME=admin
VIEWER_PASSWORD=$viewer_password
VIEWER_PORT=8000
BACKUP_PATH=$DATA/backups
SESSION_DIR=$DATA/session
SCHEDULE=0 3 * * *
ENABLE_LISTENER=true
LISTEN_DELETIONS=true
ENV
  chmod 600 "$ENVFILE"
  printf '%s' "$api_hash" | "$VENV_PY" "$APP_HOME/secret_runner.py" --set-api-hash-stdin
  unset api_hash
  NEW_INSTALL=1
else
  chmod 600 "$ENVFILE"
  NEW_INSTALL=0
  for key in TELEGRAM_API_ID TELEGRAM_PHONE; do
    grep -qE "^${key}=.+" "$ENVFILE" || fail "$ENVFILE exists but $key is missing. Add it to the existing configuration; the installer will not overwrite your credentials."
  done
  # Move a legacy plaintext API Hash into the OS keyring before removing it from .env.
  "$VENV_PY" "$APP_HOME/secret_runner.py" --migrate-env "$ENVFILE"
  "$VENV_PY" "$APP_HOME/secret_runner.py" --check-api-hash || fail "API Hash is missing from the OS keyring; Telegram services will not be started. Re-enter it in Settings."
fi

install -m 700 "$HERE/manager.py" "$APP_HOME/manager.py"
install -m 700 "$HERE/telegram_manager.py" "$APP_HOME/telegram_manager.py"
install -m 700 "$HERE/secret_runner.py" "$APP_HOME/secret_runner.py"

info "Applying database migrations"
(cd "$APP_HOME" && "$VENV_PY" -m telegram_archive --data-dir "$DATA" migrate)
if [[ "$NEW_INSTALL" == 1 ]]; then
  info "Authorizing Telegram account"
  printf 'Follow the Telegram login prompts. The verification code is entered only in this terminal.\n'
  (cd "$APP_HOME" && "$VENV_PY" "$APP_HOME/secret_runner.py" -- python -m telegram_archive --data-dir "$DATA" auth)
  # The schedule service performs its initial full backup on startup; do not run a second backup here.
fi

info "Installing systemd user services"
"$VENV_PY" - "$APP_HOME" "$DATA" "$VENV" "$UNIT_DIR" <<'PY'
import json, sys
from pathlib import Path
root, data, venv, unit_dir = map(Path, sys.argv[1:])
# systemd path-valued directives (WorkingDirectory/EnvironmentFile) are not shell
# arguments: do not put JSON-style quotes around them. ExecStart uses systemd's
# double-quoted argument syntax with escaping.
def unit_arg(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
def unit_path(value):
    value = str(value)
    if any(ch.isspace() for ch in value):
        raise SystemExit(f"Install path contains whitespace, unsupported by this service template: {value}")
    return value
py = venv / 'bin/python'
wd, envfile = unit_path(root), unit_path(root / '.env')
units = {
 'telegram-archive.service': f'''[Unit]\nDescription=Telegram Archive listener and scheduled backups\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory={wd}\nEnvironmentFile={envfile}\nUMask=0077\nExecStart={unit_arg(py)} {unit_arg(root / 'secret_runner.py')} -- python -m telegram_archive --data-dir {unit_arg(data)} schedule\nRestart=no\n\n[Install]\nWantedBy=default.target\n''',
 'telegram-viewer.service': f'''[Unit]\nDescription=Telegram Archive web viewer\nAfter=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory={wd}\nEnvironmentFile={envfile}\nUMask=0077\nExecStart={unit_arg(py)} -m uvicorn telegram_archive.web.main:app --host 127.0.0.1 --port 8000\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n''',
 'telegram-manager.service': f'''[Unit]\nDescription=Signal Archive local export manager\nAfter=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory={wd}\nEnvironmentFile={envfile}\nUMask=0077\nExecStart={unit_arg(py)} {unit_arg(root / 'manager.py')}\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n'''
}
unit_dir.mkdir(parents=True, exist_ok=True)
for name, content in units.items():
    (unit_dir / name).write_text(content, encoding='utf-8')
PY

systemctl --user daemon-reload
if ! systemd-analyze --user verify "$UNIT_DIR/telegram-archive.service" "$UNIT_DIR/telegram-viewer.service" "$UNIT_DIR/telegram-manager.service"; then
  info "Generated service files failed verification"
  exit 1
fi
systemctl --user daemon-reload
"$VENV_PY" "$APP_HOME/manager.py" --install-timer
if [[ -f "$UNIT_DIR/telegram-archive-export.service" ]]; then
  systemd-analyze --user verify "$UNIT_DIR/telegram-archive-export.service" "$UNIT_DIR/telegram-archive-export.timer"
fi
systemctl --user enable telegram-archive.service telegram-viewer.service telegram-manager.service
systemctl --user start telegram-viewer.service telegram-manager.service
systemctl --user start telegram-archive.service || info "Telegram sync is not active. Check authorization/keyring status; it will not be restarted automatically."
sleep 2
systemctl --user is-active telegram-viewer.service >/dev/null || fail "Signal Archive viewer did not start."
systemctl --user is-active telegram-manager.service >/dev/null || fail "Signal Archive manager did not start."
systemctl --user --no-pager --full status telegram-archive.service telegram-viewer.service telegram-manager.service || true
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/signal-archive.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Signal Archive
Exec=xdg-open http://127.0.0.1:8001
X-GNOME-Autostart-enabled=true
DESKTOP
if command -v xdg-open >/dev/null 2>&1; then xdg-open http://127.0.0.1:8001 >/dev/null 2>&1 & fi
# The rollback copy is retained, but remove the legacy plaintext API Hash from it after success.
if [[ -f "$ROLLBACK_DIR/env-before" ]]; then
  sed '/^TELEGRAM_API_HASH=/d' "$ROLLBACK_DIR/env-before" > "$ROLLBACK_DIR/env-before.tmp"
  chmod 600 "$ROLLBACK_DIR/env-before.tmp"; mv "$ROLLBACK_DIR/env-before.tmp" "$ROLLBACK_DIR/env-before"
fi
trap - EXIT
printf '\nSignal Archive setup/update finished.\nOpen: http://127.0.0.1:8001\n'
if [[ "${NEW_INSTALL:-0}" == 1 ]]; then
  printf 'Viewer login: admin\nViewer password: %s\nSave this password securely.\n' "$viewer_password"
fi
