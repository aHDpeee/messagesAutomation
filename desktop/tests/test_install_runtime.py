import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("install_runtime", Path(__file__).parents[1] / "install_runtime.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_failed_installed_package_check_restores_existing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".venv-direct"
            target.mkdir()
            (target / "original").write_text("old")
            calls = []
            def runner(cmd, **kwargs):
                calls.append((cmd, kwargs))
                if len(calls) == 1:
                    target.mkdir()
                elif len(calls) == 3:
                    self.assertEqual(kwargs["cwd"], target)
                    self.assertIn("-I", cmd)
                    raise subprocess.CalledProcessError(1, cmd)
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(root, python="python", runner=runner)
            self.assertEqual((target / "original").read_text(), "old")

    def test_failed_update_restores_existing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".venv-direct"
            target.mkdir()
            (target / "original").write_text("old")
            calls = []
            def runner(cmd, **kwargs):
                calls.append(cmd)
                if len(calls) == 1:
                    target.mkdir()
                    (target / "partial").write_text("new")
                else:
                    raise subprocess.CalledProcessError(1, cmd)
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(root, python="python", runner=runner)
            self.assertEqual((target / "original").read_text(), "old")
            self.assertFalse((target / "partial").exists())
            self.assertEqual(list(root.glob(".venv-direct.saved-*")), [])

    def test_success_retains_previous_runtime_for_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".venv-direct"
            target.mkdir()
            (target / "original").write_text("old")
            calls = []
            def runner(cmd, **kwargs):
                calls.append(cmd)
                if len(calls) == 1:
                    target.mkdir()
                    (target / "new").write_text("new")
            backup = installer.install(root, python="python", runner=runner)
            self.assertEqual((backup / "original").read_text(), "old")
            self.assertEqual((target / "new").read_text(), "new")
            self.assertEqual(len(calls), 3)
            self.assertIn("-I", calls[2])


if __name__ == "__main__":
    unittest.main()
