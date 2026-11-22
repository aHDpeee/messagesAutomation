"""Telethon client lifecycle confined to the Observatory asyncio runtime."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tg_observatory.exceptions import AuthenticationRequired, ConversationUnavailable, InvalidRequest
from tg_observatory.models.account import AccountProfile
from tg_observatory.models.dialog import DialogSummary
from tg_observatory.storage.time import utc_now

ClientFactory = Callable[[Any, int, str], Any]


async def _user_authorized(client: Any) -> bool:
    """Do not mistake Telegram rate limits or other RPC failures for logout."""
    from telethon import errors, functions
    try:
        await client(functions.updates.GetStateRequest())
        return True
    except errors.UnauthorizedError:
        return False


class TelegramGateway:
    def __init__(self, session_path: str | Path, client_factory: ClientFactory | None = None,
                 session_store: Any | None = None) -> None:
        self._session_path = str(session_path)
        self._client_factory = client_factory or self._create_telethon_client
        self._session_store = session_store
        self._client: Any | None = None
        self._credentials: tuple[int, str] | None = None
        self._pending_phone: str | None = None
        self._pending_code_hash: str | None = None
        self._qr_task: asyncio.Task | None = None
        self._last_logout_revoked = False

    @property
    def remote_logout_succeeded(self) -> bool:
        return self._last_logout_revoked

    @staticmethod
    def _create_telethon_client(session: Any, api_id: int, api_hash: str) -> Any:
        from telethon import TelegramClient  # type: ignore[import-untyped]

        return TelegramClient(session, api_id, api_hash, flood_sleep_threshold=0)

    async def _connected_client(self, api_id: int, api_hash: str) -> Any:
        credentials = (api_id, api_hash)
        if self._client is not None and self._credentials != credentials:
            await self._client.disconnect()
            self._client = None
        if self._client is None:
            Path(self._session_path).parent.mkdir(parents=True, exist_ok=True)
            session: Any = self._session_path
            if self._session_store is not None:
                from tg_observatory.telegram.session import protected_session

                session = protected_session(self._session_store, self._session_path)
            self._client = self._client_factory(session, api_id, api_hash)
            self._credentials = credentials
            # Telethon 1.45 reports RPC errors to a public endpoint by default.
            # This local-first app explicitly opts out.
            session = getattr(self._client, "session", None)
            if session is not None and hasattr(session, "report_errors"):
                session.report_errors = False
        if not self._client.is_connected():
            await self._client.connect()
        return self._client

    async def request_code(self, api_id: int, api_hash: str, phone: str) -> dict[str, Any]:
        client = await self._connected_client(api_id, api_hash)
        sent_code = await client.send_code_request(phone)
        self._pending_phone = phone
        self._pending_code_hash = sent_code.phone_code_hash
        delivery = {
            "SentCodeTypeApp": "telegram", "SentCodeTypeSms": "sms",
            "SentCodeTypeSmsWord": "sms", "SentCodeTypeSmsPhrase": "sms",
            "SentCodeTypeFirebaseSms": "sms", "SentCodeTypeFragmentSms": "fragment_sms",
            "SentCodeTypeCall": "call", "SentCodeTypeFlashCall": "call",
            "SentCodeTypeMissedCall": "call", "SentCodeTypeEmailCode": "email",
            "SentCodeTypeSetUpEmailRequired": "email_setup",
        }.get(type(sent_code.type).__name__, "unknown")
        return {"delivery": delivery, "timeout": getattr(sent_code, "timeout", None)}

    async def start_qr_login(self, api_id: int, api_hash: str) -> dict[str, str]:
        client = await self._connected_client(api_id, api_hash)
        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
            await asyncio.gather(self._qr_task, return_exceptions=True)
        qr = await client.qr_login()
        # Telethon must install its update handler before the code is displayed.
        self._qr_task = asyncio.create_task(qr.wait())
        await asyncio.sleep(0)
        return {"url": qr.url, "expires": qr.expires.isoformat()}

    async def finish_qr_login(self) -> tuple[str, AccountProfile | None]:
        task = self._qr_task
        if task is None:
            raise InvalidRequest("Start QR login first.")
        try:
            await task
        except asyncio.TimeoutError:
            return "expired", None
        except Exception as error:
            if type(error).__name__ == "SessionPasswordNeededError":
                return "password_required", None
            raise
        finally:
            if self._qr_task is task:
                self._qr_task = None
        return "authenticated", await self._profile(self._client, None)

    async def sign_in_code(
        self, api_id: int, api_hash: str, code: str
    ) -> tuple[str, AccountProfile | None]:
        client = await self._connected_client(api_id, api_hash)
        if self._pending_phone is None or self._pending_code_hash is None:
            raise ValueError("Request a new verification code first.")
        try:
            await client.sign_in(
                phone=self._pending_phone,
                code=code,
                phone_code_hash=self._pending_code_hash,
            )
        except Exception as error:
            if type(error).__name__ == "SessionPasswordNeededError":
                return "password_required", None
            raise
        phone = self._pending_phone
        self._pending_phone = None
        self._pending_code_hash = None
        return "authenticated", await self._profile(client, phone)

    async def sign_in_password(
        self, api_id: int, api_hash: str, password: str
    ) -> AccountProfile:
        client = await self._connected_client(api_id, api_hash)
        await client.sign_in(password=password)
        phone_hint = self._pending_phone
        self._pending_phone = None
        self._pending_code_hash = None
        return await self._profile(client, phone_hint)

    async def account_status(self, api_id: int, api_hash: str) -> AccountProfile | None:
        client = await self._connected_client(api_id, api_hash)
        if not await _user_authorized(client):
            return None
        return await self._profile(client, self._pending_phone)

    async def _profile(self, client: Any, phone: str | None) -> AccountProfile:
        user = await client.get_me()
        phone_hint = f"…{phone[-4:]}" if phone else None
        return AccountProfile(
            telegram_id=int(user.id),
            username=getattr(user, "username", None),
            first_name=getattr(user, "first_name", None),
            last_name=getattr(user, "last_name", None),
            phone_hint=phone_hint,
        )

    async def iter_dialogs(
        self, api_id: int, api_hash: str, *, limit: int | None = None,
    ) -> AsyncIterator[DialogSummary]:
        client = await self._connected_client(api_id, api_hash)
        if not await _user_authorized(client):
            raise AuthenticationRequired()
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            if dialog.is_user:
                kind = "bot" if getattr(entity, "bot", False) else "private"
            elif dialog.is_group:
                kind = "group"
            elif dialog.is_channel and getattr(entity, "megagroup", False):
                kind = "group"
            elif dialog.is_channel:
                kind = "channel"
            else:
                kind = "unknown"
            last_message = getattr(dialog, "message", None)
            last_date = getattr(last_message, "date", None)
            if last_date is not None:
                last_date = _as_utc(last_date)
            yield DialogSummary(
                telegram_id=int(dialog.id),
                raw_telegram_id=int(entity.id),
                kind=kind,
                title=str(dialog.title or ""),
                username=getattr(entity, "username", None),
                unread_count=int(getattr(dialog, "unread_count", 0) or 0),
                participant_count=getattr(entity, "participants_count", None),
                last_message_at=last_date.isoformat().replace("+00:00", "Z")
                if last_date is not None
                else None,
                last_message_present=(last_message is not None) if hasattr(dialog, "message") else None,
                last_message_out=getattr(last_message, "out", None) if last_message is not None else None,
            )

    async def iter_messages(
        self, api_id: int, api_hash: str, dialog_id: int, limit: int,
        *, max_id: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield the newest accessible messages without retaining history in memory."""
        client = await self._connected_client(api_id, api_hash)
        if not await _user_authorized(client):
            raise AuthenticationRequired()
        peer = await self._input_peer(client, dialog_id)
        async for message in client.iter_messages(peer, limit=limit, max_id=max_id or 0):
            if getattr(message, "id", None) is None:
                continue
            yield _message_data(message)

    async def preview_range_page(
        self, api_id: int, api_hash: str, dialog_id: int, *,
        after_id: int | None = None, period_start: datetime | None = None,
        period_end: datetime | None = None, limit: int = 100,
    ) -> dict[str, Any]:
        """Fetch at most one bounded chronological preview page directly from Telegram."""
        if not 1 <= limit <= 100:
            raise InvalidRequest("Preview page size must be between 1 and 100.")
        client = await self._connected_client(api_id, api_hash)
        if not await _user_authorized(client):
            raise AuthenticationRequired()
        peer = await self._input_peer(client, dialog_id)
        start = _as_utc(period_start) if period_start is not None else None
        end = _as_utc(period_end) if period_end is not None else datetime.now(UTC)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        latest_timestamp = epoch + timedelta(seconds=2**31 - 1)
        if start is not None and not epoch + timedelta(seconds=1) <= start <= latest_timestamp:
            raise InvalidRequest("Telegram preview start must be between 1970-01-01T00:00:01Z and 2038-01-19T03:14:07Z.")
        if not epoch <= end <= latest_timestamp:
            raise InvalidRequest("Telegram preview end must be between 1970-01-01T00:00:00Z and 2038-01-19T03:14:07Z.")
        if start is not None and start >= end:
            raise InvalidRequest("Period start must be earlier than period end.")
        # Telethon's reverse offset_date is exclusive at Telegram's second precision.
        # Overlap one second, then filter precisely to keep start-inclusive semantics.
        try:
            offset = start - timedelta(seconds=1) if start and after_id is None else None
        except OverflowError as error:
            raise InvalidRequest("Period start is outside Telegram's supported range.") from error
        items: list[dict[str, Any]] = []
        received = 0
        cursor = after_id
        last_id = after_id or 0
        reached_end = False
        async for message in client.iter_messages(
            peer, reverse=True, offset_date=offset, min_id=after_id or 0, limit=limit + 1,
        ):
            await asyncio.sleep(0)
            received += 1
            identifier = getattr(message, "id", None)
            date = getattr(message, "date", None)
            if identifier is None or date is None:
                raise InvalidRequest("Telegram returned a message without an ID or date. Retry the export.")
            cursor = int(identifier)
            if cursor <= last_id:
                raise InvalidRequest("Telegram returned messages out of order. Retry the export.")
            last_id = cursor
            timestamp = _as_utc(date)
            if timestamp >= end:
                reached_end = True
                break
            if start is not None and timestamp < start:
                continue
            items.append(_message_data(message))
        has_extra = len(items) > limit
        if has_extra:
            items = items[:limit]
            cursor = max(int(item["message_id"]) for item in items)
        items.sort(key=lambda item: (item["date_utc"], item["message_id"]))
        return {
            "items": items, "has_more": has_extra or (received == limit + 1 and not reached_end),
            "next_after_id": cursor, "total": None, "earliest": None, "latest": None,
            "source": "telegram",
        }

    async def iter_participants(
        self, api_id: int, api_hash: str, dialog_id: int
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield Telegram's accessible participant records for a dialog."""
        client = await self._connected_client(api_id, api_hash)
        if not await _user_authorized(client):
            raise AuthenticationRequired()
        peer = await self._input_peer(client, dialog_id)
        async for user in client.iter_participants(peer):
            user_id = getattr(user, "id", None)
            if user_id is None:
                continue
            yield {
                "telegram_id": int(user_id),
                "username": getattr(user, "username", None),
                "first_name": getattr(user, "first_name", None),
                "last_name": getattr(user, "last_name", None),
                "bot": bool(getattr(user, "bot", False)),
                "deleted": bool(getattr(user, "deleted", False)),
            }

    async def iter_member_profiles(
        self, api_id: int, api_hash: str, dialog_id: int, *, include_profiles: bool = True,
        max_profile_details: int | None = None, profile_delay: float = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Enrich only members exposed by Telegram, with explicit per-field availability."""
        from telethon import errors
        from telethon.tl.functions.users import GetFullUserRequest  # type: ignore[import-untyped]

        client = await self._connected_client(api_id, api_hash)
        if not await _user_authorized(client):
            raise AuthenticationRequired()
        peer = await self._input_peer(client, dialog_id)
        profile_attempts = 0
        async for user in client.iter_participants(peer):
            await asyncio.sleep(0)
            user_id = getattr(user, "id", None)
            if user_id is None:
                continue
            fields = {
                name: _profile_field(user, name)
                for name in ("first_name", "last_name", "username", "phone", "bot", "premium", "verified", "contact")
            }
            fields["profile_photo"] = _photo_field(user, "photo")
            username = getattr(user, "username", None)
            fields["public_link"] = (
                {"state": "AVAILABLE", "value": f"https://t.me/{username}"}
                if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username)
                else {"state": "NOT_EXPOSED", "value": None}
            )
            for name in ("bio", "personal_channel", "saved_music"):
                fields[name] = {"state": "NOT_FETCHED", "value": None}
            if not include_profiles or (max_profile_details is not None and profile_attempts >= max_profile_details):
                yield {"telegram_id": int(user_id), "fields": fields}
                continue
            if profile_attempts and profile_delay:
                await asyncio.sleep(profile_delay)
            profile_attempts += 1
            try:
                result = await client(GetFullUserRequest(user))
                full = result.full_user
                fields["bio"] = _profile_field(full, "about")
                fields["personal_channel"] = _personal_channel_field(full, getattr(result, "chats", []))
                fields["saved_music"] = _music_field(full)
                if getattr(full, "profile_photo", None) is not None:
                    fields["profile_photo"] = _photo_field(full, "profile_photo")
                enriched = next((item for item in getattr(result, "users", []) if item.id == user_id), None)
                if enriched is not None:
                    for name in ("first_name", "last_name", "username", "phone", "bot", "premium", "verified", "contact"):
                        fields[name] = _profile_field(enriched, name)
            except (errors.UserPrivacyRestrictedError, errors.UserIdInvalidError,
                    errors.PeerIdInvalidError, errors.UserDeactivatedError,
                    errors.UserDeactivatedBanError, errors.UserNotMutualContactError):
                for name in ("bio", "personal_channel", "saved_music"):
                    fields[name] = {"state": "RETRIEVAL_FAILED", "value": None}
            yield {"telegram_id": int(user_id), "fields": fields}

    async def resolve_username(
        self, api_id: int, api_hash: str, username: str
    ) -> dict[str, Any]:
        """Resolve a public username and return only profile fields Telegram exposes."""
        client = await self._connected_client(api_id, api_hash)
        if not await _user_authorized(client):
            raise AuthenticationRequired()
        entity = await client.get_entity(username.lstrip("@"))
        return {
            "telegram_id": int(entity.id),
            "username": getattr(entity, "username", None),
            "first_name": getattr(entity, "first_name", None),
            "last_name": getattr(entity, "last_name", None),
            "bot": bool(getattr(entity, "bot", False)),
            "deleted": bool(getattr(entity, "deleted", False)),
        }

    async def download_message_media(
        self,
        api_id: int,
        api_hash: str,
        dialog_id: int,
        message_id: int,
        destination: str | Path,
        *,
        max_bytes: int,
        progress_callback: Callable[[int, int], None],
    ) -> dict[str, Any] | None:
        """Download one accessible message attachment to the caller's private path."""
        client = await self._connected_client(api_id, api_hash)
        if not await _user_authorized(client):
            raise AuthenticationRequired()
        peer = await self._input_peer(client, dialog_id)
        message = await client.get_messages(peer, ids=message_id)
        if message is None or getattr(message, "media", None) is None:
            return None
        file_info = getattr(message, "file", None)
        size = getattr(file_info, "size", None)
        mime_type = getattr(file_info, "mime_type", None)
        filename = getattr(file_info, "name", None)
        media_type = _media_type(message)
        if size is not None and int(size) > max_bytes:
            return {
                "filename": filename,
                "mime_type": mime_type,
                "size_bytes": int(size),
                "kind": media_type or "other",
                "skipped": "size_limit",
            }
        result = await client.download_media(
            message,
            file=str(destination),
            progress_callback=progress_callback,
        )
        if result is None:
            return None
        return {
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": int(size) if size is not None else None,
            "kind": media_type or "other",
        }

    @staticmethod
    async def _input_peer(client: Any, dialog_id: int) -> Any:
        """Rehydrate access hashes after restoring an in-memory authorization session.

        StringSession intentionally does not persist Telethon's entity cache. Resolve from
        currently accessible dialogs only; do not guess hashes or persist them in exports.
        RPC failures, including FloodWait, propagate to the job's normal retry handling.
        """
        resolver = getattr(client, "get_input_entity", None)
        if resolver is None:
            # Alternate gateway adapters may accept stable Telegram IDs directly.
            return dialog_id
        try:
            return await resolver(dialog_id)
        except ValueError:
            pass
        async for dialog in client.iter_dialogs():
            await asyncio.sleep(0)
            if int(dialog.id) == dialog_id:
                input_entity = getattr(dialog, "input_entity", None)
                return input_entity if input_entity is not None else await resolver(dialog.entity)
        raise ConversationUnavailable()

    async def logout(self) -> None:
        client = self._client
        revoked = False
        try:
            if client is not None:
                try:
                    if client.is_connected():
                        await client.log_out()
                        revoked = True
                except Exception:
                    # A network failure must not prevent deletion of local authorization.
                    pass
                finally:
                    try:
                        await client.disconnect()
                    finally:
                        session = getattr(client, "session", None)
                        if session is not None and hasattr(session, "delete"):
                            session.delete()
        finally:
            self._client = None
            self._credentials = None
            self._pending_phone = None
            self._pending_code_hash = None
            self._last_logout_revoked = revoked
            try:
                if self._session_store is not None:
                    self._session_store.clear()
            finally:
                session_file = Path(f"{self._session_path}.session")
                for suffix in ("", "-journal", "-wal", "-shm"):
                    Path(f"{session_file}{suffix}").unlink(missing_ok=True)

    async def disconnect(self) -> None:
        """Close the current transport without revoking the persisted session."""
        if self._qr_task is not None:
            self._qr_task.cancel()
            await asyncio.gather(self._qr_task, return_exceptions=True)
            self._qr_task = None
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None
                self._credentials = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _profile_field(source: Any, name: str) -> dict[str, Any]:
    if not hasattr(source, name) or getattr(source, name) is None:
        return {"state": "NOT_EXPOSED", "value": None}
    value = getattr(source, name)
    if not isinstance(value, (str, bool, int, float)):
        return {"state": "NOT_EXPOSED", "value": None}
    return {"state": "EMPTY" if value == "" else "AVAILABLE", "value": value}


def _photo_field(source: Any, name: str) -> dict[str, Any]:
    photo = getattr(source, name, None)
    if photo is None:
        return {"state": "NOT_EXPOSED", "value": None}
    if type(photo).__name__.endswith("Empty"):
        return {"state": "EMPTY", "value": None}
    photo_id = getattr(photo, "photo_id", None) or getattr(photo, "id", None)
    return ({"state": "AVAILABLE", "value": {"photo_id": int(photo_id)}}
            if photo_id is not None else {"state": "NOT_EXPOSED", "value": None})


def _personal_channel_field(full: Any, chats: Any) -> dict[str, Any]:
    if not hasattr(full, "personal_channel_id") or full.personal_channel_id is None:
        return {"state": "NOT_EXPOSED", "value": None}
    channel_id = int(full.personal_channel_id)
    value: dict[str, Any] = {"telegram_channel_id": channel_id}
    for chat in chats:
        if getattr(chat, "id", None) == channel_id:
            for field in ("title", "username"):
                if isinstance(getattr(chat, field, None), str):
                    value[field] = getattr(chat, field)
            break
    message_id = getattr(full, "personal_channel_message", None)
    if message_id is not None:
        value["message_id"] = int(message_id)
    return {"state": "AVAILABLE", "value": value}


def _music_field(full: Any) -> dict[str, Any]:
    music = getattr(full, "saved_music", None)
    if music is None:
        return {"state": "NOT_EXPOSED", "value": None}
    document_id = getattr(music, "id", None)
    if document_id is None:
        return {"state": "NOT_EXPOSED", "value": None}
    value: dict[str, Any] = {"document_id": int(document_id)}
    if isinstance(getattr(music, "mime_type", None), str):
        value["mime_type"] = music.mime_type
    for attribute in getattr(music, "attributes", []):
        for name in ("title", "performer", "duration", "file_name"):
            if isinstance(getattr(attribute, name, None), (str, int, float)):
                value[name] = getattr(attribute, name)
    return {"state": "AVAILABLE", "value": value}


def _display_name(sender: Any) -> str | None:
    if sender is None:
        return None
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(part for part in parts if part)
    return name or getattr(sender, "title", None)


def _media_type(message: Any) -> str | None:
    media = getattr(message, "media", None)
    if media is None:
        return None
    name = type(media).__name__.lower()
    for kind in ("photo", "document", "webpage", "geo", "contact", "poll", "game"):
        if kind in name:
            return kind
    return "other"


def _entity_data(entity: Any) -> dict[str, Any]:
    return {
        "type": type(entity).__name__,
        "offset": getattr(entity, "offset", None),
        "length": getattr(entity, "length", None),
        "url": getattr(entity, "url", None),
        "user_id": getattr(entity, "user_id", None),
        "language": getattr(entity, "lang_code", None),
    }


def _message_data(message: Any) -> dict[str, Any]:
    """Normalize once for indexing and live previews; never expose raw Telethon state."""
    sender = getattr(message, "sender", None)
    date = getattr(message, "date", None)
    edit_date = getattr(message, "edit_date", None)
    forward = getattr(message, "fwd_from", None)
    forward_peer = getattr(forward, "from_id", None)
    if forward_peer is not None:
        forward_peer = getattr(forward_peer, "user_id", None) or getattr(
            forward_peer, "channel_id", None
        ) or getattr(forward_peer, "chat_id", None)
    text = getattr(message, "message", None)
    return {
        "message_id": int(message.id),
        "date_utc": _as_utc(date).isoformat().replace("+00:00", "Z") if date else utc_now(),
        "text": text,
        "sender_id": int(message.sender_id) if getattr(message, "sender_id", None) else None,
        "sender_username": getattr(sender, "username", None),
        "sender_name": _display_name(sender),
        "reply_to_message_id": getattr(message, "reply_to_msg_id", None),
        "edit_date_utc": _as_utc(edit_date).isoformat().replace("+00:00", "Z") if edit_date else None,
        "forward_from_peer_id": int(forward_peer) if forward_peer is not None else None,
        "media_type": _media_type(message),
        "raw": {
            "reactions": [
                {
                    "label": (getattr(result.reaction, "emoticon", None)
                              or (f"Custom emoji {result.reaction.document_id}"
                                  if getattr(result.reaction, "document_id", None) else "Unknown reaction")),
                    "count": int(result.count),
                }
                for result in (getattr(getattr(message, "reactions", None), "results", None) or [])
            ],
            "grouped_id": getattr(message, "grouped_id", None),
            "views": getattr(message, "views", None),
            "forwards": getattr(message, "forwards", None),
            "post_author": getattr(message, "post_author", None),
            "entities": [_entity_data(entity) for entity in (getattr(message, "entities", None) or [])],
        },
    }
