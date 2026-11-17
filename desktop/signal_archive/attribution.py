"""Clearly marked attribution in exported files."""

TEXT = "Exported with Signal Archive by ahdpeee and Codex. Ideas and contributions welcome."
REPOSITORY_URL = "https://github.com/aHDpeee/messagesAutomation"


def text() -> str:
    return TEXT + (" " + REPOSITORY_URL if REPOSITORY_URL else "")
