import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("signal_manager", Path(__file__).parents[1] / "manager.py")
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


class ManagerExportTests(unittest.TestCase):
    def setup_manager(self, root):
        manager.CONFIG = root / "export-config.json"
        manager.STATE = root / "export-state.json"
        manager.EXPORTS = root / "Downloads" / "Signal Archive"
        manager.ROOT = root
        manager.DATA = root / "data"
        manager.PY = root / ".venv" / "bin" / "python"
        manager.RUN_LOCK = threading.Lock()
        manager.PROCESS_LOCK = root / ".export.lock"
        manager.JOB_FILE = root / "export-job.json"
        manager.save_json(manager.CONFIG, {"selected": ["123"]})

    @staticmethod
    def fake_run_factory(calls, valid=True):
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            outfile = Path(cmd[cmd.index("-o") + 1])
            outfile.parent.mkdir(parents=True, exist_ok=True)
            content = '{"chats":[],"messages":[]}' if valid else '{broken'
            outfile.write_text(content, encoding="utf-8")
            class Result:
                returncode = 0
                stdout = ""
                stderr = ""
            return Result()
        return fake_run

    def test_recent_export_uses_message_dates_and_preserves_delta_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); self.setup_manager(root)
            manager.save_json(manager.STATE, {"123":"2026-11-10T00:00:00Z"})
            calls=[]
            with patch.object(manager.subprocess,"run",side_effect=self.fake_run_factory(calls)):
                self.assertEqual(manager.export_job(54.0),["123"])
            self.assertEqual(manager.load_json(manager.STATE,{}),{"123":"2026-11-10T00:00:00Z"})
            self.assertIn("export-chat",calls[0])
            self.assertIn("--from",calls[0]); self.assertIn("--to",calls[0])
            self.assertNotIn("--changed-since",calls[0])
            output=next(manager.EXPORTS.rglob("*.json"))
            self.assertEqual(output.stat().st_mode & 0o777,0o600)
            self.assertEqual(manager.EXPORTS.stat().st_mode & 0o777,0o700)

    def test_delta_checkpoint_advances_only_after_valid_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); self.setup_manager(root)
            manager.save_json(manager.STATE,{"123":"2026-11-10T00:00:00Z"})
            with patch.object(manager.subprocess,"run",side_effect=self.fake_run_factory([],valid=False)):
                with self.assertRaises(RuntimeError): manager.export_job()
            self.assertEqual(manager.load_json(manager.STATE,{}),{"123":"2026-11-10T00:00:00Z"})
            self.assertFalse(list(manager.EXPORTS.rglob("*.json")))

    def test_selection_is_export_only_and_never_writes_chat_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); self.setup_manager(root)
            manager.CONFIG=root/"export-config.json"; manager.ENVFILE=root/".env"
            manager.chats_from_db=lambda: ([{"id":"123","title":"Example","type":"group"}],"")
            with patch.object(manager,"install_export_timer"):
                self.assertEqual(manager.save_selection(["123"]),["123"])
            self.assertEqual(manager.load_json(manager.CONFIG,{})["selected"],["123"])
            self.assertFalse(manager.ENVFILE.exists())

    def test_cross_process_lock_prevents_second_export(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); self.setup_manager(root)
            manager.PROCESS_LOCK.parent.mkdir(parents=True,exist_ok=True)
            with manager.PROCESS_LOCK.open("a+") as held:
                fcntl.flock(held.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError,"Another export process"):
                    manager.export_job()


if __name__ == "__main__":
    unittest.main()
