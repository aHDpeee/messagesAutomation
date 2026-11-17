import asyncio
import unittest
from types import SimpleNamespace

from signal_archive.chat_add import parse_chat_reference, resolve_chat


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
