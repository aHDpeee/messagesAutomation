import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("windows_bundle_package", ROOT / "package_direct.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class WindowsBundleTests(unittest.TestCase):
    def test_packaged_windows_launchers_have_windows_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "bundle.zip"
            package.build(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                for name in ("install-windows.bat", "start-windows.bat", "run-windows.bat"):
                    with self.subTest(name=name):
                        content = archive.read("signal-archive/" + name)
                        self.assertIn(b"\r\n", content)
                        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
