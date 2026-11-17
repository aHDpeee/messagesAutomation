import json
import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from signal_archive import session as session_ops
from signal_archive.direct_app import _secret_account, finish_session_switch
from signal_archive.direct_service import DirectService


class SessionReuseTests(unittest.TestCase):
    def test_windows_preflight_closes_exclusive_handle_and_rejects_busy_file(self):
        kernel = MagicMock()
        kernel.CreateFileW.return_value = 123
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            session_ops._check_windows_file_unused(Path("session.session"))
        self.assertEqual(kernel.CreateFileW.call_args.args[2], 0)
        kernel.CloseHandle.assert_called_once_with(123)
        kernel = MagicMock()
        kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            with self.assertRaisesRegex(RuntimeError, "используется"):
                session_ops._check_windows_file_unused(Path("session.session"))
        kernel.CloseHandle.assert_not_called()

    def test_switching_to_new_account_preserves_old_data_without_reusing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DirectService(None, root / "home", root / "exports")
            def old_account(state):
                state.update(account_id=123, dialogs=[{"id": "42", "title": "Old chat", "type": "chat"}],
                             selected=["42"], checkpoints={"42": 999}, account_switch_pending=True)
            service.mutate_catalog(old_account)
            service.bind_account(456)
            self.assertEqual(service.state["account_id"], 456)
            self.assertEqual(service.state["selected"], [])
            self.assertEqual(service.state["checkpoints"], {})
            self.assertEqual(service.state["dialogs"], [])
            history = list((root / "home" / "account-history").glob("*.json"))
            self.assertEqual(len(history), 1)
            self.assertEqual(json.loads(history[0].read_text())["checkpoints"], {"42": 999})
            self.assertEqual(len(service.catalog_backups()), 1)

    def test_reauthorizing_same_account_keeps_selection_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DirectService(None, root / "home", root / "exports")
            def existing(state):
                state.update(account_id=123, dialogs=[{"id": "42", "title": "Chat", "type": "chat"}],
                             selected=["42"], checkpoints={"42": 999}, account_switch_pending=True)
            service.mutate_catalog(existing)
            service.bind_account(123)
            self.assertEqual(service.state["selected"], ["42"])
            self.assertEqual(service.state["checkpoints"], {"42": 999})
            self.assertNotIn("account_switch_pending", service.state)

    def test_discovers_single_regular_legacy_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            session = folder / "one.session"
            session.write_text("data")
            (folder / "journal.session-journal").write_text("ignored")
            (folder / "notes.txt").write_text("ignored")
            with patch.object(session_ops, "legacy_session_directories", return_value=[folder]):
                self.assertEqual(session_ops.discover_existing_session(), session)
            (folder / "two.session").write_text("data")
            with patch.object(session_ops, "legacy_session_directories", return_value=[folder]):
                with self.assertRaisesRegex(RuntimeError, "несколько"):
                    session_ops.discover_existing_session()

    def test_prepares_legacy_session_by_stopping_service_and_checking_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "one.session"
            session.write_text("data")
            results = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
                       subprocess.CompletedProcess([], 1, stderr=b"")]
            with patch.object(session_ops.subprocess, "run", side_effect=results) as run:
                session_ops.prepare_existing_session(session)
            self.assertEqual([call.args[0][1:] for call in run.call_args_list],
                             [["--user", "show-environment"], ["--user", "stop", "telegram-archive.service"],
                              ["-s", str(session)]])

    def test_auto_uses_legacy_session_unless_user_switched_to_own(self):
        with patch.object(session_ops, "discover_existing_session", return_value=Path("/legacy/one.session")), \
             patch.object(session_ops, "prepare_existing_session") as prepare:
            session, required = session_ops.resolve_startup_session({}, None, False)
            self.assertEqual(session, Path("/legacy/one.session"))
            self.assertTrue(required)
            prepare.assert_called_once()
        with patch.object(session_ops, "discover_existing_session") as discover:
            session, required = session_ops.resolve_startup_session({"session_source": "own"}, None, False)
            self.assertIsNone(session)
            self.assertFalse(required)
            discover.assert_not_called()

    def test_switch_marker_is_cleared_on_shutdown(self):
        class Keyring:
            def __init__(self): self.values = {}
            def get_password(self, service, account): return self.values.get((service, account))
            def delete_password(self, service, account): self.values.pop((service, account), None)

        class Service:
            def __init__(self, home):
                self.home = home
                self.state = {}
                self.saves = 0
            def save(self): self.saves += 1

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            account = _secret_account(home)
            keyring = Keyring()
            keyring.values[("SignalArchiveDirect", account)] = "a" * 32
            (home / "account.json").write_text(json.dumps({"keyring_account": account}))
            (home / "telegram.session").write_text("session")
            service = Service(home)
            session_ops.request_session_switch(home)
            self.assertTrue(session_ops.switch_requested(home))
            with patch("signal_archive.direct_app._keyring", return_value=keyring):
                finish_session_switch(home, service)
            self.assertFalse(session_ops.switch_requested(home))
            self.assertFalse((home / "account.json").exists())
            self.assertFalse((home / "telegram.session").exists())
            self.assertEqual(service.state["session_source"], "own")
            self.assertTrue(service.state["account_switch_pending"])
            self.assertEqual(service.saves, 1)
            self.assertNotIn(("SignalArchiveDirect", account), keyring.values)


if __name__ == "__main__":
    unittest.main()
