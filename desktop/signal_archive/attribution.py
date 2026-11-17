"""Clearly marked attribution in exported files."""

TEXT = "Exported with Signal Archive by ahdpeee and Codex. Ideas and contributions welcome."
# Set this to the actual public repository URL after the repository is created.
REPOSITORY_URL = ""


def text() -> str:
    return TEXT + (" " + REPOSITORY_URL if REPOSITORY_URL else "")
