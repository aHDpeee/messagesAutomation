#!/usr/bin/env sh
# Открыть старый локальный архив без соединения с Telegram.
set -eu
cd "$(dirname "$0")"
exec sh ./start-linux.sh --offline-legacy
