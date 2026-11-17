"""TLS context for an opt-in, separately authenticated LAN listener.

Does not open sockets. A trusted hostname-matching certificate and key must
be supplied by the owner; untrusted generated certificates are not sufficient
for browser passkeys.
"""
from __future__ import annotations

import ssl
from pathlib import Path


def server_context(certificate: Path, private_key: Path) -> ssl.SSLContext:
    cert, key = Path(certificate), Path(private_key)
    if cert.is_symlink() or key.is_symlink() or not cert.is_file() or not key.is_file():
        raise ValueError("A regular HTTPS certificate and private key are required.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert), str(key))
    return context
