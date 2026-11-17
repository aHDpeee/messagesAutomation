import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from signal_archive.chat_add import parse_chat_reference, resolve_chat, resolve_and_join


class AddChatTests(unittest.TestCase):
    def test_parse_supported_inputs_and_reject_other_sites(self):
        for value, expected in [("@alice", ("username", "alice")), ("-100123", ("id", "-100123")), ("https://t.me/alice/10", ("username", "alice")), ("https://t.me/+abc_def", ("invite", "abc_def"))]:
            self.assertEqual(parse_chat_reference(value), expected)
        with self.assertRaises(ValueError):
            parse_chat_reference("https://evil.example/alice")

    def test_name_selects_unique_local_chat_without_network(self):
        chats = [{"id": "1", "title": "Example group"}]
        record, method = asyncio.run(resolve_chat(None, "Example", chats))
        self.assertEqual((record["id"], method), ("1", "name"))
        with self.assertRaisesRegex(ValueError, "Several"):
            asyncio.run(resolve_chat(None, "Example", chats + [{"id": "2", "title": "Example 2"}]))

    def test_resolves_username_to_dialog_record(self):
        from telethon.tl.types import User
        class Client:
            async def get_entity(self, value):
                self.value = value
                return User(id=123, first_name="Alice", username="alice")
        client = Client()
        record, method = asyncio.run(resolve_chat(client, "alice", []))
        self.assertEqual((record["id"], record["title"], method), ("123", "Alice", "username"))
        self.assertEqual(client.value, "alice")

    def test_invitation_never_joins(self):
        from telethon.tl.types import ChatInvite, ChatPhotoEmpty
        class Client:
            async def __call__(self, request):
                self.request = request
                return ChatInvite(title="Private", photo=ChatPhotoEmpty(), participants_count=5, color=0)
        client = Client()
        with self.assertRaisesRegex(ValueError, "not joined"):
            asyncio.run(resolve_chat(client, "https://t.me/+abc_def", []))
        self.assertEqual(type(client.request).__name__, "CheckChatInviteRequest")


class JoinChatTests(unittest.TestCase):
    def channel(self, **kwargs):
        from telethon.tl.types import Channel
        values = dict(id=100123, title="Example group", photo=None,
                      date=datetime(2026, 1, 1, tzinfo=timezone.utc), megagroup=True,
                      username="example")
        values.update(kwargs)
        return Channel(**values)

    def client(self, entity=None, *, check=None, join_error=None, import_error=None, imported=None):
        from telethon import errors, functions
        from telethon.tl.types import ChatInvite, ChatInviteAlready, ChatPhotoEmpty, Channel, Chat

        class Client:
            def __init__(self):
                self.requests = []
                self.join_error = join_error
                self.import_error = import_error
                self.imported = imported

            async def get_entity(self, value):
                self.requests.append(("get_entity", value))
                if entity is None:
                    raise errors.ChannelPrivateError(None)
                return entity

            async def __call__(self, request):
                self.requests.append(request)
                if isinstance(request, functions.messages.CheckChatInviteRequest):
                    if isinstance(check, Exception):
                        raise check
                    return check if check is not None else ChatInvite(
                        title="Private group", photo=ChatPhotoEmpty(),
                        participants_count=5, color=0, megagroup=True)
                if isinstance(request, functions.channels.JoinChannelRequest):
                    if self.join_error:
                        raise self.join_error
                    return None
                if isinstance(request, functions.messages.ImportChatInviteRequest):
                    if self.import_error:
                        raise self.import_error
                    return SimpleNamespace(chats=self.imported or [])
                raise AssertionError(type(request).__name__)

        return Client()

    def test_username_joins_public_group_and_records_it(self):
        from telethon import functions, utils
        entity = self.channel()
        client = self.client(entity)
        record, method, join = asyncio.run(resolve_and_join(client, "@example", []))
        self.assertEqual((record["id"], record["type"], method), (str(utils.get_peer_id(entity)), "group", "username"))
        self.assertEqual(join["status"], "joined")
        self.assertFalse(join["problem"])
        self.assertTrue(any(isinstance(request, functions.channels.JoinChannelRequest) for request in client.requests))

    def test_already_participant_is_not_a_problem(self):
        from telethon import errors, utils
        entity = self.channel()
        client = self.client(entity, join_error=errors.UserAlreadyParticipantError(None))
        record, method, join = asyncio.run(resolve_and_join(client, "example", []))
        self.assertEqual(join["status"], "already")
        self.assertFalse(join["problem"])
        self.assertEqual(record["id"], str(utils.get_peer_id(entity)))

    def test_private_group_is_problem_without_adding_it(self):
        from telethon import errors
        client = self.client(join_error=None, check=errors.ChannelPrivateError(None))
        record, method, join = asyncio.run(resolve_and_join(client, "-100999", []))
        self.assertIsNone(record)
        self.assertEqual(join["status"], "problem")
        self.assertTrue(join["problem"])
        self.assertIn("приватный", join["message"])

    def test_invite_joins_and_returns_group_record(self):
        from telethon import functions
        client = self.client(imported=[self.channel(title="Invited group")])
        record, method, join = asyncio.run(resolve_and_join(client, "https://t.me/+abc_def", []))
        self.assertEqual(method, "invite")
        self.assertEqual(record["title"], "Invited group")
        self.assertEqual(join["status"], "joined")
        self.assertTrue(any(isinstance(request, functions.messages.ImportChatInviteRequest) for request in client.requests))

    def test_pending_invite_request_is_problem(self):
        from telethon import errors
        client = self.client(import_error=errors.UserAlreadyInvitedError(None))
        record, method, join = asyncio.run(resolve_and_join(client, "https://t.me/joinchat/abc_def", []))
        self.assertIsNone(record)
        self.assertEqual(method, "invite")
        self.assertEqual(join["status"], "requested")
        self.assertTrue(join["problem"])
        self.assertIn("Ожидайте", join["message"])

    def test_invite_request_sent_is_recorded_as_pending(self):
        from telethon import errors
        client = self.client(import_error=errors.InviteRequestSentError(None))
        record, method, join = asyncio.run(resolve_and_join(client, "https://t.me/+abc_def", []))
        self.assertIsNone(record)
        self.assertEqual(join["status"], "requested")

    def test_flood_wait_is_propagated_for_cooldown(self):
        from telethon import errors
        client = self.client(entity=self.channel(), join_error=errors.FloodWaitError(None, capture=42))
        with self.assertRaises(errors.FloodWaitError):
            asyncio.run(resolve_and_join(client, "@example", []))

    def test_expired_invite_is_problem(self):
        from telethon import errors
        client = self.client(check=errors.InviteHashExpiredError(None))
        record, method, join = asyncio.run(resolve_and_join(client, "https://t.me/+abc_def", []))
        self.assertIsNone(record)
        self.assertEqual(join["status"], "problem")
        self.assertIn("истекла", join["message"])

    def test_existing_local_chat_is_not_joined_again(self):
        from telethon import functions
        client = self.client()
        local = {"id": "-100123", "title": "Example group", "type": "group", "username": "example"}
        record, method, join = asyncio.run(resolve_and_join(client, "@example", [local]))
        self.assertEqual((record["id"], method, join["status"]), ("-100123", "username", "existing"))
        self.assertEqual(client.requests, [])
