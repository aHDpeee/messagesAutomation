"""Isolated passkey ceremony backend, not an HTTP server or access middleware.

Requires py_webauthn and a trusted HTTPS origin. No caller should expose an
interface until transport, owner approval, CSRF and session checks are wired.
"""
from __future__ import annotations

import base64
import ipaddress
import secrets
import threading
import time
from urllib.parse import urlsplit


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class PasskeyCeremonies:
    def __init__(self, origin: str, records: dict, save, pairing_gate, *, clock=time.monotonic):
        url = urlsplit(origin)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            raise ValueError("A fixed trusted HTTPS origin is required.")
        try:
            ipaddress.ip_address(url.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("Use a stable DNS hostname, not an IP address, for WebAuthn.")
        self.origin, self.rp_id = origin, url.hostname
        self.records, self.save, self.clock = records, save, clock
        self.pairing_gate = pairing_gate
        self.pending = {}
        self.lock = threading.RLock()

    def _challenge(self, purpose: str, options, extra=None):
        now = self.clock()
        self.pending = {key: item for key, item in self.pending.items() if item[2] > now}
        if len(self.pending) >= 16:
            raise RuntimeError("Too many pending authentication requests.")
        ident = secrets.token_urlsafe(24)
        self.pending[ident] = (purpose, options.challenge, now + 120, extra)
        return ident

    def _consume(self, ident: str, purpose: str):
        item = self.pending.pop(ident, None)
        if not item or item[0] != purpose or item[2] <= self.clock():
            raise ValueError("WebAuthn request expired or is invalid.")
        return item

    def begin_registration(self, pairing_id: str, pairing_token: str) -> tuple[str, str]:
        """Only a locally approved, one-use pairing request can enroll a device."""
        from webauthn import generate_registration_options, options_to_json
        from webauthn.helpers.structs import AuthenticatorSelectionCriteria, UserVerificationRequirement
        with self.lock:
            device_name = self.pairing_gate.consume(pairing_id, pairing_token)
            options = generate_registration_options(rp_id=self.rp_id, rp_name="Signal Archive", user_name=device_name,
                user_id=secrets.token_bytes(32), authenticator_selection=AuthenticatorSelectionCriteria(user_verification=UserVerificationRequirement.REQUIRED))
            return self._challenge("register", options, device_name), options_to_json(options)

    def finish_registration(self, ident: str, credential: dict) -> str:
        from webauthn import verify_registration_response
        with self.lock:
            _, challenge, _, name = self._consume(ident, "register")
            verified = verify_registration_response(credential=credential, expected_challenge=challenge,
                expected_rp_id=self.rp_id, expected_origin=self.origin, require_user_verification=True)
            cred_id = _b64(verified.credential_id)
            if cred_id in self.records:
                raise ValueError("This device credential is already registered.")
            self.records[cred_id] = {"name": name, "public_key": _b64(verified.credential_public_key), "sign_count": verified.sign_count}
            try:
                self.save()
            except BaseException:
                del self.records[cred_id]
                raise
            return cred_id

    def begin_authentication(self) -> tuple[str, str]:
        from webauthn import generate_authentication_options, options_to_json
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement
        with self.lock:
            if not self.records:
                raise ValueError("No registered devices.")
            options = generate_authentication_options(rp_id=self.rp_id,
                allow_credentials=[PublicKeyCredentialDescriptor(id=_unb64(key)) for key in self.records],
                user_verification=UserVerificationRequirement.REQUIRED)
            return self._challenge("authenticate", options), options_to_json(options)

    def finish_authentication(self, ident: str, credential: dict) -> str:
        from webauthn import verify_authentication_response
        with self.lock:
            _, challenge, _, _ = self._consume(ident, "authenticate")
            cred_id = credential.get("id", "")
            record = self.records.get(cred_id)
            if not record:
                raise ValueError("Unknown device credential.")
            verified = verify_authentication_response(credential=credential, expected_challenge=challenge,
                expected_rp_id=self.rp_id, expected_origin=self.origin, credential_public_key=_unb64(record["public_key"]),
                credential_current_sign_count=record["sign_count"], require_user_verification=True)
            previous = record["sign_count"]
            record["sign_count"] = verified.new_sign_count
            try:
                self.save()
            except BaseException:
                record["sign_count"] = previous
                raise
            return cred_id

    def revoke(self, cred_id: str) -> None:
        with self.lock:
            record = self.records.pop(cred_id)
            try:
                self.save()
            except BaseException:
                self.records[cred_id] = record
                raise
