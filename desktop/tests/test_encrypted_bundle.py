import importlib.util
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from signal_archive.encrypted_bundle import add_encrypted_file


class EncryptedBundleTests(unittest.TestCase):
    def test_missing_dependency_fails_closed_without_touching_existing_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.csv"
            source.write_text("private", encoding="utf-8")
            bundle = root / "out.zip"
            bundle.write_bytes(b"previous archive")
            with patch.dict("sys.modules", {"pyzipper": None}):
                with self.assertRaisesRegex(RuntimeError, "Encrypted ZIP support is unavailable"):
                    add_encrypted_file(bundle, source, "secret")
            self.assertEqual(bundle.read_bytes(), b"previous archive")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["out.zip", "source.csv"])

    def test_empty_password_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.csv"
            source.write_text("hello")
            with self.assertRaises(ValueError):
                add_encrypted_file(root / "out.zip", source, "")
            self.assertFalse((root / "out.zip").exists())

    @unittest.skipUnless(importlib.util.find_spec("pyzipper"), "pyzipper not installed")
    def test_encrypted_append_and_wrong_password_preserves_archive(self):
        import pyzipper
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "out.zip"
            first, second = root / "first.csv", root / "second.csv"
            first.write_text("first")
            second.write_text("second")
            add_encrypted_file(bundle, first, "secret")
            before = bundle.read_bytes()
            with self.assertRaises((RuntimeError, ValueError)):
                add_encrypted_file(bundle, second, "wrong")
            self.assertEqual(bundle.read_bytes(), before)
            add_encrypted_file(bundle, second, "secret")
            with pyzipper.AESZipFile(bundle) as archive:
                archive.setpassword(b"secret")
                self.assertEqual(archive.read("first.csv"), b"first")
                self.assertEqual(archive.read("second.csv"), b"second")
