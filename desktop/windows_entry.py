"""Entry point for the windowed, single-file Windows distribution."""
from __future__ import annotations

import ctypes
import sys


def main() -> int:
    try:
        from signal_archive.browser_app import main as browser_main
        browser_main()
        return 0
    except Exception as exc:
        message = f"Signal Archive could not start.\n\n{type(exc).__name__}: {exc}"
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, message, "Signal Archive", 0x10)
        else:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
