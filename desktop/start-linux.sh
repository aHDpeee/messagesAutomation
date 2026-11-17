#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv-direct/bin/python ]; then
    echo 'First launch: installing Signal Archive. Internet access is required.'
    sh ./install-linux.sh
fi
if [ ! -x .venv-direct/bin/python ]; then echo 'Installation did not create the Python runtime.' >&2; exit 1; fi
if [ "${1:-}" = "--existing-session" ]; then
    shift
    if [ "$#" -ne 0 ]; then echo '--existing-session does not accept other arguments.' >&2; exit 1; fi
    exec sh ./start-existing-session-linux.sh
fi
exec .venv-direct/bin/python -m signal_archive telegram "$@"
