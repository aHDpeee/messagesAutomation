import importlib.util
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("package_direct", ROOT / "package_direct.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX launcher")
    def test_installed_runtime_is_reused_and_existing_session_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "start-linux.sh").write_bytes((ROOT / "start-linux.sh").read_bytes())
            runtime = root / ".venv-direct" / "bin" / "python"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("#!/bin/sh\nprintf 'normal: %s\\n' \"$*\"\n")
            runtime.chmod(0o755)
            (root / "start-existing-session-linux.sh").write_text("#!/bin/sh\necho existing-session\n")
            normal = subprocess.run(["sh", str(root / "start-linux.sh")], capture_output=True, text=True)
            self.assertEqual(normal.returncode, 0)
            self.assertIn("normal: -m signal_archive telegram", normal.stdout)
            existing = subprocess.run(["sh", str(root / "start-linux.sh"), "--existing-session"], capture_output=True, text=True)
            self.assertEqual(existing.returncode, 0)
            self.assertIn("existing-session", existing.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX launcher")
    def test_linux_launcher_works_when_extraction_drops_executable_bits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "release.zip"
            package.build(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                archive.extractall(root / "unpacked")
            app = root / "unpacked" / "signal-archive"
            (app / "start-linux.sh").chmod(0o644)
            bindir = root / "bin"
            bindir.mkdir()
            fake_python = bindir / "python3"
            fake_python.write_text("#!/bin/sh\n/bin/mkdir -p .venv-direct/bin\n/bin/printf '#!/bin/sh\\nprintf \"started: %%s\\n\" \"$*\"\\n' > .venv-direct/bin/python\n/bin/chmod +x .venv-direct/bin/python\n")
            fake_python.chmod(0o755)
            env = {**os.environ, "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
            result = subprocess.run(["sh", str(app / "run-linux.sh")], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("First launch", result.stdout)
            self.assertIn("started: -m signal_archive telegram", result.stdout)

    def test_package_contains_only_allowlisted_files_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "release.zip"
            package.build(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                expected = {"signal-archive/README.md"} | {"signal-archive/" + name for name in package.FILES}
                self.assertEqual(set(archive.namelist()), expected)
                self.assertIsNone(archive.testzip())
            before = output.read_bytes()
            with self.assertRaises(FileExistsError):
                package.build(ROOT, output)
            self.assertEqual(output.read_bytes(), before)

    def test_release_contains_session_and_problem_chat_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "release.zip"
            package.build(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                for module in ("session", "problem_chats"):
                    self.assertIn(f"signal-archive/signal_archive/{module}.py", archive.namelist())

    def test_unpacked_release_can_import_desktop_entrypoint(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "release.zip"
            package.build(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                archive.extractall(root)
            app = root / "signal-archive"
            env = {**os.environ, "PYTHONPATH": str(app)}
            result = subprocess.run([sys.executable, "-c", "import signal_archive.direct_app, signal_archive.browser_app"], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
