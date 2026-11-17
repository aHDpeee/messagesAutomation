from __future__ import annotations

"""Scheduler-side IPC worker for Signal Archive Telegram actions.

Imported by the existing telegram_archive scheduler process. It never opens a
second Telethon session. Unix socket permissions are owner-only.
"""
import asyncio
import json
import os
import socketserver
import threading
import uuid
from pathlib import Path
from typing import Any

from .telegram_ops import discover_dialogs, export_group_members, TelegramJobQueue


class SchedulerTelegramWorker:
    def __init__(self, scheduler: Any, socket_path: Path, export_dir: Path, state_path: Path):
        self.scheduler = scheduler
        self.socket_path = socket_path
        self.export_dir = export_dir
        self.queue = TelegramJobQueue(state_path)
        self._server = None
        self._thread = None
        self._loop = None

    async def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        op = payload.get("op")
        account_index = int(payload.get("account", 1))
        entry = next((x for x in self.scheduler._accounts if x.account.index == account_index), None)
        if entry is None:
            raise RuntimeError("Telegram account is not connected in the archive scheduler.")
        client = await entry.connection.ensure_connected()
        if op == "dialogs":
            return await self.queue.execute(
                client, "dialogs",
                lambda c: discover_dialogs(c, set(payload.get("archived_ids", [])),
                                           limit=int(payload.get("limit", 100)), offset=int(payload.get("offset", 0))),
                idempotency_key=f"dialogs:{account_index}:{uuid.uuid4().hex}"
            )
        if op == "members":
            chat_id = int(payload["chat_id"])
            return await self.queue.execute(
                client, "members",
                lambda c: export_group_members(c, chat_id, self.export_dir, max_members=int(payload.get("max_members",10000))),
                idempotency_key=f"members:{account_index}:{chat_id}:{payload.get('request_id','')}"
            )
        if op == "status":
            return {"jobs": self.queue.status(payload.get("job_id"))}
        raise ValueError("Unknown Telegram operation")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        worker = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                raw = self.rfile.readline(1024 * 1024)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid request")
                    future = asyncio.run_coroutine_threadsafe(worker._call(payload), worker._loop)
                    result = future.result(timeout=900)
                    response = {"ok": True, "result": result}
                except Exception as exc:
                    response = {"ok": False, "error": str(exc)[:500]}
                self.wfile.write(json.dumps(response, ensure_ascii=False, default=str).encode() + b"\n")

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
            allow_reuse_address = False

        self._loop = asyncio.get_running_loop()
        self._server = Server(str(self.socket_path), Handler)
        os.chmod(self.socket_path, 0o600)
        self._thread = threading.Thread(target=self._server.serve_forever, name="signal-archive-telegram-ipc", daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        self.socket_path.unlink(missing_ok=True)
