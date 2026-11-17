import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]

class ReleaseContractTests(unittest.TestCase):
    def test_selection_never_mutates_upstream_chat_whitelist(self):
        src=(ROOT/"manager.py").read_text(encoding="utf-8")
        self.assertNotIn('write_env({"CHAT_IDS"',src)
        self.assertIn("Deliberately do not write CHAT_IDS",src)

    def test_compatibility_entrypoint_has_no_second_export_engine(self):
        src=(ROOT/"telegram_manager.py").read_text(encoding="utf-8")
        self.assertIn("from manager import main",src)
        self.assertNotIn("def export_job",src)

    def test_recent_export_uses_message_date_command(self):
        src=(ROOT/"manager.py").read_text(encoding="utf-8")
        self.assertIn('"export-chat"',src)
        self.assertIn('"--from"',src)
        self.assertIn('"--to"',src)

    def test_updater_pins_runtime_and_migrates_every_update(self):
        src=(ROOT/"update-existing-linux.sh").read_text(encoding="utf-8")
        self.assertIn("telegram-archive==9.7.0",src)
        self.assertIn("keyring==25.7.0",src)
        self.assertIn("--data-dir \"$DATA\" migrate",src)
        self.assertIn("UMask=0077",src)

    def test_uninstaller_removes_keyring_secret_and_scrubs_legacy_copies(self):
        src=(ROOT/"uninstall-linux.sh").read_text(encoding="utf-8")
        self.assertIn("--delete-api-hash",src)
        self.assertIn('rm -f -- "$d/.env"',src)
        self.assertIn('rm -rf -- "$d/data/session"',src)

if __name__=="__main__": unittest.main()
