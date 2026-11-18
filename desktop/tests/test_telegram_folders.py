import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from telethon.tl import types
from signal_archive import catalog, telegram_folders
from signal_archive.direct_service import DirectService


class FolderImportTests(unittest.TestCase):
    def folder(self, **kwargs):
        return types.DialogFilter(id=2, title=types.TextWithEntities(text="Work", entities=[]),
            pinned_peers=kwargs.pop("pinned_peers", []), include_peers=kwargs.pop("include_peers", []),
            exclude_peers=kwargs.pop("exclude_peers", []), **kwargs)

    def test_explicit_includes_and_excludes(self):
        user = types.User(id=5, first_name="A")
        dialog = SimpleNamespace(id=5, entity=user)
        folder = self.folder(include_peers=[types.InputPeerUser(user_id=5, access_hash=7)])
        self.assertTrue(telegram_folders._matches(folder, dialog, datetime.now(timezone.utc)))
        folder.exclude_peers = [types.InputPeerUser(user_id=5, access_hash=7)]
        self.assertFalse(telegram_folders._matches(folder, dialog, datetime.now(timezone.utc)))

    def test_dynamic_unknown_mute_is_not_silently_imported(self):
        dialog = SimpleNamespace(id=5, entity=types.User(id=5), dialog=SimpleNamespace(notify_settings=None))
        self.assertIsNone(telegram_folders._matches(self.folder(non_contacts=True, exclude_muted=True), dialog, datetime.now(timezone.utc)))

    def test_explicit_saved_messages_matches_self(self):
        dialog = SimpleNamespace(id=5, entity=types.User(id=5, is_self=True))
        folder = self.folder(include_peers=[types.InputPeerSelf()])
        self.assertTrue(telegram_folders._matches(folder, dialog, datetime.now(timezone.utc)))
        folder.exclude_peers = [types.InputPeerSelf()]
        self.assertFalse(telegram_folders._matches(folder, dialog, datetime.now(timezone.utc)))

    def test_explicit_chats_missing_from_dialogs_are_reported_as_uncertain(self):
        folder = self.folder(include_peers=[types.InputPeerUser(user_id=5, access_hash=7), types.InputPeerSelf()],
                             pinned_peers=[types.InputPeerUser(user_id=5, access_hash=7)])
        class Client:
            async def __call__(self, request):
                return types.messages.DialogFilters(filters=[folder])
            async def iter_dialogs(self):
                if False:
                    yield None
        result = asyncio.run(telegram_folders.discover(Client()))
        self.assertEqual(result[0]["uncertain"], 2)

    def test_preview_conflicts_and_atomic_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = DirectService(None, Path(tmp) / "home", Path(tmp) / "exports")
            service.mutate_catalog(lambda state: state.update(dialogs=[{"id": "5", "title": "A", "type": "chat"}]))
            service.mutate_catalog(lambda state: catalog.set_chat_group(state, "5", "ungrouped"))
            service.state["telegram_folders"] = [{"id": "2", "name": "Work", "dialogs": [{"id": "5", "title": "A", "type": "chat"}, {"id": "6", "title": "B", "type": "chat"}], "uncertain": 0}]
            before = service.preview_telegram_folder("2")
            self.assertEqual([x["id"] for x in before["conflicts"]], ["5"])
            result = service.import_telegram_folder("2", "keep", before["fingerprint"])
            self.assertEqual((result["added"], result["skipped"]), (1, 1))
            self.assertEqual(catalog.chat_group(service.state, "5"), "ungrouped")
            self.assertNotEqual(catalog.chat_group(service.state, "6"), None)
            preview = service.preview_telegram_folder("2")
            service.mutate_catalog(lambda state: catalog.set_chat_group(state, "6", None))
            with self.assertRaisesRegex(ValueError, "changed since preview"):
                service.import_telegram_folder("2", "move", preview["fingerprint"])
            preview = service.preview_telegram_folder("2")
            service.import_telegram_folder("2", "move", preview["fingerprint"])
            self.assertEqual(catalog.chat_group(service.state, "5"), catalog.chat_group(service.state, "6"))

    def test_discover_uses_telethon_filters_and_skips_default(self):
        folder = self.folder(groups=True)
        group = types.Chat(id=10, title="Group", photo=types.ChatPhotoEmpty(), participants_count=0, date=None, version=1)
        dialog = SimpleNamespace(id=-10, entity=group, date=None, unread_count=0, folder_id=None)
        class Client:
            async def __call__(self, request):
                return types.messages.DialogFilters(filters=[types.DialogFilterDefault(), folder])
            async def iter_dialogs(self):
                yield dialog
        result = asyncio.run(telegram_folders.discover(Client()))
        self.assertEqual([(x["name"], [d["id"] for d in x["dialogs"]]) for x in result], [("Work", ["-10"])])
