"""Ephemeral local-approval gate for a future HTTPS/WebAuthn LAN listener.

This is *not* an authentication implementation and grants no HTTP access.
The caller must additionally complete a verified WebAuthn ceremony. Never expose
the current loopback-only interface directly to a LAN using this gate alone.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time


class PairingGate:
    """Track pending, locally approved, short-lived one-use enrollments."""
    def __init__(self, *, ttl_seconds: int = 120, max_pending: int = 8, clock=time.monotonic):
        if ttl_seconds < 1 or max_pending < 1:
            raise ValueError("Pairing limits must be positive.")
        self.ttl = ttl_seconds
        self.max_pending = max_pending
        self.clock = clock
        self._requests: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _prune(self):
        now = self.clock()
        self._requests = {k: v for k, v in self._requests.items() if v["deadline"] > now}

    def request(self, device_name: str) -> tuple[str, str]:
        name = " ".join(str(device_name).split())[:80]
        if not name or any(ord(c) < 32 for c in name):
            raise ValueError("A device name is required.")
        with self._lock:
            self._prune()
            if len(self._requests) >= self.max_pending:
                raise RuntimeError("Too many pending pairing requests.")
            token = secrets.token_urlsafe(32)
            request_id = secrets.token_urlsafe(18)
            self._requests[request_id] = {"device_name": name, "token_hash": hashlib.sha256(token.encode()).digest(),
                                          "deadline": self.clock() + self.ttl, "approved": False, "code": f"{secrets.randbelow(1000000):06d}"}
            return request_id, token

    def pending(self) -> list[dict]:
        with self._lock:
            self._prune()
            return [{"id": ident, "device_name": r["device_name"], "approved": r["approved"], "code": r["code"]}
                    for ident, r in self._requests.items()]

    def approve(self, request_id: str) -> None:
        """Only invoke from an authenticated local-owner interface."""
        with self._lock:
            self._prune()
            if request_id not in self._requests:
                raise ValueError("Pairing request expired or does not exist.")
            self._requests[request_id]["approved"] = True

    def deny(self, request_id: str) -> None:
        with self._lock:
            self._requests.pop(request_id, None)

    def approved(self, request_id: str, token: str) -> bool:
        with self._lock:
            self._prune()
            entry = self._requests.get(request_id)
            try:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
            except (AttributeError, UnicodeError):
                raise ValueError("Invalid pairing request.") from None
            if not entry or not hmac.compare_digest(entry["token_hash"], digest):
                raise ValueError("Pairing request expired or does not exist.")
            return entry["approved"]

    def code(self, request_id: str, token: str) -> str:
        with self._lock:
            self._prune()
            entry = self._requests.get(request_id)
            try:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
            except (AttributeError, UnicodeError):
                raise ValueError("Invalid pairing request.") from None
            if not entry or not hmac.compare_digest(entry["token_hash"], digest):
                raise ValueError("Pairing request expired or does not exist.")
            return entry["code"]

    def consume(self, request_id: str, token: str) -> str:
        """Consume authorization to enroll a verified passkey, not a login."""
        with self._lock:
            self._prune()
            entry = self._requests.get(request_id)
            try:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
            except (AttributeError, UnicodeError):
                raise ValueError("Invalid pairing request.") from None
            if not entry or not entry["approved"] or not hmac.compare_digest(entry["token_hash"], digest):
                raise ValueError("Pairing request is not approved or has expired.")
            del self._requests[request_id]
            return entry["device_name"]
