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
    def test_linux_launcher_works_when_extraction_drops_executable_bits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "release.zip"
            package.build(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                archive.extractall(root / "unpacked")
            app = root / "unpacked" / "signal-archive"
            (app / "start-linux.sh").chmod(0o644)
            result = subprocess.run(["sh", str(app / "run-linux.sh")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Run sh install-linux.sh first", result.stderr)

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
