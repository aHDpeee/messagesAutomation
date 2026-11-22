"""Validate a Telegram chat reference before resolving or joining it."""
import re
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
        raise ValueError("This link does not identify a public chat or invitation.")
    if re.fullmatch(r"-?[0-9]+", value):
        return "id", value
    if value.startswith("@"):
        if not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", value):
            raise ValueError("Invalid Telegram username.")
        return "username", value[1:]
    return "name", value


async def resolve_and_join(client, reference: str, dialogs: list[dict], *, verify_membership: bool = False) -> dict:
    """Join only as a direct consequence of the user's explicit add action."""
    from telethon import errors, functions, types, utils

    kind, value = parse_chat_reference(reference)
    if kind == "name":
        matches = [d for d in dialogs if value.casefold() in str(d.get("title", "")).casefold()]
        if len(matches) > 1:
            raise ValueError("Several chats match this name; choose one from the catalog.")
        if matches:
            if not verify_membership:
                return {"status": "existing", "dialog": matches[0], "method": kind}
            kind, value = "id", str(matches[0]["id"])
        elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", value):
            kind = "username"
        else:
            raise ValueError("No chat matches this name. Refresh the list first.")
    for dialog in dialogs:
        if not verify_membership and ((kind == "id" and str(dialog.get("id")) == value) or (kind == "username" and str(dialog.get("username") or "").casefold() == value.casefold())):
            return {"status": "existing", "dialog": dialog, "method": kind}

    def record(entity):
        if isinstance(entity, types.User):
            chat_type = "bot" if entity.bot else "chat"
        elif isinstance(entity, types.Channel):
            chat_type = "group" if entity.megagroup else "channel"
        else:
            chat_type = "group"
        title = getattr(entity, "title", None) or " ".join(filter(None, (getattr(entity, "first_name", None), getattr(entity, "last_name", None)))) or str(entity.id)
        return {"id": str(utils.get_peer_id(entity)), "title": title, "type": chat_type, "username": getattr(entity, "username", None) or ""}

    if kind == "invite":
        invitation = await client(functions.messages.CheckChatInviteRequest(value))
        if isinstance(invitation, types.ChatInviteAlready):
            return {"status": "already", "dialog": record(invitation.chat), "method": kind}
        try:
            result = await client(functions.messages.ImportChatInviteRequest(value))
        except (errors.UserAlreadyInvitedError, errors.InviteRequestSentError):
            return {"status": "requested", "title": getattr(invitation, "title", ""), "method": kind}
        except errors.UserAlreadyParticipantError:
            # A membership may have been approved between checking and joining.
            invitation = await client(functions.messages.CheckChatInviteRequest(value))
            if isinstance(invitation, types.ChatInviteAlready):
                return {"status": "already", "dialog": record(invitation.chat), "method": kind}
            raise ValueError("Telegram reports existing membership. Refresh the chat list.")
        chats = getattr(result, "chats", None) or []
        if not chats:
            raise ValueError("Telegram did not return the joined chat; refresh the list.")
        return {"status": "joined", "dialog": record(chats[0]), "method": kind}

    entity = await client.get_entity(int(value) if kind == "id" else value)
    if isinstance(entity, (types.ChannelForbidden, types.ChatForbidden)):
        raise ValueError("The chat is inaccessible to this account.")
    if isinstance(entity, types.Channel):
        try:
            await client(functions.channels.JoinChannelRequest(entity))
        except (errors.UserAlreadyInvitedError, errors.InviteRequestSentError):
            return {"status": "requested", "dialog": record(entity), "method": kind}
        except errors.UserAlreadyParticipantError:
            return {"status": "already", "dialog": record(entity), "method": kind}
        return {"status": "joined", "dialog": record(entity), "method": kind}
    if isinstance(entity, types.Chat) and getattr(entity, "left", False):
        raise ValueError("An invitation link is required to join this group.")
    return {"status": "added", "dialog": record(entity), "method": kind}


async def check_invite_membership(client, reference: str) -> dict:
    """Check an invitation without creating or re-sending a join request."""
    from telethon import functions, types, utils
    kind, value = parse_chat_reference(reference)
    if kind != "invite":
        raise ValueError("An invitation link is required.")
    result = await client(functions.messages.CheckChatInviteRequest(value))
    if isinstance(result, types.ChatInviteAlready):
        return {"status": "already", "chat_id": str(utils.get_peer_id(result.chat))}
    return {"status": "pending"}
