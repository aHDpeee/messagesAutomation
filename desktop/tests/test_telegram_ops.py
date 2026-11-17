import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class TelegramDiscoveryTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_real_peer_types_and_archived_dialogs(self):
        from telethon.tl import types as telegram_types
        from signal_archive.telegram_ops import _dialog_record
        peers = [
            (telegram_types.User(id=1, first_name="Helper", bot=True), "bot"),
            (telegram_types.ChatForbidden(id=2, title="Old group"), "group"),
            (telegram_types.ChannelForbidden(id=3, access_hash=10, title="Old supergroup", megagroup=True), "group"),
            (telegram_types.ChannelForbidden(id=4, access_hash=11, title="Old channel", broadcast=True), "channel"),
        ]
        for peer, expected in peers:
            with self.subTest(peer=type(peer).__name__):
                dialog = types.SimpleNamespace(id=peer.id, entity=peer, date=None, folder_id=1)
                record = _dialog_record(dialog, set())
                self.assertEqual(record["type"], expected)
                self.assertTrue(record["archived"])
                self.assertEqual(record["accessible"], isinstance(peer, telegram_types.User))

    def test_second_page_skips_first_page(self):
        telethon = types.ModuleType("telethon")
        telethon.errors = types.SimpleNamespace()
        tl = types.ModuleType("telethon.tl")
        peer_types = types.ModuleType("telethon.tl.types")
        peer_types.User = type("User", (), {})
        peer_types.Channel = type("Channel", (), {})
        peer_types.ChannelForbidden = type("ChannelForbidden", (), {})
        peer_types.Chat = type("Chat", (), {})
        peer_types.ChatForbidden = type("ChatForbidden", (), {})
        modules = {"telethon": telethon, "telethon.tl": tl, "telethon.tl.types": peer_types}
        spec = importlib.util.spec_from_file_location("signal_archive.test_telegram_ops_module", Path(__file__).parents[1] / "signal_archive" / "telegram_ops.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)

        class Client:
            async def iter_dialogs(self, limit):
                for number in range(min(limit, 5)):
                    yield types.SimpleNamespace(id=number, entity=types.SimpleNamespace(title=str(number)), date=None)

        result = asyncio.run(module.discover_dialogs(Client(), set(), limit=2, offset=2))
        self.assertEqual([item["id"] for item in result["items"]], ["2", "3"])
        self.assertEqual(result["next_offset"], 4)

    def test_dialog_description_is_kept_when_present(self):
        from signal_archive.telegram_ops import _dialog_record
        dialog = types.SimpleNamespace(id=42, entity=types.SimpleNamespace(title="Group", about="A description"), date=None)
        self.assertEqual(_dialog_record(dialog, set())["description"], "A description")


if __name__ == "__main__":
    unittest.main()
