"""Play a local song after an export without affecting the export itself."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

_AUDIO_EXTENSIONS = {".mp3", ".ogg", ".opus", ".wav", ".flac", ".m4a", ".aac"}


def _play() -> None:
    try:
        folder = Path.home() / "Music"
        songs = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS), key=lambda p: p.name.casefold())
        if not songs:
            return
        if os.name == "nt":
            os.startfile(str(songs[0]))
        else:
            # This runs on a daemon thread, not the export event loop. Waiting
            # reaps xdg-open and avoids leaking a child process handle.
            subprocess.run(["xdg-open", str(songs[0])], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, check=False)
    except (OSError, RuntimeError):
        # Music is optional: a missing directory, desktop or player must not
        # turn a completed export into a failure.
        return


def play_on_completion() -> None:
    try:
        threading.Thread(target=_play, name="export-completion-music", daemon=True).start()
    except Exception:
        pass
