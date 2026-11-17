import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from signal_archive import shortcuts


class ShortcutTests(unittest.TestCase):
    def test_source_shortcut_imports_checkout_from_arbitrary_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'checkout'
            package = source / 'signal_archive'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('', encoding='utf-8')
            (package / 'shortcut_launcher.py').write_text(
                'def main():\n import os\n print(os.getcwd())\n', encoding='utf-8')
            with patch.object(shortcuts, 'application_directory', return_value=source), \
                 patch.object(shortcuts.sys, 'frozen', False, create=True):
                command = shortcuts.launcher_command(root / 'home', root / 'exports')
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=True)
            self.assertEqual(Path(result.stdout.strip()), source)

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

