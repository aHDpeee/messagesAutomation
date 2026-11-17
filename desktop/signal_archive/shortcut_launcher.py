"""Authenticate the local application before opening an existing instance."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import secrets
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path


def save_identity(home: Path, port: int) -> str:
    secret = secrets.token_hex(32)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.instance-', dir=home)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'port': port, 'secret': secret}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, home / 'running-instance.json')
    finally:
        Path(name).unlink(missing_ok=True)
    return secret


def remove_identity(home: Path, secret: str) -> None:
    try:
        path = home / 'running-instance.json'
        if json.loads(path.read_text(encoding='utf-8')).get('secret') == secret:
            path.unlink()
    except (OSError, ValueError, TypeError):
        pass


def prove(secret: str, challenge: str) -> str:
    return hmac.new(bytes.fromhex(secret), challenge.encode('ascii'), hashlib.sha256).hexdigest()


def _verified_instance(home: Path) -> tuple[int, str] | None:
    try:
        path = home / 'running-instance.json'
        if path.is_symlink() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        port, secret = int(data['port']), data['secret']
        if not 1 <= port <= 65535 or not isinstance(secret, str) or len(secret) != 64:
            return None
        challenge = secrets.token_hex(32)
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
        try:
            connection.request('GET', '/instance-proof/' + challenge)
            response = connection.getresponse()
            answer = response.read(128).decode('ascii')
            if response.status != 200 or not hmac.compare_digest(answer, prove(secret, challenge)):
                return None
        finally:
            connection.close()
        return port, secret
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, http.client.HTTPException):
        return None


def open_existing(home: Path) -> bool:
    instance = _verified_instance(home)
    if not instance:
        return False
    webbrowser.open(f'http://127.0.0.1:{instance[0]}/')
    return True


def stop_existing(home: Path, *, timeout: float = 20) -> bool:
    """Stop only an authenticated instance and wait for its session lock."""
    instance = _verified_instance(home)
    if not instance:
        return False
    port, secret = instance
    challenge = secrets.token_hex(32)
    signature = prove(secret, 'shutdown:' + challenge)
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request('POST', '/instance-shutdown/' + challenge + '/' + signature,
                           body=b'', headers={'Origin': f'http://127.0.0.1:{port}'})
        response = connection.getresponse()
        response.read()
        if response.status != 200:
            raise RuntimeError('The previous Signal Archive instance refused to stop.')
    finally:
        connection.close()
    from .direct_app import SessionLock
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with SessionLock(home / 'session.lock'):
                return True
        except RuntimeError:
            time.sleep(0.1)
    raise RuntimeError('The previous Signal Archive instance did not release its session in time.')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if open_existing(args.home.expanduser()):
        return
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        raise ValueError('No startup command specified.')
    subprocess.Popen(command, start_new_session=os.name != 'nt')


if __name__ == '__main__':
    main()
