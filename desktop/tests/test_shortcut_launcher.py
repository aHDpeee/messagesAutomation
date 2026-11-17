import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from signal_archive import shortcut_launcher as launcher


class ShortcutLauncherTests(unittest.TestCase):
    def test_refuses_unrelated_local_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            launcher.save_identity(home, 8765)
            response = MagicMock(status=200)
            response.read.return_value = b'not a valid proof'
            connection = MagicMock()
            connection.getresponse.return_value = response
            with patch.object(launcher.http.client, 'HTTPConnection', return_value=connection), \
                 patch.object(launcher.webbrowser, 'open') as opened:
                self.assertFalse(launcher.open_existing(home))
                opened.assert_not_called()

    def test_proof_matches_private_instance_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            secret = launcher.save_identity(home, 8765)
            challenge = 'ab' * 32
            response = MagicMock(status=200)
            response.read.return_value = launcher.prove(secret, challenge).encode('ascii')
            connection = MagicMock()
            connection.getresponse.return_value = response
            with patch.object(launcher.secrets, 'token_hex', return_value=challenge), \
                 patch.object(launcher.http.client, 'HTTPConnection', return_value=connection), \
                 patch.object(launcher.webbrowser, 'open') as opened:
                self.assertTrue(launcher.open_existing(home))
                opened.assert_called_once_with('http://127.0.0.1:8765/')
            launcher.remove_identity(home, '00' * 32)
            self.assertTrue((home / 'running-instance.json').exists())
            launcher.remove_identity(home, secret)
            self.assertFalse((home / 'running-instance.json').exists())
