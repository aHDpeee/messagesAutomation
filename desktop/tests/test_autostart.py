import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

from signal_archive import autostart


@unittest.skipIf(os.name == "nt", "Linux desktop entry behavior")
class AutostartTests(unittest.TestCase):
    def test_enable_disable_and_quote_paths(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            home = Path(tmp) / 'home with $ and %'
            exports = Path(tmp) / 'my exports'
            self.assertFalse(autostart.enabled())
            autostart.configure(True, home, exports)
            content = autostart._desktop_path().read_text(encoding="utf-8")
            self.assertTrue(autostart.enabled())
            self.assertIn('--no-browser', content)
            self.assertIn('--wait-for-cooldown', content)
            self.assertIn('home with \\$ and %%', content)
            self.assertIn('my exports', content)
            autostart.configure(False, home, exports)
            self.assertFalse(autostart.enabled())


class WindowsAutostartTests(unittest.TestCase):
    def test_registry_entry_is_added_and_removed(self):
        values = {}
        class Key:
            def __enter__(self): return self
            def __exit__(self, *_): pass
        def query(key, name):
            if name not in values: raise FileNotFoundError(name)
            return values[name], 1
        def remove(key, name):
            if name not in values: raise FileNotFoundError(name)
            del values[name]
        registry = types.SimpleNamespace(HKEY_CURRENT_USER=1, REG_SZ=1,
            OpenKey=lambda *_: Key(), CreateKey=lambda *_: Key(), QueryValueEx=query,
            SetValueEx=lambda key, name, reserved, kind, value: values.update({name: value}), DeleteValue=remove)
        with patch.object(autostart, 'os', types.SimpleNamespace(name='nt')), patch.dict(sys.modules, {'winreg': registry}):
            self.assertFalse(autostart.enabled())
            autostart.configure(True, Path('/home/example'), Path('/home/exports'))
            self.assertTrue(autostart.enabled())
            self.assertIn('--no-browser', values[autostart.NAME])
            self.assertIn('--wait-for-cooldown', values[autostart.NAME])
            autostart.configure(False, Path('/home/example'), Path('/home/exports'))
            self.assertFalse(autostart.enabled())
