"""Single-client Telegram operations and durable export state.

The host owns the client and calls these methods on one asyncio event loop.
No credentials or session data are stored in the state file.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import hashlib
import json
import os
import queue
import re
import shutil
import uuid
import zipfile
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .direct_export import ChatLookupError, export_chat
from . import catalog

INCREMENTAL_SEGMENT_SIZE = 1000


def _new_bundle(exports: Path, kind: str, *, label: str = "", since: datetime | None = None, until: datetime | None = None) -> Path:
    exports.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if label:
        safe = re.sub(r'[^\w -]+', '_', label, flags=re.UNICODE).strip(' ._')[:50] or 'selection'
        period = f"{since:%Y-%m-%d}_to_{until:%Y-%m-%d}" if since and until else "new"
        return exports / f"{kind}_{safe}_{period}_{stamp}_{uuid.uuid4().hex[:8]}.zip"
    return exports / f"{kind}_{stamp}_{uuid.uuid4().hex[:8]}.zip"


def _add_to_bundle(bundle: Path, source: Path, arcname: str | None = None, *, password: str | None = None) -> None:
    """Publish a valid archive containing this file before committing its checkpoint."""
    if password:
        from .encrypted_bundle import add_encrypted_file
        add_encrypted_file(bundle, source, password, arcname=arcname)
        return
    fd, name = tempfile.mkstemp(prefix=".export-", suffix=".part", dir=bundle.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        if bundle.exists():
            shutil.copyfile(bundle, temporary)
        with zipfile.ZipFile(temporary, "a", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.write(source, arcname=arcname or source.name)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, bundle)
    finally:
        temporary.unlink(missing_ok=True)


class PartialExportError(RuntimeError):
    """Some selected chats could not be accessed, but other chats completed."""


def selection_fingerprint(ids: list[str]) -> str:
    encoded = json.dumps(sorted(map(str, ids)), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class DirectService:
    def __init__(self, client: Any, home: Path, exports: Path):
        self.client, self.home, self.exports = client, home, exports
        self.state_path = home / "direct-state.json"
        self.catalog_path = home / "chats.csv"
        self.catalog_journal = home / "catalog-import.pending.json"
        self._recover_catalog_import()
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(self.state, dict):
                raise ValueError("Invalid state")
        except FileNotFoundError:
            self.state = {}
        self.state.setdefault("selected", [])
        self.state.setdefault("checkpoints", {})
        self.state.setdefault("dialogs", [])
        self.lock = asyncio.Lock()
        self.catalog_ready = catalog.initialize_catalog(self.state, self.catalog_path)
        self.catalog_sync_error = ""
        if not self.catalog_ready:
            try:
                self.sync_catalog()
            except (ValueError, OSError, RuntimeError) as exc:
                self.catalog_sync_error = str(exc)
        if self.state.get("job", {}).get("status") == "running":
            self.state["job"].update(status="interrupted", error="The application stopped before this job completed.")
            self.save()

    def save(self) -> None:
        _write_state(self.state_path, self.state)

    def _recover_catalog_import(self) -> None:
        """Finish a validated import after a crash without discarding later edits."""
        try:
            pending = json.loads(self.catalog_journal.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        current = catalog._file_hash(self.catalog_path)
        if current == pending["new_hash"]:
            _write_state(self.state_path, pending["state"])
            marker = self.catalog_path.with_name(self.catalog_path.name + ".sha256")
            catalog._atomic_write(marker, lambda stream: stream.write(current))
        elif current != pending["old_hash"]:
            raise RuntimeError("A catalog import was interrupted and the CSV changed afterwards. Preserve both files and resolve the conflict before restarting.")
        self.catalog_journal.unlink()

    def catalog_content(self) -> tuple[str, str]:
        data = self.catalog_path.read_bytes()
        return data.decode("utf-8-sig"), hashlib.sha256(data).hexdigest()

    def catalog_backups(self) -> list[str]:
        return [p.name for p in catalog.list_catalog_backups(self.catalog_path)]

    def catalog_is_current(self) -> bool:
        marker = self.catalog_path.with_name(self.catalog_path.name + ".sha256")
        try:
            return (not self.catalog_journal.exists() and
                    catalog._file_hash(self.catalog_path) == marker.read_text(encoding="utf-8-sig").strip())
        except (OSError, UnicodeError, ValueError):
            return False

    def sync_catalog(self, *, internal: bool = False) -> bool:
        """Adopt valid external edits as authoritative, preserving exact CSV bytes."""
        if self.catalog_is_current():
            self.catalog_sync_error = ""
            return False
        if not internal and self.lock.locked():
            raise RuntimeError("Wait for the current operation to finish before reloading chats.csv.")
        if self.catalog_journal.exists():
            raise RuntimeError("A catalog import needs recovery. Restart the app before changing the catalog.")
        if self.catalog_path.is_symlink():
            raise ValueError("Catalog must not be a symbolic link.")
        content = self.catalog_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        updated = catalog.parse_catalog(content, self.state)
        if catalog._file_hash(self.catalog_path) != digest:
            raise ValueError("Catalog changed while reading it. Try again.")
        catalog.backup_catalog(self.state, self.catalog_path)
        _write_state(self.catalog_journal, {"state": updated, "old_hash": digest, "new_hash": digest})
        if catalog._file_hash(self.catalog_path) != digest:
            raise ValueError("Catalog changed while applying it. Restart the application to recover safely.")
        _write_state(self.state_path, updated)
        marker = self.catalog_path.with_name(self.catalog_path.name + ".sha256")
        def verify():
            if catalog._file_hash(self.catalog_path) != digest:
                raise ValueError("Catalog changed while applying it. Restart the application to recover safely.")
        catalog._atomic_write(marker, lambda stream: stream.write(digest), verify=verify)
        self.state = updated
        self.catalog_journal.unlink(missing_ok=True)
        self.catalog_ready = True
        self.catalog_sync_error = ""
        return True

    def _commit_catalog_state(self, updated: dict, old_hash: str | None) -> None:
        with tempfile.TemporaryDirectory(prefix="catalog-import-") as folder:
            candidate = Path(folder) / "candidate.csv"
            catalog.write_catalog(updated, candidate)
            new_hash = catalog._file_hash(candidate)
        _write_state(self.catalog_journal, {"state": updated, "old_hash": old_hash, "new_hash": new_hash})
        try:
            catalog.write_catalog(updated, self.catalog_path, expected_hash=old_hash, importing=True)
            _write_state(self.state_path, updated)
        except BaseException:
            if catalog._file_hash(self.catalog_path) == old_hash:
                self.catalog_journal.unlink(missing_ok=True)
            raise
        self.state = updated
        self.catalog_journal.unlink(missing_ok=True)
        self.catalog_ready = True

    def mutate_catalog(self, mutate, *, internal: bool = False) -> None:
        """Apply an ordinary catalog edit only while the on-disk CSV is unchanged."""
        if not internal and self.lock.locked():
            raise RuntimeError("Wait for the current operation to finish before changing the catalog.")
        self.sync_catalog(internal=internal)
        if self.catalog_journal.exists():
            raise RuntimeError("A catalog import needs recovery. Restart the app before changing the catalog.")
        marker = self.catalog_path.with_name(self.catalog_path.name + ".sha256")
        try:
            expected = marker.read_text(encoding="utf-8-sig").strip()
        except FileNotFoundError:
            raise ValueError("Catalog may have external edits. Reload chats.csv before saving.") from None
        if catalog._file_hash(self.catalog_path) != expected:
            raise ValueError("Catalog changed outside the app. Reload chats.csv before saving.")
        updated = copy.deepcopy(self.state)
        mutate(updated)
        self._commit_catalog_state(updated, expected)

    def import_catalog(self, content: bytes | str, *, expected_hash: str | None = None) -> str:
        if self.lock.locked():
            raise RuntimeError("Wait for the current operation to finish before changing the catalog.")
        if self.catalog_journal.exists():
            raise RuntimeError("A catalog import needs recovery. Restart the app before changing the catalog.")
        if expected_hash is not None and catalog._file_hash(self.catalog_path) != expected_hash:
            raise ValueError("Catalog changed since it was opened. Reload it before saving.")
        old_hash = catalog._file_hash(self.catalog_path)
        updated = catalog.parse_catalog(content, self.state)
        backup = catalog.backup_catalog(self.state, self.catalog_path)
        catalog.backup_external_catalog(self.catalog_path, backup, expected_hash=old_hash)
        self._commit_catalog_state(updated, old_hash)
        return backup.name

    def restore_catalog(self, name: str, *, expected_hash: str | None = None) -> str:
        if self.lock.locked():
            raise RuntimeError("Wait for the current operation to finish before changing the catalog.")
        matches = {p.name: p for p in catalog.list_catalog_backups(self.catalog_path)}
        if name not in matches:
            raise ValueError("Catalog backup not found.")
        return self.import_catalog(matches[name].read_bytes(), expected_hash=expected_hash)

    def configure_encryption(self, enabled: bool, password: str = "") -> None:
        from .direct_app import _keyring, _secret_account, SERVICE
        if enabled:
            if not password or len(password) > 1024:
                raise ValueError("Enter an archive password (up to 1024 characters).")
            kr = _keyring()
            account = _secret_account(self.home) + "-zip"
            kr.set_password(SERVICE, account, password)
            if kr.get_password(SERVICE, account) != password:
                raise RuntimeError("Could not save the archive password in the OS keyring.")
        self.state["encryption_enabled"] = bool(enabled)
        self.save()

    def archive_password(self, override: str | None = None) -> str | None:
        if override:
            return override
        if not self.state.get("encryption_enabled", False):
            return None
        from .direct_app import _keyring, _secret_account, SERVICE
        password = _keyring().get_password(SERVICE, _secret_account(self.home) + "-zip")
        if not password:
            raise RuntimeError("Archive encryption is enabled but the OS keyring password is unavailable.")
        return password

    def record_archive(self, path: Path) -> None:
        names = self.state.setdefault("archive_files", [])
        if path.name not in names:
            names.append(path.name)
            self.save()

    def bind_account(self, account_id: int) -> None:
        account_id = int(account_id)
        existing = self.state.get("account_id")
        if existing is None:
            # Existing unbound state may already contain checkpoints from an
            # earlier preview. Do not silently assign them to an unknown account.
            if self.state["checkpoints"] or self.state["dialogs"] or self.state["selected"]:
                raise RuntimeError("Existing workspace data has no verified Telegram account. Use a new application home to avoid mixing accounts.")
            self.state["account_id"] = account_id
            self.save()
        elif int(existing) != account_id:
            raise RuntimeError("This workspace belongs to a different Telegram account. Use a separate application home for this account.")

    def select(self, ids: list[str], *, expected_selection: str | None = None) -> None:
        self.sync_catalog()
        if expected_selection is not None and selection_fingerprint(self.state["selected"]) != expected_selection:
            raise ValueError("The saved selection changed in another tab. Refresh the page before saving.")
        # Allow retaining a previously selected source which disappeared from a
        # refresh; the interface displays it explicitly so it can be removed.
        available = {str(item["id"]) for item in self.state["dialogs"]} | set(self.state["selected"])
        selected = list(dict.fromkeys(str(int(item)) for item in ids))
        if any(item not in available for item in selected):
            raise ValueError("A selected chat is not in the current dialog list. Refresh it first.")
        self.mutate_catalog(lambda state: state.update(selected=selected))

    def cooldown_remaining(self) -> float:
        until = self.state.get("cooldown_until")
        return max(0.0, (datetime.fromisoformat(until) - _now()).total_seconds()) if until else 0.0

    def _check_cooldown(self) -> None:
        if self.cooldown_remaining() > 0:
            until = self.state["cooldown_until"]
            raise RuntimeError(f"Telegram requested a pause until {until}.")

    def record_wait(self, seconds: int, *, job_name: str | None = None) -> str:
        until = _now() + timedelta(seconds=max(1, int(seconds)))
        self.state["cooldown_until"] = until.isoformat()
        if job_name:
            self.state["job"] = {"name": job_name, "started_at": _now().isoformat()}
        message = f"Telegram requested a pause until {until.isoformat()}."
        self.state.setdefault("job", {}).update(status="waiting", error=message)
        self.save()
        return message

    async def _run(self, name: str, operation, *, check_cooldown: bool = True):
        from telethon.errors import FloodWaitError, FloodPremiumWaitError, SlowModeWaitError, FloodTestPhoneWaitError
        async with self.lock:
            if check_cooldown:
                self._check_cooldown()
            self.state["job"] = {"name": name, "status": "running", "started_at": _now().isoformat()}
            self.save()
            try:
                result = await operation()
            except asyncio.CancelledError:
                self.state["job"].update(status="interrupted", error="The operation was interrupted. Completed chat files were kept.")
                self.save()
                raise
            except (FloodWaitError, FloodPremiumWaitError, SlowModeWaitError, FloodTestPhoneWaitError) as exc:
                raise RuntimeError(self.record_wait(exc.seconds)) from None
            except PartialExportError as exc:
                self.state["job"].update(status="partial", error=str(exc), finished_at=_now().isoformat())
                self.save()
                raise
            except Exception as exc:
                self.state["job"].update(status="failed", error=str(exc) or type(exc).__name__)
                self.save()
                raise
            self.state["job"].update(status="success", finished_at=_now().isoformat(), result=result)
            if name in {"export messages", "export snapshot", "export recent", "export group members", "export old archive"}:
                self.state["last_" + name.replace(" ", "_")] = self.state["job"].copy()
            self.save()
            if name in {"export messages", "export snapshot", "export recent", "export group members", "export old archive"}:
                from .retention import remove_expired
                try:
                    retention = self.state.get("retention", {})
                    removed = remove_expired(self.exports, self.state.get("archive_files", []), enabled=retention.get("enabled", False), days=retention.get("days", 30))
                    if removed:
                        self.state["archive_files"] = [n for n in self.state.get("archive_files", []) if n not in removed]
                        self.save()
                except (OSError, ValueError):
                    pass
                from .export_notifications import notify_export_completed
                try:
                    notify_export_completed()
                except Exception:
                    pass
            return result

    async def refresh_dialogs(self) -> list[dict[str, Any]]:
        from .telegram_ops import _dialog_record, pace_iterator

        async def refresh():
            dialogs = []
            async for dialog in pace_iterator(self.client.iter_dialogs()):
                dialogs.append(_dialog_record(dialog, set()))
            self.mutate_catalog(lambda state: state.update(dialogs=catalog.merge_dialogs(state, dialogs)), internal=True)
            return {"count": len(dialogs)}

        await self._run("refresh dialogs", refresh)
        return self.state["dialogs"]

    async def add_chat(self, reference: str) -> dict[str, Any]:
        from .chat_add import resolve_chat
        async def add():
            record, method = await resolve_chat(self.client, reference, self.state["dialogs"])
            ident = str(record["id"])
            def update(state):
                dialogs = [d for d in state["dialogs"] if str(d["id"]) != ident] + [record]
                state["dialogs"] = catalog.merge_dialogs(state, dialogs, method=method)
                if ident not in state["selected"]:
                    state["selected"].append(ident)
            self.mutate_catalog(update, internal=True)
            return {"id": record["id"], "title": record["title"]}
        return await self._run("add chat", add)

    async def export_selected(self, *, snapshot: bool = False, days: int | None = None, expected_selection: str | None = None, start: datetime | None = None, end: datetime | None = None, chat_ids: list[str] | None = None, password: str | None = None, incremental: bool = False) -> list[dict[str, Any]]:
        password = self.archive_password(password)
        from telethon import errors
        inaccessible = (ChatLookupError, errors.ChannelPrivateError, errors.ChatAdminRequiredError,
                        errors.ChatForbiddenError, errors.UserBannedInChannelError,
                        errors.ChannelInvalidError, errors.PeerIdInvalidError)
        if incremental and (snapshot or days is not None or start is not None or end is not None):
            raise ValueError("Incremental export cannot be combined with a date range.")
        if days is not None and (snapshot or not 1 <= days <= 3650):
            raise ValueError("Choose a recent period of 1 to 3650 days.")
        if start is not None and (snapshot or days is not None or start.tzinfo is None):
            raise ValueError("Choose a valid start date.")
        if end is not None and (start is None or end.tzinfo is None):
            raise ValueError("Choose a valid end date.")
        async def export():
            self.sync_catalog(internal=True)
            selected = list(self.state["selected"]) if chat_ids is None else catalog.expand_selection(self.state, chat_ids, [])
            if expected_selection is not None and selection_fingerprint(selected) != expected_selection:
                raise ValueError("The saved selection changed. Refresh the page before exporting.")
            if not selected:
                raise ValueError("Select at least one chat before exporting.")
            started_at = _now()
            until = (end or started_at) if start is not None else started_at if days is not None else None
            since = start if start is not None else until - timedelta(days=days) if until is not None else None
            if not snapshot and not incremental and since is None:
                until = started_at
                previous = self.state.get("last_selection_exports", {}).get(selection_fingerprint(selected), "")
                try:
                    since = datetime.fromisoformat(previous)
                    if since.tzinfo is None or since > until:
                        raise ValueError("Invalid previous export date")
                except (TypeError, ValueError):
                    since = until - timedelta(days=1)
            if since is not None and since > until:
                raise ValueError("The start date must be before the end date.")
            independent = snapshot or since is not None
            results, failures, completed_chats = [], [], 0
            names = [b["name"] for b in self.state.get("blocks", {}).values() if set(b.get("chat_ids", [])) & set(selected)]
            label = " + ".join(names) if names else (next((d.get("title", "") for d in self.state["dialogs"] if str(d["id"]) == selected[0]), "") if len(selected) == 1 else "selection")
            bundle = _new_bundle(self.exports, "chats", label=label or "selection", since=since, until=until)
            with tempfile.TemporaryDirectory(prefix="signal-archive-") as staging:
                for index, chat_id in enumerate(selected):
                    self.state["job"]["current_chat"] = chat_id
                    self.state["job"]["completed"] = completed_chats
                    self.state["job"]["total"] = len(selected)
                    self.state["job"]["current_messages"] = 0
                    self.save()
                    if index:
                        # A new iterator starts a new request immediately; pause
                        # between chats as well as between pages within a chat.
                        await asyncio.sleep(3)
                    checkpoint = 0 if independent else int(self.state["checkpoints"].get(chat_id, 0))
                    chat_info = next((d for d in self.state["dialogs"] if d["id"] == chat_id), None)
                    chat_messages = 0
                    def progress(count):
                        self.state["job"]["current_messages"] = chat_messages + count
                        self.save()
                    try:
                        while True:
                            result = await export_chat(self.client, int(chat_id), Path(staging), after_id=checkpoint, chat_info=chat_info, on_progress=progress, skip_empty=not snapshot, since=since, until=until, limit=None if independent else INCREMENTAL_SEGMENT_SIZE)
                            if result.get("path"):
                                source = Path(result["path"])
                                folders = []
                                for ident, block in self.state.get("blocks", {}).items():
                                    if chat_id in block.get("chat_ids", []):
                                        safe_name = re.sub(r'[^\w .-]+', '_', block["name"], flags=re.UNICODE).strip(' .')[:70] or 'Block'
                                        folders.append(f"{safe_name}_{ident[:8]}/{source.name}")
                                if folders:
                                    for name in folders:
                                        _add_to_bundle(bundle, source, name, password=password)
                                else:
                                    _add_to_bundle(bundle, source, password=password)
                                self.record_archive(bundle)
                                source.unlink()
                                result["file"] = folders[0] if folders else source.name
                                result["path"] = str(bundle)
                            count = int(result.get("messages", 0))
                            last_id = int(result["last_id"])
                            if not independent and count and last_id <= checkpoint:
                                raise RuntimeError("Export did not advance the message checkpoint.")
                            chat_messages += count
                            self.state["job"]["current_messages"] = chat_messages
                            if not independent:
                                checkpoint = last_id
                                self.state["checkpoints"][chat_id] = checkpoint
                            # A final empty page after a full segment needs no extra row.
                            if count or chat_messages == 0 or independent:
                                results.append(result)
                            self.state["job"]["results"] = list(results)
                            self.save()
                            if independent or count < INCREMENTAL_SEGMENT_SIZE:
                                break
                            await asyncio.sleep(3)
                    except inaccessible as exc:
                        failures.append({"chat_id": chat_id, "title": (chat_info or {}).get("title", ""), "error": str(exc) or type(exc).__name__})
                        self.state["job"]["failures"] = list(failures)
                        self.save()
                        continue
                    completed_chats += 1
                    self.state["job"]["completed"] = completed_chats
                    self.save()
                if failures:
                    raise PartialExportError(f"{len(failures)} chat(s) could not be exported; {completed_chats} completed. See the chat errors below.")
                listing = Path(staging) / "chats.txt"
                catalog.write_selected_chats(self.state, listing, selected)
                _add_to_bundle(bundle, listing, password=password)
                self.record_archive(bundle)
                self.state.setdefault("last_selection_exports", {})[selection_fingerprint(selected)] = (until or started_at).isoformat()
                self.save()
                return results

        return await self._run("export snapshot" if snapshot else "export recent" if days is not None else "export messages", export)

    async def export_members(self, chat_id: str, *, enrich_profiles: bool = False, password: str | None = None) -> dict[str, Any]:
        password = self.archive_password(password)
        from .telegram_ops import export_group_members

        async def export():
            dialog = next((d for d in self.state["dialogs"] if d["id"] == str(chat_id)), None)
            if not dialog or dialog["type"] != "group":
                raise ValueError("Choose a group from the refreshed dialog list.")
            self.state["job"]["current_chat"] = str(chat_id)
            self.state["job"]["current_members"] = 0
            def progress(count):
                self.state["job"]["current_members"] = count
                self.save()
            bundle = _new_bundle(self.exports, "members")
            with tempfile.TemporaryDirectory(prefix="signal-archive-") as staging:
                result = await export_group_members(self.client, int(chat_id), Path(staging), on_progress=progress, enrich_profiles=enrich_profiles)
                source = Path(result["path"])
                _add_to_bundle(bundle, source, password=password)
                self.record_archive(bundle)
                result["file"] = source.name
                result["path"] = str(bundle)
                def update(state):
                    next(d for d in state["dialogs"] if d["id"] == str(chat_id))["members_exported_at"] = _now().isoformat()
                try:
                    self.mutate_catalog(update, internal=True)
                except ValueError as exc:
                    # The archive is already safely published; preserve an
                    # external catalog edit and clearly report the missing date.
                    result["note"] = (result.get("note", "") + " Catalog export date was not updated: " + str(exc)).strip()
                return result

        return await self._run("export group members", export)

    async def export_legacy_chat(self, db_path: Path, account_id: int | None, chat_id: int, *, days: int = 0) -> dict[str, Any]:
        """Export previously archived messages locally, without Telegram requests."""
        from .legacy_archive import export_chat as export_old_chat
        password = self.archive_password()
        if not isinstance(days, int) or not 0 <= days <= 3650:
            raise ValueError("Choose 0 for all history or a period of 1 to 3650 days.")
        until = _now() if days else None
        since = until - timedelta(days=days) if until is not None else None

        async def export():
            cancelled = threading.Event()
            progress_queue: queue.SimpleQueue[int] = queue.SimpleQueue()
            job = self.state["job"]
            job["current_legacy_messages"] = 0
            self.save()

            def update_progress(count: int) -> None:
                if self.state.get("job") is job and job.get("status") == "running":
                    job["current_legacy_messages"] = count
                    self.save()

            def drain_progress() -> None:
                while True:
                    try:
                        count = progress_queue.get_nowait()
                    except queue.Empty:
                        break
                    update_progress(count)

            bundle = _new_bundle(self.exports, "old_archive")
            with tempfile.TemporaryDirectory(prefix="signal-archive-") as staging, concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="legacy-export") as executor:
                future = executor.submit(
                    export_old_chat, Path(db_path), Path(staging), account_id, int(chat_id),
                    since=since, until=until, on_progress=progress_queue.put, cancel_event=cancelled,
                )
                try:
                    while not future.done():
                        drain_progress()
                        await asyncio.sleep(0.1)
                    drain_progress()
                    result = future.result()
                    source = Path(result["path"])
                    _add_to_bundle(bundle, source, password=password)
                    self.record_archive(bundle)
                    result["file"] = source.name
                    result["path"] = str(bundle)
                    return result
                except BaseException:
                    cancelled.set()
                    while not future.done():
                        await asyncio.sleep(0.1)
                    try:
                        future.result()
                    except BaseException:
                        pass
                    raise

        # Reading an existing SQLite archive makes no Telegram API request.
        return await self._run("export old archive", export, check_cooldown=False)
