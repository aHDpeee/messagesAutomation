import json
import tempfile
import unittest
from pathlib import Path
from signal_archive.core import Archive

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.archive = Archive(self.home)
        self.archive.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_deduplicates_and_exports(self):
        source = Path(self.tmp.name) / "input.jsonl"
        rows = [
            {"source_id":"ai-news","id":1,"date":"2026-11-11T10:00:00Z",
             "from":"Alice","text":"New compiler release https://example.com"},
            {"source_id":"ai-news","id":1,"date":"2026-11-11T10:00:00Z",
             "from":"Alice","text":"New compiler release https://example.com"},
        ]
        source.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
        result = self.archive.import_file(source, "ai-news")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["duplicates"], 1)
        out = Path(self.tmp.name) / "out"
        manifest = self.archive.export(out, profile="daily-news")
        self.assertEqual(manifest["counts"]["messages"], 1)
        self.assertTrue((out / "messages.jsonl").exists())
        self.assertTrue((out / "overview.md").exists())
        self.assertTrue((out / "manifest.json").exists())

    def test_search(self):
        self.archive.add_source("dev","Developers")
        source = Path(self.tmp.name) / "input.jsonl"
        source.write_text(json.dumps({"source_id":"dev","id":2,"text":"C++ memory model"}))
        self.archive.import_file(source, "dev")
        self.assertEqual(len(self.archive.search("memory")), 1)

    def test_collection_assignment(self):
        self.archive.add_source("news", "News")
        self.archive.add_collection("technology", "Technology")
        self.archive.assign_source("news", "technology")
        self.assertEqual(self.archive.list_collections()[0]["source_count"], 1)

    def test_export_verification_detects_tampering(self):
        source = Path(self.tmp.name) / "one.jsonl"
        source.write_text(json.dumps({"source_id":"s","id":1,"text":"hello"}), encoding="utf-8")
        self.archive.import_file(source, "s")
        out = Path(self.tmp.name) / "export"
        self.archive.export(out)
        self.assertTrue(self.archive.verify_export(out)["ok"])
        (out / "messages.jsonl").write_text("tampered", encoding="utf-8")
        self.assertFalse(self.archive.verify_export(out)["ok"])

if __name__ == "__main__":
    unittest.main()
