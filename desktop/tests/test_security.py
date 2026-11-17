import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location("secret_runner",Path(__file__).parents[1]/"secret_runner.py")
secrets_mod=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(secrets_mod)

class SecretRunnerTests(unittest.TestCase):
    def fake_keyring(self, backend_name="SecretService"):
        values={}
        Backend=type("Keyring",(),{})
        Backend.__module__="keyring.backends."+backend_name
        module=types.ModuleType("keyring")
        module.get_keyring=lambda:Backend()
        module.set_password=lambda service,account,value: values.__setitem__((service,account),value)
        module.get_password=lambda service,account: values.get((service,account))
        module.delete_password=lambda service,account: values.pop((service,account),None)
        return module,values

    def test_api_hash_is_stored_and_verified_in_os_keyring(self):
        module,values=self.fake_keyring()
        with patch.dict(sys.modules,{"keyring":module}):
            secrets_mod.set_secret("0123456789abcdef0123456789abcdef")
        self.assertEqual(len(values),1)

    def test_legacy_env_hash_migrates_and_is_removed_from_env(self):
        module,values=self.fake_keyring()
        with tempfile.TemporaryDirectory() as tmp:
            env=Path(tmp)/".env"
            env.write_text("TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=0123456789abcdef0123456789abcdef\n",encoding="utf-8")
            with patch.dict(sys.modules,{"keyring":module}):
                secrets_mod.remove_env_hash(env)
            self.assertIn("TELEGRAM_API_ID=123",env.read_text())
            self.assertNotIn("TELEGRAM_API_HASH",env.read_text())
            self.assertEqual(len(values),1)

    def test_manual_legacy_rollback_restores_api_hash_only_to_active_env(self):
        module,values=self.fake_keyring()
        secret="0123456789abcdef0123456789abcdef"
        values[("SignalArchive","telegram-api-hash")]=secret
        with tempfile.TemporaryDirectory() as tmp:
            env=Path(tmp)/".env"; env.write_text("TELEGRAM_API_ID=123\n",encoding="utf-8")
            with patch.dict(sys.modules,{"keyring":module}):
                # The helper's restore branch is exercised via its CLI parser entry point.
                with patch.object(secrets_mod.sys,"argv",["secret_runner.py","--restore-legacy-env",str(env)]):
                    self.assertEqual(secrets_mod.main(),0)
            self.assertIn("TELEGRAM_API_HASH="+secret,env.read_text())
            self.assertEqual(env.stat().st_mode & 0o777,0o600)

    def test_plaintext_or_unknown_keyring_backend_is_rejected(self):
        module,_=self.fake_keyring("fail")
        with patch.dict(sys.modules,{"keyring":module}):
            with self.assertRaisesRegex(RuntimeError,"Unsafe keyring backend"):
                secrets_mod.keyring_module()

if __name__=="__main__": unittest.main()
