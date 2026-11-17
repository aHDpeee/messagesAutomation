import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from signal_archive import autostart


ROOT = Path(__file__).resolve().parents[1]


class WindowsExecutableTests(unittest.TestCase):
    def test_spec_is_single_file_and_windowed(self):
        tree = ast.parse((ROOT / "SignalArchive.spec").read_text())
        executable = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                          and isinstance(node.value, ast.Call)
                          and getattr(node.value.func, "id", "") == "EXE")
        options = {kw.arg: kw.value for kw in executable.keywords}
        self.assertIs(options["console"].value, False)
        self.assertIn("a.binaries", ast.unparse(executable))
        self.assertIn("a.datas", ast.unparse(executable))
        self.assertNotIn("COLLECT(", ast.unparse(tree))

    def test_frozen_autostart_relaunches_executable(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", r"C:\\Program Files\\Signal Archive.exe"):
            command = autostart._command(Path("state folder"), Path("output folder"))
        self.assertEqual(command[0], r"C:\\Program Files\\Signal Archive.exe")
        self.assertNotIn("-m", command)
        self.assertIn("--no-browser", command)

    def test_build_script_has_windows_line_endings(self):
        script = (ROOT / "build-windows-exe.bat").read_bytes()
        self.assertNotIn(b"\n", script.replace(b"\r\n", b""))
        self.assertIn(b"-m PyInstaller --noconfirm --clean SignalArchive.spec", script)

    def test_browser_launcher_requests_graphical_authorization(self):
        from signal_archive import browser_app, direct_app
        with patch.object(sys, "argv", ["SignalArchive.exe"]), patch.object(direct_app, "run") as run:
            browser_app.main()
        self.assertTrue(run.call_args.kwargs["browser_auth"])
        self.assertTrue(run.call_args.kwargs["open_browser"])
