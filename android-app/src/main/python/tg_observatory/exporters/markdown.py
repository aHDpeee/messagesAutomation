"""One Markdown renderer for untrusted, locally indexed Telegram data."""

from __future__ import annotations

import html
import json
import re
from typing import Any

WARNING = (
    "Only locally indexed messages are included. Telegram history completeness has not "
    "been verified; messages outside the index or inaccessible to this account may be missing."
)


def escape(value: Any) -> str:
    text = html.escape(str(value or ""), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def header(dialog: Any, exported_at: str, count: int, start: str | None,
           end: str | None, *, end_exclusive: bool = False) -> bytes:
    title = str(dialog["title"] or "Telegram conversation")
    fields = {
        "title": f"{title} — Conversation Export",
        "aliases": [f"{title} Export"],
        "type": "telegram-export", "schema_version": 1, "export_type": "chat",
        "telegram_dialog_id": int(dialog["telegram_id"]), "exported_at": exported_at,
        "period_start": start, "period_end": end, "timezone": "UTC",
        "message_count": count, "completeness": "partial",
    }
    fields["period_end_semantics"] = "exclusive" if end_exclusive else "inclusive indexed bound"
    frontmatter = "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()
    )
    return (
        f"---\n{frontmatter}\n---\n\n# {escape(title)}\n\n## Export information\n\n"
        f"- Period (UTC; end {"exclusive" if end_exclusive else "inclusive"}): {escape(start)} — {escape(end)}\n"
        f"- Messages: {count}\n- Completeness: partial\n\n{WARNING}\n\n## Messages\n\n"
    ).encode("utf-8")


def message(item: dict[str, Any], dialog: Any) -> bytes:
    sender = item.get("sender_name") or item.get("sender_username") or item.get("sender_id") or "Unknown sender"
    lines = [f"### {escape(item['date_utc'])}", "",
             f"**{escape(sender)}** · Message ID: {item['message_id']}", ""]
    if item.get("sender_id") is not None:
        lines += [f"Sender ID: {item['sender_id']}", ""]
    if item.get("sender_username"):
        lines += [f"Username: {escape('@' + str(item['sender_username']))}", ""]
    # Escape each line to preserve literal code and line breaks without allowing injected headings/HTML.
    lines += [escape(item.get("text", "")).replace("\n", "  \n"), ""]
    for key, label in (("reply_to_message_id", "Reply to message"),
                       ("forward_from_peer_id", "Forwarded from peer"),
                       ("edit_date_utc", "Edited (UTC)")):
        if item.get(key) is not None:
            lines += [f"{label}: {escape(item[key])}", ""]
    if item.get("media_type"):
        lines += [f"> [{escape(item['media_type'])} attached: description unavailable]", ""]
        username = dialog["username"]
        if dialog["kind"] in {"group", "channel"} and username and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            lines += [f"[View message in Telegram](https://t.me/{username}/{item['message_id']})", ""]
    reactions = item.get("raw", {}).get("reactions", [])
    if reactions:
        lines += ["**Reactions:** " + " · ".join(
            f"{escape(reaction.get('label', 'Unknown reaction'))} {int(reaction['count'])}"
            for reaction in reactions
        ), ""]
    return ("\n".join(lines) + "\n---\n\n").encode("utf-8")


def footer() -> bytes:
    return f"## Warnings\n\n{WARNING}\n".encode("utf-8")
