import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from signal_archive.direct_service import DirectService


class MemoryKeyring:
    def __init__(self):
        self.items = {}

    def set_password(self, service, account, value):
        self.items[service, account] = value

    def get_password(self, service, account):
        return self.items.get((service, account))


class ArchiveEncryptionSettingsTests(unittest.TestCase):
    def test_password_is_stored_in_keyring_and_not_state(self):
        with tempfile.TemporaryDirectory() as temp, patch("signal_archive.direct_app._keyring", return_value=MemoryKeyring()):
            service = DirectService(None, Path(temp) / "home", Path(temp) / "exports")
            service.configure_encryption(True, "secret-password")
            self.assertEqual(service.archive_password(), "secret-password")
            self.assertNotIn("secret-password", service.state_path.read_text())
            service.configure_encryption(False)
            self.assertIsNone(service.archive_password())

    def test_enabled_setting_fails_closed_when_password_missing(self):
        with tempfile.TemporaryDirectory() as temp, patch("signal_archive.direct_app._keyring", return_value=MemoryKeyring()):
            service = DirectService(None, Path(temp) / "home", Path(temp) / "exports")
            service.state["encryption_enabled"] = True
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                service.archive_password()

    def test_cannot_enable_without_password(self):
        with tempfile.TemporaryDirectory() as temp:
            service = DirectService(None, Path(temp) / "home", Path(temp) / "exports")
            with self.assertRaises(ValueError):
                service.configure_encryption(True, "")
            self.assertFalse(service.state.get("encryption_enabled", False))
