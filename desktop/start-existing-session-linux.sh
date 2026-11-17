#!/usr/bin/env sh
# Reuse the existing Telegram Archive session without requesting a login code.
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv-direct/bin/python ]; then
    echo 'Сначала установите приложение: sh install-linux.sh' >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1; then
    echo 'Не удалось проверить старую службу systemd. Старую сессию использовать небезопасно.' >&2
    exit 1
fi
if ! command -v fuser >/dev/null 2>&1; then
    echo 'Установите fuser (обычно пакет psmisc), чтобы проверить, что сессия свободна.' >&2
    exit 1
fi
session_dir="${TELEGRAM_ARCHIVE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/TelegramArchive}/data/session"
set -- "$session_dir"/*.session
if [ "$#" -ne 1 ] || [ ! -f "$1" ] || [ -L "$1" ]; then
    echo 'Не найден единственный обычный файл старой Telegram-сессии.' >&2
    exit 1
fi
# The legacy scheduler and this client must never open the same session together.
echo 'Останавливаю старую службу Telegram Archive, чтобы использовать её сессию.'
if ! systemctl --user stop telegram-archive.service; then
    echo 'Не удалось остановить старую службу; запуск отменён.' >&2
    exit 1
fi
check_errors="$(mktemp "${TMPDIR:-/tmp}/signal-session-check.XXXXXXXX")" || exit 1
trap 'rm -f "$check_errors"' 0
if fuser -s "$1" 2>"$check_errors"; then
    echo 'Старая сессия ещё используется другим процессом. Закройте его и повторите.' >&2
    exit 1
else
    status=$?
    # fuser uses 1 for "not in use", but errors can also be nonzero.
    # Do not continue if the scan reported a problem or an unexpected status.
    if [ "$status" -ne 1 ] || [ -s "$check_errors" ]; then
        echo 'Не удалось надёжно проверить старую сессию через fuser; запуск отменён.' >&2
        exit 1
    fi
fi
rm -f "$check_errors"
trap - 0
echo 'Запускаю приложение со старой сессией. Код автоматически запрашиваться не будет.'
exec .venv-direct/bin/python -m signal_archive telegram --session-file "$1" --require-existing-auth
