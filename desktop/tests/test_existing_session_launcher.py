import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "start-existing-session-linux.sh"


@unittest.skipIf(os.name == "nt", "POSIX launcher")
class ExistingSessionLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.app.mkdir()
        shutil.copyfile(SOURCE, self.app / SOURCE.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.home = self.root / "home"
        self.session_dir = self.home / ".local/share/TelegramArchive/data/session"
        self.session_dir.mkdir(parents=True)
        self.session = self.session_dir / "account.session"
        self.session.write_text("fixture")
        runtime = self.app / ".venv-direct/bin/python"
        runtime.parent.mkdir(parents=True)
        self.script(runtime, '#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_ARGS"\n')
        self.script(self.bin / "systemctl", '''#!/bin/sh
printf '%s\n' "$*" >> "$TEST_SYSTEMCTL"
case "$*" in
  *show-environment*) [ "${TEST_NO_BUS:-0}" != 1 ] ;;
  *stop*) [ "${TEST_STOP_FAIL:-0}" != 1 ] ;;
  *) exit 2 ;;
esac
''')
        self.script(self.bin / "fuser", '''#!/bin/sh
case "${TEST_FUSER:-free}" in
  busy) exit 0 ;;
  free) exit 1 ;;
  error) echo 'scan failed' >&2; exit 2 ;;
  warning) echo 'permission denied' >&2; exit 1 ;;
esac
''')
        self.env = {**os.environ, "HOME": str(self.home), "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "TEST_ARGS": str(self.root / "args"), "TEST_SYSTEMCTL": str(self.root / "systemctl")}
        self.env.pop("TELEGRAM_ARCHIVE_HOME", None)
        self.env.pop("XDG_DATA_HOME", None)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def script(path, text):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def launch(self, **variables):
        return subprocess.run(["sh", str(self.app / SOURCE.name)], env={**self.env, **variables},
                              capture_output=True, text=True, timeout=5)

    def test_stops_legacy_service_and_passes_existing_session_without_new_code(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user stop telegram-archive.service", (self.root / "systemctl").read_text())
        args = (self.root / "args").read_text().splitlines()
        self.assertEqual(args, ["-m", "signal_archive", "telegram", "--session-file", str(self.session), "--require-existing-auth"])
        self.assertEqual(self.session.read_text(), "fixture")

    def test_busy_session_and_fuser_errors_never_launch_client(self):
        for mode in ("busy", "error", "warning"):
            with self.subTest(mode=mode):
                result = self.launch(TEST_FUSER=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "args").exists())
                self.assertEqual(self.session.read_text(), "fixture")

    def test_stop_failure_or_ambiguous_sessions_never_launch_client(self):
        result = self.launch(TEST_STOP_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "args").exists())
        (self.session_dir / "another.session").write_text("fixture")
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "args").exists())

    def test_unavailable_systemd_or_symlink_session_never_launches_client(self):
        result = self.launch(TEST_NO_BUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "args").exists())
        trace = (self.root / "systemctl").read_text()
        self.assertNotIn(" stop ", trace)
        self.session.unlink()
        target = self.root / "actual.session"
        target.write_text("fixture")
        self.session.symlink_to(target)
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "args").exists())
        self.assertEqual(target.read_text(), "fixture")


if __name__ == "__main__":
    unittest.main()
