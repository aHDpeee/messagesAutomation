"""Unicode-aware matching for small dialog metadata, never full message history."""

from difflib import SequenceMatcher
import re
import unicodedata


def normalize(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def dialog_rank(query: str, title: str | None, username: str | None,
                preview: str | None) -> int:
    needle = normalize(query)
    if not needle:
        return 1
    candidates = [normalize(title), normalize(username).lstrip("@"), normalize(preview)]
    needle = needle.lstrip("@")
    if any(value.startswith(needle) for value in candidates):
        return 3
    if any(needle in value for value in candidates):
        return 2
    if len(needle) >= 4:
        words = [word for value in candidates for word in re.findall(r"\w+", value)]
        if any(SequenceMatcher(None, needle, word).ratio() >= 0.75 for word in words):
            return 1
    return 0
