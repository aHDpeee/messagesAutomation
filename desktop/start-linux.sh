#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv-direct/bin/python ]; then echo 'Run sh install-linux.sh first.' >&2; exit 1; fi
exec .venv-direct/bin/python -m signal_archive telegram "$@"
