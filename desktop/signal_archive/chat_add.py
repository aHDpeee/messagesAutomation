"""Resolve user-supplied chat identifiers and join public chats when possible."""
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


def _join_info(status: str, message: str) -> dict[str, str]:
    return {"status": status, "message": message, "problem": status in {"requested", "problem"}}


def _local_dialog(dialogs: list[dict], kind: str, value: str) -> dict | None:
    for dialog in dialogs:
        if kind == "id" and str(dialog.get("id")) == value:
            return dialog
        if kind == "username" and str(dialog.get("username") or "").casefold() == value.casefold():
            return dialog
    return None


def _record(client, entity) -> dict:
    from telethon import utils
    from .telegram_ops import _dialog_record
    ident = utils.get_peer_id(entity)
    return _dialog_record(SimpleNamespace(id=ident, entity=entity, unread_count=0, folder_id=None, date=None), set())


def _rpc_message(exc: Exception) -> str:
    from telethon import errors
    if isinstance(exc, errors.ChannelPrivateError):
        return "Этот чат приватный или недоступен текущему аккаунту."
    if isinstance(exc, (errors.ChannelInvalidError, errors.ChatInvalidError, errors.PeerIdInvalidError,
                        errors.UsernameNotOccupiedError, errors.UsernameInvalidError)):
        return "Telegram не нашёл такой чат или ссылка больше не действует."
    if isinstance(exc, (errors.InviteHashExpiredError, errors.InviteHashInvalidError, errors.InviteHashEmptyError)):
        return "Ссылка-приглашение недействительна или истекла."
    if isinstance(exc, errors.UserBannedInChannelError):
        return "Этот аккаунт заблокирован в чате."
    if isinstance(exc, errors.ChatAdminRequiredError):
        return "Для вступления нужны права администратора."
    return f"Telegram отклонил вступление ({type(exc).__name__})."


def _raise_wait(exc: Exception) -> None:
    """Let the service persist Telegram's required cooldown."""
    from telethon import errors
    if isinstance(exc, (errors.FloodWaitError, errors.FloodPremiumWaitError,
                        errors.SlowModeWaitError, errors.FloodTestPhoneWaitError)):
        raise exc


async def _join_channel(client, entity, *, name: str = "чат") -> dict[str, str]:
    from telethon import errors, functions
    try:
        await client(functions.channels.JoinChannelRequest(entity))
        return _join_info("joined", f"Вы вступили в «{name}».")
    except errors.UserAlreadyParticipantError:
        return _join_info("already", f"Аккаунт уже участвует в «{name}».")
    except (errors.UserAlreadyInvitedError, errors.InviteRequestSentError):
        return _join_info("requested", f"Запрос на вступление в «{name}» отправлен. Ожидайте одобрения администратором.")
    except errors.RPCError as exc:
        _raise_wait(exc)
        return _join_info("problem", _rpc_message(exc))


async def resolve_chat(client, reference: str, dialogs: list[dict]) -> tuple[dict | None, str]:
    """Resolve without joining; kept for local-name lookups and tests."""
    from telethon import functions, types
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
    return _record(client, entity), kind


async def resolve_and_join(client, reference: str, dialogs: list[dict]) -> tuple[dict | None, str, dict[str, str]]:
    """Return (record, method, join result), joining or requesting when needed."""
    from telethon import errors, functions, types

    kind, value = parse_chat_reference(reference)
    if kind == "name":
        matches = [d for d in dialogs if value.casefold() in str(d.get("title", "")).casefold()]
        if len(matches) > 1:
            raise ValueError("Several chats match this name; choose one from the chat list.")
        if matches:
            return dict(matches[0]), "name", _join_info("existing", "Чат уже есть в списке.")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", value):
            kind = "username"
        else:
            raise ValueError("No known chat matches this name. Refresh the chat list first.")

    existing = _local_dialog(dialogs, kind, value)
    if existing is not None:
        return dict(existing), kind, _join_info("existing", "Чат уже есть в списке.")

    if kind == "invite":
        try:
            invite = await client(functions.messages.CheckChatInviteRequest(value))
        except errors.RPCError as exc:
            _raise_wait(exc)
            return None, kind, _join_info("problem", _rpc_message(exc))
        if isinstance(invite, types.ChatInviteAlready):
            record = _record(client, invite.chat)
            return record, kind, _join_info("already", "Аккаунт уже участвует в этом чате.")
        title = str(getattr(invite, "title", None) or value)
        guessed_type = "channel" if getattr(invite, "broadcast", False) else "group"
        try:
            result = await client(functions.messages.ImportChatInviteRequest(value))
        except (errors.UserAlreadyInvitedError, errors.InviteRequestSentError):
            return (None, kind, _join_info("requested",
                    f"Запрос на вступление в «{title}» отправлен. Ожидайте одобрения администратором."))
        except errors.UserAlreadyParticipantError:
            return None, kind, _join_info("already", "Аккаунт уже участвует в этом чате.")
        except errors.RPCError as exc:
            _raise_wait(exc)
            return (None, kind, _join_info("problem", _rpc_message(exc)))
        chats = list(getattr(result, "chats", ()) or ())
        if chats:
            record = _record(client, chats[0])
            return record, kind, _join_info("joined", f"Вы вступили в «{record['title']}».")
        return (None, kind, _join_info("problem", f"Telegram не вернул данные чата «{title}». Обновите список чатов."))

    try:
        entity = await client.get_entity(int(value) if kind == "id" else value)
    except errors.RPCError as exc:
        _raise_wait(exc)
        return None, kind, _join_info("problem", _rpc_message(exc))
    except ValueError:
        return None, kind, _join_info("problem", "Telegram не нашёл этот чат. Проверьте ссылку или имя.")
    record = _record(client, entity)
    if isinstance(entity, types.Channel):
        join = await _join_channel(client, entity, name=record["title"])
    elif isinstance(entity, (types.ChannelForbidden, types.ChatForbidden)):
        join = _join_info("problem", "Этот чат недоступен текущему аккаунту.")
    elif isinstance(entity, types.Chat) and getattr(entity, "left", False):
        join = _join_info("problem", "Для вступления в эту группу нужна ссылка-приглашение.")
    else:
        join = _join_info("added", f"Чат «{record['title']}» добавлен.")
    return record, kind, join
