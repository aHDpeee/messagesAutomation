import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from signal_archive import shortcuts


class ShortcutTests(unittest.TestCase):
    def test_linux_quotes_command_and_makes_executable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(shortcuts.os, 'name', 'posix'):
            path = shortcuts.create_shortcut(Path(tmp), ['/tmp/My App', '--home', '/tmp/100%'])
            text = path.read_text()
            self.assertIn('Exec="/tmp/My App" "--home" "/tmp/100%%"', text)
            self.assertTrue(path.stat().st_mode & 0o111)

    def test_invalid_multiline_command_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                shortcuts.create_shortcut(Path(tmp), ['/bin/app\nExec=evil'])

