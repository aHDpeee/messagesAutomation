"""Ephemeral HTTPS-only sessions for verified, paired WebAuthn credentials.

Not wired to an HTTP listener: caller must enforce these checks for every route.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time


class DeviceSessions:
    def __init__(self, records: dict, *, ttl_seconds: int = 3600, clock=time.monotonic):
        if ttl_seconds < 1:
            raise ValueError("Session duration must be positive.")
        self.records, self.ttl, self.clock = records, ttl_seconds, clock
        self._sessions: dict[bytes, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, credential_id: str) -> str:
        """Call exclusively after successful WebAuthn user verification."""
        with self._lock:
            if credential_id not in self.records:
                raise ValueError("Unknown device credential.")
            now = self.clock()
            self._sessions = {k: v for k, v in self._sessions.items() if v[1] > now and v[0] in self.records}
            token = secrets.token_urlsafe(32)
            self._sessions[hashlib.sha256(token.encode()).digest()] = (credential_id, now + self.ttl)
            return token

    def valid(self, token: str) -> bool:
        try:
            digest = hashlib.sha256(token.encode("ascii")).digest()
        except (AttributeError, UnicodeError):
            return False
        with self._lock:
            session = self._sessions.get(digest)
            if not session:
                return False
            if session[1] <= self.clock() or session[0] not in self.records:
                self._sessions.pop(digest, None)
                return False
            return True

    def revoke(self, credential_id: str) -> None:
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if v[0] != credential_id}

    @staticmethod
    def cookie(token: str) -> str:
        if not token or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token):
            raise ValueError("Invalid session token.")
        return f"__Host-signal-session={token}; Secure; HttpOnly; SameSite=Strict; Path=/"
