"""Browser-first launcher used by the Windows executable."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="SignalArchive")
    parser.add_argument("--home", type=Path, default=Path.home() / ".signal-archive")
    parser.add_argument("--exports", type=Path, default=Path.home() / "Downloads" / "Signal Archive")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--wait-for-cooldown", action="store_true")
    parser.add_argument("--open-existing", action="store_true")
    args = parser.parse_args()
    if args.open_existing:
        from .shortcut_launcher import open_existing
        if open_existing(args.home.expanduser()):
            return
    elif not args.no_browser:
        from .shortcut_launcher import stop_existing
        stop_existing(args.home.expanduser())
    from .direct_app import run
    run(args.home.expanduser(), args.exports.expanduser(), 8765,
        open_browser=not args.no_browser, wait_for_cooldown=args.wait_for_cooldown,
        browser_auth=True)
