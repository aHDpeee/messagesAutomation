"""Resolve user-supplied chat identifiers without joining invitations."""
from __future__ import annotations

import re
from types import SimpleNamespace
from urllib.parse import urlparse


def parse_chat_reference(value: str) -> tuple[str, str]:
    value = str(value).strip()
    if not value or len(value) > 2048:
        raise ValueError("Enter a chat link, username, ID, or name.")
    if value.startswith(("https://", "http://")):
        url = urlparse(value)
        if url.scheme != "https" or url.hostname not in {"t.me", "telegram.me", "www.t.me"} or url.username or url.password or url.port:
            raise ValueError("Use a Telegram https://t.me/ link.")
        path = url.path.strip("/")
        if path.startswith("+") and re.fullmatch(r"\+[A-Za-z0-9_-]+", path):
            return "invite", path[1:]
        if path.startswith("joinchat/") and re.fullmatch(r"joinchat/[A-Za-z0-9_-]+", path):
            return "invite", path.split("/", 1)[1]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}(?:/[0-9]+)?", path):
            return "username", path.split("/", 1)[0]
        raise ValueError("This Telegram link does not identify a public chat or invite.")
    if re.fullmatch(r"-?[0-9]+", value):
        return "id", value
    if value.startswith("@"):
        if not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", value):
            raise ValueError("Invalid Telegram username.")
        return "username", value[1:]
    return "name", value


async def resolve_chat(client, reference: str, dialogs: list[dict]) -> tuple[dict | None, str]:
    """Return (record, method); name matching uses only existing local dialogs."""
    from telethon import functions, types, utils
    from .telegram_ops import _dialog_record

    kind, value = parse_chat_reference(reference)
    if kind == "name":
        matches = [d for d in dialogs if value.casefold() in str(d.get("title", "")).casefold()]
        if len(matches) > 1:
            raise ValueError("Several chats match this name; choose one from the chat list.")
        if matches:
            return dict(matches[0]), "name"
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", value):
            kind = "username"
        else:
            raise ValueError("No known chat matches this name. Refresh the chat list first.")
    if kind == "invite":
        invite = await client(functions.messages.CheckChatInviteRequest(value))
        if not isinstance(invite, types.ChatInviteAlready):
            raise ValueError("This invite points to a group you have not joined. Open it in Telegram and choose whether to join.")
        entity = invite.chat
    else:
        entity = await client.get_entity(int(value) if kind == "id" else value)
    ident = utils.get_peer_id(entity)
    record = _dialog_record(SimpleNamespace(id=ident, entity=entity, unread_count=0, folder_id=None, date=None), set())
    return record, kind
