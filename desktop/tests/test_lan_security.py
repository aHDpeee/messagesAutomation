import sys
import types
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from signal_archive.lan_pairing import PairingGate
from signal_archive.lan_sessions import DeviceSessions
from signal_archive.lan_webauthn import PasskeyCeremonies
from signal_archive.lan_device_store import DeviceStore
from signal_archive.lan_tls import server_context


def fake_webauthn():
    web = types.ModuleType("webauthn")
    helpers = types.ModuleType("webauthn.helpers")
    structs = types.ModuleType("webauthn.helpers.structs")
    structs.AuthenticatorSelectionCriteria = lambda **kw: kw
    structs.PublicKeyCredentialDescriptor = lambda **kw: kw
    structs.UserVerificationRequirement = types.SimpleNamespace(REQUIRED="required")
    web.generate_registration_options = lambda **kw: types.SimpleNamespace(challenge=b"registration")
    web.generate_authentication_options = lambda **kw: types.SimpleNamespace(challenge=b"authentication")
    web.options_to_json = lambda options: "{}"
    def reg(**kw):
        assert kw["require_user_verification"] and kw["expected_challenge"] == b"registration"
        assert kw["expected_origin"] == "https://archive.example.test:8443"
        return types.SimpleNamespace(credential_id=b"id", credential_public_key=b"public", sign_count=0)
    def auth(**kw):
        assert kw["require_user_verification"] and kw["expected_challenge"] == b"authentication"
        assert kw["credential_public_key"] == b"public"
        return types.SimpleNamespace(new_sign_count=1)
    web.verify_registration_response, web.verify_authentication_response = reg, auth
    return {"webauthn": web, "webauthn.helpers": helpers, "webauthn.helpers.structs": structs}


class LanSecurityTests(unittest.TestCase):
    def test_pair_register_authenticate_revoke_and_replay(self):
        with patch.dict(sys.modules, fake_webauthn()):
            gate = PairingGate()
            records, saves = {}, []
            service = PasskeyCeremonies("https://archive.example.test:8443", records, lambda: saves.append(True), gate)
            pair, token = gate.request("Phone")
            with self.assertRaises(ValueError):
                service.begin_registration(pair, token)
            gate.approve(pair)
            ident, _ = service.begin_registration(pair, token)
            device = service.finish_registration(ident, {})
            with self.assertRaises(ValueError):
                service.finish_registration(ident, {})
            ident, _ = service.begin_authentication()
            self.assertEqual(service.finish_authentication(ident, {"id": device}), device)
            self.assertEqual(records[device]["sign_count"], 1)
            sessions = DeviceSessions(records)
            session = sessions.issue(device)
            self.assertIn("Secure; HttpOnly; SameSite=Strict", sessions.cookie(session))
            self.assertTrue(sessions.valid(session))
            service.revoke(device)
            self.assertFalse(sessions.valid(session))
            self.assertEqual(len(saves), 3)

    def test_rejects_unsafe_origin(self):
        for origin in ("http://example.test", "https://127.0.0.1", "https://example.test/path", "https://user@example.test"):
            with self.assertRaises(ValueError):
                PasskeyCeremonies(origin, {}, lambda: None, PairingGate())

    def test_sessions_expire_and_revocation_is_device_specific(self):
        now = [0]
        sessions = DeviceSessions({"a": {}, "b": {}}, ttl_seconds=10, clock=lambda: now[0])
        a, b = sessions.issue("a"), sessions.issue("b")
        sessions.revoke("a")
        self.assertFalse(sessions.valid(a))
        self.assertTrue(sessions.valid(b))
        now[0] = 10
        self.assertFalse(sessions.valid(b))

    def test_device_store_roundtrip_and_rejects_invalid_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "devices.json"
            store = DeviceStore(path)
            store.records["abc"] = {"name": "Phone", "public_key": "cHVibGlj", "sign_count": 1}
            store.save()
            self.assertEqual(DeviceStore(path).records, store.records)
            path.write_text('{"abc": {"sign_count": -1}}')
            with self.assertRaises(ValueError):
                DeviceStore(path)

    def test_tls_rejects_missing_certificate(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                server_context(Path(temporary) / "missing.pem", Path(temporary) / "missing.key")
