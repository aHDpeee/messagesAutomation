#!/usr/bin/env bash
set -Eeuo pipefail
APP_HOME="${TELEGRAM_ARCHIVE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/TelegramArchive}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/signal-archive.desktop"
EXPORTS="${XDG_DOWNLOAD_DIR:-$HOME/Downloads}/Signal Archive"
LEGACY_GLOB="$HOME/Signal-Archive-preserved-*"
[[ "${EUID:-$(id -u)}" -ne 0 ]] || { echo "Run as your normal user, not root." >&2; exit 1; }
MODE="${1:---keep-archives-remove-credentials}"

remove_sessions_from_env() {
  local envfile="$1" line value resolved
  [[ -f "$envfile" ]] || return 0
  while IFS= read -r line; do
    case "$line" in
      SESSION_DIR=*|SESSION_PATH=*)
        value="${line#*=}"
        [[ -n "$value" ]] || continue
        if [[ "$value" != /* ]]; then value="$(dirname -- "$envfile")/$value"; fi
        resolved="$(realpath -m -- "$value")"
        case "$resolved" in
          "$HOME"/*) rm -rf -- "$resolved" ;;
          *) echo "Refusing to delete session path outside HOME: $resolved" >&2; return 1 ;;
        esac
        ;;
    esac
  done < "$envfile"
}
if [[ "$MODE" == "--purge" ]]; then
  if [[ "${2:-}" != "--confirm-purge" ]]; then
    echo "This permanently removes archive database, media, credentials, sessions and exports."
    read -r -p 'Type DELETE ALL DATA to continue: ' confirm
    [[ "$confirm" == 'DELETE ALL DATA' ]] || { echo 'Cancelled.'; exit 0; }
  fi
elif [[ "$MODE" != "--keep-archives-remove-credentials" ]]; then
  echo "Usage: $0 [--keep-archives-remove-credentials|--purge [--confirm-purge]]" >&2; exit 2
fi
# Remove custom session paths too; never evaluate .env as shell code.
remove_sessions_from_env "$APP_HOME/.env"
shopt -s nullglob
for legacy in $LEGACY_GLOB; do remove_sessions_from_env "$legacy/.env"; done
# Remove the API Hash from the OS keyring before deleting the helper/runtime.
if [[ -x "$APP_HOME/.venv/bin/python" && -f "$APP_HOME/secret_runner.py" ]]; then
  "$APP_HOME/.venv/bin/python" "$APP_HOME/secret_runner.py" --delete-api-hash || { echo "Could not verify API Hash removal from OS keyring; aborting uninstall." >&2; exit 1; }
fi
services=(telegram-archive.service telegram-viewer.service telegram-manager.service telegram-archive-export.timer telegram-archive-export.service)
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user disable --now "${services[@]}" 2>/dev/null || true
  rm -f "$UNIT_DIR/telegram-archive.service" "$UNIT_DIR/telegram-viewer.service" "$UNIT_DIR/telegram-manager.service" "$UNIT_DIR/telegram-archive-export.service" "$UNIT_DIR/telegram-archive-export.timer"
  systemctl --user daemon-reload || true
  systemctl --user reset-failed || true
fi
rm -f "$AUTOSTART"
if [[ "$MODE" == "--purge" ]]; then
  rm -rf -- "$APP_HOME" "$EXPORTS"
  # Remove credentials and sessions from preserved copies created by earlier versions.
  shopt -s nullglob
  for d in $LEGACY_GLOB; do rm -rf -- "$d"; done
  echo 'Application, local archive data, session, credentials, preserved copies and exports removed.'
  echo 'This cannot revoke a Telegram authorization on Telegram servers. Review Telegram Settings > Devices and terminate the old session there.'
else
  # Keep only the archive database/media. Remove code, venv, API credentials and session.
  keep_tmp="$(mktemp -d "$HOME/.signal-archive-keep.XXXXXX")"
  if [[ -d "$APP_HOME/data/backups" ]]; then cp -a "$APP_HOME/data/backups" "$keep_tmp/backups"; fi
  rm -rf -- "$APP_HOME"
  install -d -m 700 "$APP_HOME/data"
  if [[ -d "$keep_tmp/backups" ]]; then mv "$keep_tmp/backups" "$APP_HOME/data/backups"; fi
  rm -rf -- "$keep_tmp"
  if [[ -d "$APP_HOME/data/backups" ]]; then find "$APP_HOME/data/backups" -type d -exec chmod 700 {} +; find "$APP_HOME/data/backups" -type f -exec chmod 600 {} +; fi
  if [[ -d "$EXPORTS" ]]; then find "$EXPORTS" -type d -exec chmod 700 {} +; find "$EXPORTS" -type f -exec chmod 600 {} +; fi
  # Scrub old preservation directories made by the previous uninstaller.
  for d in $LEGACY_GLOB; do
    rm -f -- "$d/.env"
    rm -rf -- "$d/data/session" "$d/data/sessions" "$d/session"
  done
  echo 'Application services and credentials removed. Archive database/media and Downloads exports were retained.'
  echo 'Old preserved copies were checked and their .env/session files removed.'
  echo 'This cannot revoke a Telegram authorization on Telegram servers. Review Telegram Settings > Devices and terminate the old session there.'
fi
