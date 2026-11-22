import unittest
import asyncio
from unittest.mock import AsyncMock

from tg_observatory.telegram.chat_reference import parse_chat_reference, resolve_and_join, check_invite_membership


class ChatReferenceTests(unittest.TestCase):
    def test_supported_references(self):
        self.assertEqual(parse_chat_reference("@example"), ("username", "example"))
        self.assertEqual(parse_chat_reference("https://t.me/example/12"), ("username", "example"))
        self.assertEqual(parse_chat_reference("https://t.me/+invite_123"), ("invite", "invite_123"))
        self.assertEqual(parse_chat_reference("-100123"), ("id", "-100123"))
        self.assertEqual(parse_chat_reference("My chat"), ("name", "My chat"))

    def test_rejects_impostor_links(self):
        for reference in ("https://t.me.evil.example/example", "https://t.me@evil.example/example", "http://t.me/example"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                parse_chat_reference(reference)

    def test_existing_chat_does_not_join_again(self):
        client = AsyncMock()
        result = asyncio.run(resolve_and_join(client, "@example", [{"id": "1", "title": "Example", "username": "example"}]))
        self.assertEqual(result["status"], "existing")
        client.assert_not_awaited()

    def test_channel_join_and_request(self):
        from telethon import errors, types
        entity = types.Channel(id=123, title="News", photo=types.ChatPhotoEmpty(), date=None, broadcast=True, username="example")
        class Client:
            async def get_entity(self, value): return entity
            async def __call__(self, request):
                if self.pending:
                    raise errors.InviteRequestSentError(request=request)
        client = Client()
        client.pending = False
        joined = asyncio.run(resolve_and_join(client, "@example", []))
        self.assertEqual((joined["status"], joined["dialog"]["type"]), ("joined", "channel"))
        client.pending = True
        requested = asyncio.run(resolve_and_join(client, "@example", []))
        self.assertEqual(requested["status"], "requested")
        client.pending = False
        verified = asyncio.run(resolve_and_join(client, "@example", [{"id": "-100123", "title": "News", "username": "example"}], verify_membership=True))
        self.assertEqual(verified["status"], "joined")

    def test_invite_approved_between_check_and_join(self):
        from telethon import errors, functions, types
        entity = types.Channel(id=123, title="News", photo=types.ChatPhotoEmpty(), date=None, broadcast=True)
        class Client:
            checks = 0
            async def __call__(self, request):
                if isinstance(request, functions.messages.CheckChatInviteRequest):
                    self.checks += 1
                    if self.checks == 1:
                        return types.ChatInvite(title="News", photo=types.PhotoEmpty(id=0), participants_count=1, color=0)
                    return types.ChatInviteAlready(chat=entity)
                raise errors.UserAlreadyParticipantError(request=request)
        result = asyncio.run(resolve_and_join(Client(), "https://t.me/+invite", []))
        self.assertEqual((result["status"], result["dialog"]["title"]), ("already", "News"))

    def test_check_invite_never_sends_join_request(self):
        from telethon import functions, types, utils
        entity = types.Channel(id=123, title="News", photo=types.ChatPhotoEmpty(), date=None, broadcast=True)
        class Client:
            async def __call__(self, request):
                self.assert_check(request)
                return types.ChatInviteAlready(chat=entity)
            def assert_check(self, request):
                assert isinstance(request, functions.messages.CheckChatInviteRequest)
        self.assertEqual(asyncio.run(check_invite_membership(Client(), "https://t.me/+invite")), {"status": "already", "chat_id": str(utils.get_peer_id(entity))})
        with self.assertRaises(ValueError):
            asyncio.run(check_invite_membership(Client(), "@example"))


if __name__ == "__main__":
    unittest.main()
