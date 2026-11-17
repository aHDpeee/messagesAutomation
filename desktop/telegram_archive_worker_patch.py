#!/usr/bin/env python3
"""Version-locked AST patch for the installed Telegram Archive scheduler.

Refuses unknown source. Keeps the scheduler's existing try/finally structure and
inserts a worker start after the shared client connections are established plus
worker shutdown as the first statement of the existing finally block.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import os
from pathlib import Path
import shutil
import sys

TARGET = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "telegram_archive" / "scheduler.py"
BACKUP = TARGET.with_name("scheduler.py.signal-archive-original")
MARKER = "SIGNAL_ARCHIVE_WORKER_HOOK_V1"

START_LINES = [
    "'SIGNAL_ARCHIVE_WORKER_HOOK_V1_START'",
    "if os.getenv('SIGNAL_ARCHIVE_TELEGRAM_IPC', 'true').casefold() in {'1', 'true', 'yes', 'on'}:",
    "    try:",
    "        from pathlib import Path",
    "        from signal_archive.telegram_worker import SchedulerTelegramWorker",
    "        app_home = Path(os.getenv('SIGNAL_ARCHIVE_HOME', str(Path.home() / '.local/share/TelegramArchive')))",
    "        self._signal_archive_worker = SchedulerTelegramWorker(",
    "            self, app_home / 'run' / 'signal-archive-telegram.sock',",
    "            Path(os.getenv('SIGNAL_ARCHIVE_EXPORTS', str(Path.home() / 'Downloads/Signal Archive'))),",
    "            app_home / 'telegram-jobs.json',",
    "        )",
    "        self._signal_archive_worker.start()",
    "        logger.info('Signal Archive Telegram worker started')",
    "    except Exception as worker_error:",
    "        logger.error('Signal Archive Telegram worker unavailable: %s', type(worker_error).__name__)",
]
STOP_LINES = [
    "# SIGNAL_ARCHIVE_WORKER_HOOK_V1 shutdown",
    "if getattr(self, '_signal_archive_worker', None) is not None:",
    "    try:",
    "        self._signal_archive_worker.stop()",
    "    except Exception as worker_error:",
    "        logger.warning('Signal Archive worker shutdown failed: %s', type(worker_error).__name__)",
    "    self._signal_archive_worker = None",
]


def _find_run_forever(tree: ast.Module):
    for cls in (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BackupScheduler"):
        for node in cls.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_forever":
                return node
    raise RuntimeError("BackupScheduler.run_forever not found")


def transform(source: str) -> str:
    if MARKER in source:
        return source
    tree = ast.parse(source)
    run_forever = _find_run_forever(tree)
    # Locate the connection await in a nested try body without changing its owner block.
    connect_block = None
    connect_index = None
    for parent in ast.walk(run_forever):
        body = getattr(parent, "body", None)
        if not isinstance(body, list):
            continue
        for index, node in enumerate(body):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Await):
                call = node.value.value
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "_connect":
                    connect_block, connect_index = body, index + 1
                    break
        if connect_block is not None:
            break
    if connect_block is None:
        raise RuntimeError("Expected await self._connect() not found in run_forever")
    start_block = ast.parse("\n".join(START_LINES)).body
    connect_block[connect_index:connect_index] = start_block
    # Insert stop block at head of the actual existing finally handler.
    finally_handlers = []
    for node in ast.walk(run_forever):
        if isinstance(node, ast.Try):
            finally_handlers.extend(node.finalbody)
    if not finally_handlers:
        raise RuntimeError("run_forever has no finally body")
    # Select the finally whose first meaningful call cancels heartbeat_task.
    chosen = None
    for node in ast.walk(run_forever):
        if isinstance(node, ast.Try) and node.finalbody:
            text = ast.unparse(ast.Module(body=node.finalbody, type_ignores=[]))
            if "heartbeat_task.cancel" in text:
                chosen = node.finalbody
                break
    if chosen is None:
        raise RuntimeError("Could not identify run_forever cleanup finally block")
    stop_block = ast.parse("\n".join(STOP_LINES)).body
    chosen[0:0] = stop_block
    # Since inserted AST nodes have no source location, use ast.unparse as deterministic full-module output.
    result = ast.unparse(tree) + "\n"
    compile(result, str(TARGET), "exec")
    if result.count(MARKER) != 2:
        raise RuntimeError("Unexpected worker marker count in transformed scheduler")
    return result


def install() -> None:
    original = TARGET.read_text(encoding="utf-8")
    patched = transform(original)
    if patched == original:
        print("Scheduler hook already installed")
        return
    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
        os.chmod(BACKUP, 0o600)
    tmp = TARGET.with_suffix(".py.signal-archive-tmp")
    tmp.write_text(patched, encoding="utf-8")
    os.chmod(tmp, TARGET.stat().st_mode & 0o777)
    compile(patched, str(tmp), "exec")
    os.replace(tmp, TARGET)
    print(f"Installed AST scheduler hook; original saved to {BACKUP}")


def uninstall() -> None:
    if not BACKUP.exists():
        print("No scheduler backup found; nothing to restore")
        return
    current = TARGET.read_text(encoding="utf-8")
    if MARKER not in current:
        raise SystemExit("Worker marker absent; refusing to overwrite scheduler")
    original = BACKUP.read_text(encoding="utf-8")
    compile(original, str(BACKUP), "exec")
    tmp = TARGET.with_suffix(".py.restore-tmp")
    tmp.write_text(original, encoding="utf-8")
    os.chmod(tmp, BACKUP.stat().st_mode & 0o777)
    os.replace(tmp, TARGET)
    print("Restored original Telegram Archive scheduler")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "uninstall"])
    args = parser.parse_args()
    install() if args.action == "install" else uninstall()
