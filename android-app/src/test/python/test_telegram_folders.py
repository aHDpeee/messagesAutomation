import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from telethon.tl import types
from tg_observatory.telegram import folders


class FolderDiscoveryTests(unittest.TestCase):
    def test_inclusion_exclusion_and_unknown_mute(self):
        folder = types.DialogFilter(id=2, title=types.TextWithEntities(text="Work", entities=[]), pinned_peers=[], include_peers=[types.InputPeerUser(user_id=5, access_hash=7)], exclude_peers=[])
        dialog = SimpleNamespace(id=5, entity=types.User(id=5), dialog=SimpleNamespace(notify_settings=None))
        self.assertTrue(folders._matches(folder, dialog, datetime.now(timezone.utc)))
        folder.exclude_peers = [types.InputPeerUser(user_id=5, access_hash=7)]
        self.assertFalse(folders._matches(folder, dialog, datetime.now(timezone.utc)))
        folder.include_peers = []; folder.exclude_peers = []; folder.non_contacts = True; folder.exclude_muted = True
        self.assertIsNone(folders._matches(folder, dialog, datetime.now(timezone.utc)))

    def test_missing_peers_reported_as_uncertain(self):
        folder = types.DialogFilter(id=2, title=types.TextWithEntities(text="Work", entities=[]), pinned_peers=[], include_peers=[types.InputPeerUser(user_id=5, access_hash=7)], exclude_peers=[])
        class Client:
            async def __call__(self, request): return types.messages.DialogFilters(filters=[folder])
            async def iter_dialogs(self):
                if False: yield None
        self.assertEqual(asyncio.run(folders.discover(Client()))[0]["uncertain"], 1)
