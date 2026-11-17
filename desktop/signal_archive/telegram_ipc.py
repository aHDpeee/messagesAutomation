from __future__ import annotations
import json
import socket
from pathlib import Path

SOCKET_PATH = Path.home() / '.local/share/TelegramArchive/run/signal-archive-telegram.sock'


def request(payload: dict, timeout: float = 900) -> dict:
    if not SOCKET_PATH.exists():
        raise RuntimeError('Telegram scheduler is not running. Start telegram-archive.service first.')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(SOCKET_PATH))
        client.sendall(json.dumps(payload).encode('utf-8') + b'\n')
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b'\n' in chunk:
                break
    if not chunks:
        raise RuntimeError('Telegram scheduler returned an empty response.')
    response = json.loads(b''.join(chunks).split(b'\n', 1)[0].decode('utf-8'))
    if not response.get('ok'):
        raise RuntimeError(response.get('error', 'Telegram operation failed'))
    return response.get('result', {})
