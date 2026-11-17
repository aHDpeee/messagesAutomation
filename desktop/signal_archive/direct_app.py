"""Cross-platform local Telegram workspace, with one session-owning event loop."""
from __future__ import annotations

import asyncio
import getpass
import hashlib
import html
import json
import os
import re
import secrets
import subprocess
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .direct_service import DirectService, _write_state, selection_fingerprint
from . import autostart, legacy_archive, catalog, problem_chats, session as session_ops

SERVICE = "SignalArchiveDirect"

UI_SCRIPT = r"""
let selectionDirty = false;
let refreshReady = false;
const refreshForm = document.getElementById('refreshForm');
const refreshToken = refreshForm.querySelector('input[name=token]').value;
let latestStarted = refreshForm.dataset.started;
// datetime-local has no timezone. Send an explicit offset for each boundary,
// calculated at that date, so remote devices and DST changes are respected.
document.querySelectorAll('input[type="datetime-local"]').forEach(input => {
    const instant = input.dataset.defaultInstant;
    if (instant) {
        const d = new Date(instant), pad = n => String(n).padStart(2, '0');
        if (!isNaN(d)) input.value = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    if (input.id === 'single-start') input.dataset.initial = input.value;
    input.form.addEventListener('submit', event => {
        if (!input.value) return;
        const d = new Date(input.value), pad = n => String(n).padStart(2, '0');
        const roundtrip = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
        if (isNaN(d) || roundtrip !== input.value.slice(0,16)) {
            event.preventDefault(); input.setCustomValidity('This local time does not exist due to a clock change. Choose another time.'); input.reportValidity(); return;
        }
        for (let minutes = 30; minutes <= 180; minutes += 30) {
            const alternate = new Date(d.getTime() + minutes * 60000);
            const altLocal = `${alternate.getFullYear()}-${pad(alternate.getMonth()+1)}-${pad(alternate.getDate())}T${pad(alternate.getHours())}:${pad(alternate.getMinutes())}`;
            if (altLocal === roundtrip) {
                event.preventDefault(); input.setCustomValidity('This local time occurs twice due to a clock change. Choose an unambiguous time.'); input.reportValidity(); return;
            }
        }
        input.setCustomValidity('');
        let field = input.form.querySelector(`input[name="${input.name}_instant"]`);
        if (!field) { field = document.createElement('input'); field.type = 'hidden'; field.name = input.name + '_instant'; input.form.appendChild(field); }
        field.value = d.toISOString();
    });
    input.addEventListener('input', () => input.setCustomValidity(''));
});
function markSelectionDirty() {
    selectionDirty = true;
    document.getElementById('selectionNotice').textContent = 'Selection changed. Save it before exporting.';
}
document.querySelectorAll('.chat input[name="chat"], form[action="/select"] input[name="block"]').forEach(input => input.addEventListener('change', markSelectionDirty));
function ensureSelectionSaved() {
    if (!selectionDirty) return true;
    document.getElementById('selectionNotice').textContent = 'Save your selection before exporting.';
    document.getElementById('selectionNotice').scrollIntoView({behavior: 'smooth', block: 'center'});
    return false;
}
function rememberRefresh(kind = 'refresh dialogs') {
    if (selectionDirty) {
        document.getElementById('selectionNotice').textContent = 'Save your selection before refreshing the chat list.';
        return false;
    }
    try { sessionStorage.setItem('signal-refresh', JSON.stringify({token: refreshToken, started: latestStarted, kind})); } catch (_) {}
    return true;
}
function filter() {
    let q = document.getElementById('search').value.toLowerCase();
    document.querySelectorAll('.chat').forEach(x => {
        x.hidden = !x.dataset.search.includes(q);
    });
}
let activeGroup = 'ungrouped';
let groupSavePending = false;
function syncGroupCheckboxes() {
    document.querySelectorAll('.chat').forEach(row => {
        const box = row.querySelector('.group-membership');
        if (!box) return;
        const groups = (row.dataset.groups || '').split(' ').filter(Boolean);
        box.checked = activeGroup === 'ungrouped' ? groups.length === 0 : groups.includes(activeGroup);
    });
}
function showGroup(id, button) {
    activeGroup = id;
    document.querySelectorAll('.group-tab').forEach(tab => tab.setAttribute('aria-selected', tab === button ? 'true' : 'false'));
    syncGroupCheckboxes();
    filter();
}
async function saveGroupMembership(box) {
    if (groupSavePending) { syncGroupCheckboxes(); return; }
    const row = box.closest('.chat'), group = activeGroup, value = box.checked;
    if (group === 'ungrouped' && !value) {
        box.checked = true;
        document.getElementById('group-notice').textContent = 'Choose a named group to move this chat out of Ungrouped.';
        return;
    }
    groupSavePending = true;
    const checkboxes = document.querySelectorAll('.group-membership');
    checkboxes.forEach(x => x.disabled = true);
    try {
        const fields = new URLSearchParams({token: refreshToken, chat_id: row.dataset.id, group, checked: value ? 'yes' : 'no', expected_hash: document.getElementById('group-catalog-hash').value});
        const response = await fetch('/group-membership', {method: 'POST', body: fields});
        const result = await response.json();
        if (!response.ok) throw Error(result.error || 'Could not save group.');
        row.dataset.groups = result.groups.join(' ');
        document.getElementById('group-catalog-hash').value = result.hash;
        document.getElementById('group-notice').textContent = 'Group saved.';
    } catch (error) { document.getElementById('group-notice').textContent = error.message || 'Could not save group.'; }
    finally { groupSavePending = false; checkboxes.forEach(x => x.disabled = false); syncGroupCheckboxes(); }
}
function showPage(id) {
    document.querySelectorAll('[data-page]').forEach(page => page.hidden = page.dataset.page !== id || page.dataset.offlineDisabled === 'yes');
    document.querySelectorAll('.page-tab').forEach(tab => tab.setAttribute('aria-selected', tab.dataset.target === id ? 'true' : 'false'));
}
function openSingleExport(id, title, start, end) {
    const dialog = document.getElementById('single-dialog');
    document.getElementById('single-chat-id').value = id;
    document.getElementById('single-chat-title').textContent = title;
    const startInput = document.getElementById('single-start'), endInput = document.getElementById('single-end');
    startInput.value = start || startInput.dataset.initial;
    endInput.value = end || '';
    dialog.showModal();
}
async function previewCatalog(form, operation) {
    const status = document.getElementById('catalog-status');
    try {
        if (operation === 'upload') {
            const file = form.querySelector('input[type=file]').files[0];
            if (!file) throw Error('Choose a CSV file.');
            if (file.size > 2000000) throw Error('CSV file is too large (maximum 2 MB).');
            form.querySelector('input[name=csv]').value = await file.text();
        }
        const data = new URLSearchParams(new FormData(form));
        data.set('operation', operation);
        if (['edit', 'upload'].includes(operation) && new TextEncoder().encode(data.get('csv') || '').length > 2000000) throw Error('CSV is too large (maximum 2 MB).');
        if (data.toString().length > 8000000) throw Error('CSV is too large to upload.');
        const response = await fetch('/catalog-preview', {method: 'POST', body: data});
        const result = await response.json();
        if (!response.ok) throw Error(result.error || 'CSV validation failed.');
        const expected = form.querySelector('input[name=expected_hash]');
        if (operation === 'edit' && expected.value && expected.value !== result.hash) throw Error('Catalog changed outside the browser. Reload the page before saving.');
        expected.value = result.hash;
        const warning = `Replace the current chat list?\n${result.chats} chats, ${result.selected} selected, ${result.blocks} groups.\nA backup will be saved.`;
        if (confirm(warning)) form.submit();
    } catch (error) { status.textContent = error.message || 'Unable to check the CSV.'; }
    return false;
}
function selectVisible(value) {
    document.querySelectorAll('.chat').forEach(x => {
        if (!x.hidden) {
            let input = x.querySelector('input');
            if (input.checked !== value) markSelectionDirty();
            input.checked = value;
        }
    });
}
function update() {
    fetch('/status').then(r => r.json()).then(s => {
        let j = s.job || {}, t = j.name ? j.name + ': ' + j.status : 'No activity yet.';
        latestStarted = j.started_at || '';
        let pending = null;
        try { pending = JSON.parse(sessionStorage.getItem('signal-refresh')); } catch (_) {}
        if (pending && pending.token === refreshToken && j.name === (pending.kind || 'refresh dialogs') && j.started_at && j.started_at !== pending.started && j.status !== 'running') {
            try { sessionStorage.removeItem('signal-refresh'); } catch (_) {}
            if (j.status === 'success') {
                if (!selectionDirty) { location.reload(); return; }
                refreshReady = true;
            }
        }
        if (j.total) t += '\nChats completed: ' + (j.completed || 0) + ' / ' + j.total;
        if (j.status === 'running' && j.current_chat && j.current_messages !== undefined) t += '\nCurrent chat ' + j.current_chat + ': ' + j.current_messages + ' messages read';
        if (j.status === 'running' && j.current_members !== undefined) t += '\nMembers read: ' + j.current_members;
        if (j.status === 'running' && j.current_legacy_messages !== undefined) t += '\nArchive messages read: ' + j.current_legacy_messages;
        if (j.error) t += '\n' + j.error;
        if (j.failures) j.failures.forEach(f => t += '\n' + (f.title || f.chat_id) + ': ' + f.error);
        if (j.status === 'partial' && j.results) j.results.forEach(r => { if (r.path) t += '\nSaved: ' + r.path; });
        if (j.result && j.result.path) t += '\nSaved: ' + j.result.path + (j.result.members_exported !== undefined ? '\nMembers exported: ' + j.result.members_exported : '') + '\n' + (j.result.note || '');
        if (Array.isArray(j.result)) { const saved = [...new Set(j.result.map(r => r.path).filter(Boolean))]; if (saved.length) t += '\nSaved: ' + saved.join(', '); }
        if (refreshReady) t += '\nThe new chat list is ready. Save your selection to show it.';
        else if (j.status === 'success' && j.name === 'refresh dialogs') t += '\nRefresh the page to see the new list.';
        document.getElementById('status').textContent = t;
        let alerted = '';
        try { alerted = sessionStorage.getItem('signal-add-alert') || ''; } catch (_) {}
        if (j.name === 'add chat' && j.started_at && j.started_at !== alerted && j.status !== 'running') {
            const message = (j.result && j.result.message) || (j.status === 'failed' ? j.error : '');
            if (message && (j.status === 'failed' || (j.result && j.result.problem))) {
                if (typeof alert === 'function') alert(message);
                try { sessionStorage.setItem('signal-add-alert', j.started_at); } catch (_) {}
            }
        }
    }).catch(() => document.getElementById('status').textContent = 'Cannot reach the application.');
}
update();
setInterval(update, 3000);
"""


def parse_form_datetime(form: dict, name: str) -> datetime:
    """Use the browser's absolute instant; support older local-only forms."""
    instant = form.get(name + "_instant", [""])[0]
    if instant:
        parsed = datetime.fromisoformat(instant.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("A timezone is required for the supplied instant.")
        return parsed
    return datetime.fromisoformat(form[name][0]).astimezone()


def protect_existing_session(home: Path, session_file: Path | None = None) -> None:
    """Restrict existing SQLite session files before opening them on POSIX."""
    if os.name == "nt":
        return  # Windows relies on the containing user profile's ACL.
    base = session_file if session_file is not None else home / "telegram.session"
    for suffix in ("", "-journal", "-wal", "-shm"):
        path = Path(str(base) + suffix)
        if path.is_symlink():
            raise RuntimeError(f"Refusing a symbolic link for Telegram session file: {path}")
        if path.exists():
            os.chmod(path, 0o600)


def _secret_account(home: Path) -> str:
    """Keep distinct workspaces from overwriting one another's API Hash."""
    canonical = os.path.normcase(str(home.expanduser().resolve()))
    return "api-hash-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def open_exports(path: Path) -> None:
    """Open the application's configured export directory in the file manager."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _keyring():
    import keyring
    name = (type(keyring.get_keyring()).__module__ + "." + type(keyring.get_keyring()).__name__).lower()
    if not any(x in name for x in ("secretservice", "kwallet", "windows.win", "windows.credential")):
        raise RuntimeError(f"A supported OS keyring is required; current backend is {name}.")
    return keyring


def credentials(home: Path, prompt=None) -> tuple[int, str, str]:
    """Prompt on first run; never put the API hash or login code in config."""
    path = home / "account.json"
    kr = _keyring()
    account = _secret_account(home)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        api_hash = kr.get_password(SERVICE, account)
        if not api_hash and not data.get("keyring_account"):
            # Earlier preview versions stored one shared entry. Copy it only
            # for an existing workspace and leave the old entry intact.
            api_hash = kr.get_password(SERVICE, "api-hash")
            if api_hash:
                kr.set_password(SERVICE, account, api_hash)
                if kr.get_password(SERVICE, account) != api_hash:
                    raise RuntimeError("Could not confirm the migrated API Hash in the OS keyring.")
                data["keyring_account"] = account
                _write_state(path, data)
        if not api_hash:
            raise RuntimeError("API Hash is missing from the OS keyring. Restore it before starting.")
        return int(data["api_id"]), api_hash, data["phone"]
    if prompt is None:
        print("First-time setup: obtain API ID and API Hash at https://my.telegram.org")
        api_id = input("API ID: ").strip()
        api_hash = getpass.getpass("API Hash (hidden): ").strip()
        phone = input("Phone number with country code: ").strip()
    else:
        api_id = prompt("API ID").strip()
        api_hash = prompt("API Hash", secret=True).strip()
        phone = prompt("Phone number with country code (for example +12345678900)").strip()
    if not api_id.isdigit() or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash) or not re.fullmatch(r"\+[0-9]{7,15}", phone):
        raise ValueError("Check API ID, 32-character API Hash, and international phone number.")
    kr.set_password(SERVICE, account, api_hash)
    if kr.get_password(SERVICE, account) != api_hash:
        raise RuntimeError("Could not confirm the API Hash in the OS keyring.")
    _write_state(path, {"api_id": int(api_id), "phone": phone, "keyring_account": account})
    return int(api_id), api_hash, phone


class SessionLock:
    """Advisory cross-process lock held while this app owns its session."""
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b"0"); self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Signal Archive is already running for this account.") from None
        return self

    def __exit__(self, *_):
        self.file.close()


def _forget_login_unlocked(home: Path) -> None:
    """Remove local credentials; the caller must hold the workspace session lock."""
    if not home.exists():
        raise ValueError("Workspace does not exist.")
    config = home / "account.json"
    if config.is_symlink():
        raise RuntimeError("Refusing a symbolic link for account.json.")
    data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    account = _secret_account(home)
    if data.get("keyring_account") not in (None, account):
        raise RuntimeError("Workspace path changed; refusing to remove another workspace's keyring entry.")
    kr = _keyring()
    if kr.get_password(SERVICE, account) is not None:
        kr.delete_password(SERVICE, account)
        if kr.get_password(SERVICE, account) is not None:
            raise RuntimeError("The OS keyring did not remove the API Hash.")
    # Do not remove the legacy shared keyring entry: another old workspace
    # may still depend on it. Its removal requires separate inspection.
    for suffix in (".session", ".session-journal", ".session-wal", ".session-shm"):
        (home / ("telegram" + suffix)).unlink(missing_ok=True)
    config.unlink(missing_ok=True)


def forget_telegram_login(home: Path) -> None:
    """Remove this workspace's local credentials while preserving export state."""
    if not home.exists():
        raise ValueError("Workspace does not exist.")
    with SessionLock(home / "session.lock"):
        _forget_login_unlocked(home)


def finish_session_switch(home: Path, service: DirectService | None = None) -> None:
    """Apply a requested session switch while this process owns the lock."""
    if not session_ops.switch_requested(home):
        return
    if service is not None:
        service.state["session_source"] = "own"
        service.state["account_switch_pending"] = True
        service.save()
    _forget_login_unlocked(home)
    session_ops.clear_session_switch(home)


class Runtime:
    def __init__(self, loop, service, *, external_session: bool = False, offline_legacy: bool = False):
        self.loop, self.service = loop, service
        self.external_session = external_session
        self.offline_legacy = offline_legacy
        self.future = None
        self.scheduled_task = None
        self.scheduled_cancel_requested = False
        self.guard = threading.Lock()
        self.lan_gateway = None

    def start_lan(self, origin, certificate, private_key, local_port, bind_ip):
        from .lan_gateway import LanGateway
        import importlib.util
        if importlib.util.find_spec("webauthn") is None:
            raise RuntimeError("LAN access requires the optional webauthn package; install Signal Archive with the lan extra.")
        with self.guard:
            if self.lan_gateway is not None:
                raise RuntimeError("LAN access is already running.")
            gateway = LanGateway(origin, Path(certificate).expanduser(), Path(private_key).expanduser(), self.service.home, local_port, host=bind_ip)
            try:
                gateway.start()
            except BaseException:
                gateway.server.server_close()
                raise
            self.lan_gateway = gateway

    def stop_lan(self):
        with self.guard:
            gateway, self.lan_gateway = self.lan_gateway, None
        if gateway:
            gateway.stop()

    def submit(self, operation):
        with self.guard:
            if self.future and not self.future.done():
                raise RuntimeError("A Telegram operation is already running.")
            if self.call(lambda: bool(self.scheduled_task and not self.scheduled_task.done()) or self.service.lock.locked()):
                raise RuntimeError("A Telegram operation is already running.")
            self.future = asyncio.run_coroutine_threadsafe(operation(), self.loop)
            self.future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)

    def call(self, operation):
        async def invoke():
            return operation()
        return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(timeout=10)

    def cancel(self):
        with self.guard:
            if self.future and not self.future.done():
                self.future.cancel()
                return
            def cancel_scheduled():
                task = self.scheduled_task
                if not task or task.done():
                    raise RuntimeError("No operation is running.")
                self.scheduled_cancel_requested = True
                task.cancel()
            self.call(cancel_scheduled)

    def shutdown(self):
        """Stop the loop; run() cancels work, disconnects, and releases the lock."""
        self.loop.call_soon_threadsafe(self.loop.stop)


async def scheduler(runtime: Runtime):
    """Run once per local calendar day while the app is running, with catch-up."""
    while True:
        service = runtime.service
        if not service.lock.locked() and hasattr(service, "sync_catalog"):
            try:
                service.sync_catalog()
            except (OSError, ValueError, RuntimeError) as exc:
                service.catalog_sync_error = str(exc)
        settings = service.state.get("schedule", {})
        now = datetime.now().astimezone()
        target = settings.get("time", "03:15")
        try:
            hour, minute = map(int, target.split(":"))
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < due:
                due -= timedelta(days=1)
            day = due.date().isoformat()
            if settings.get("enabled") and settings.get("last_day") != day and settings.get("failed_day") != day and service.state["selected"] and service.catalog_is_current():
                if (not runtime.future or runtime.future.done()) and not service.lock.locked():
                    try:
                        runtime.scheduled_cancel_requested = False
                        runtime.scheduled_task = asyncio.create_task(service.export_selected())
                        await runtime.scheduled_task
                    except asyncio.CancelledError:
                        if not runtime.scheduled_cancel_requested:
                            raise
                        current = service.state.get("schedule", {})
                        if current.get("time", "03:15") == target:
                            current["failed_day"] = day
                            service.save()
                    except Exception as exc:
                        # Only temporary network errors and explicit FloodWaits are retried.
                        if isinstance(exc, OSError) or service.state.get("job", {}).get("status") == "waiting":
                            await asyncio.sleep(900)
                        else:
                            current = service.state.get("schedule", {})
                            if current.get("time", "03:15") == target:
                                current["failed_day"] = day
                                service.save()
                    else:
                        current = service.state.get("schedule", {})
                        if current.get("time", "03:15") == target:
                            current["last_day"] = day
                            current.pop("failed_day", None)
                            service.save()
                    finally:
                        runtime.scheduled_task = None
        except (ValueError, TypeError):
            pass
        await asyncio.sleep(30)


def make_handler(runtime: Runtime, token: str):
    class Handler(BaseHTTPRequestHandler):
        def send(self, body, status=200, content_type="text/html; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.valid_host():
                self.send("Invalid host", 403); return
            if re.fullmatch(r'/instance-proof/[0-9a-f]{64}', self.path):
                from .shortcut_launcher import prove
                secret = getattr(runtime, 'instance_secret', None)
                if not secret:
                    self.send('Not found', 404); return
                self.send(prove(secret, self.path.rsplit('/', 1)[1]), content_type='text/plain; charset=utf-8')
                return
            if self.path == "/codex-guide":
                import sys
                root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
                try:
                    guide = (root / "CODEX_ARCHIVE_GUIDE.md").read_text(encoding="utf-8")
                except OSError:
                    self.send("Guide unavailable", 404); return
                self.send("<!doctype html><meta charset=utf-8><title>Codex guide</title><pre style='white-space:pre-wrap'>" + html.escape(guide) + "</pre>")
                return
            if self.path == "/status":
                state = runtime.call(lambda: {"job": json.loads(json.dumps(runtime.service.state.get("job", {})))})
                self.send(json.dumps(state, ensure_ascii=False), content_type="application/json; charset=utf-8"); return
            if self.path != "/":
                self.send("Not found", 404); return
            def sync_visible_catalog():
                service = runtime.service
                if hasattr(service, "sync_catalog"):
                    try:
                        service.sync_catalog()
                    except (OSError, ValueError, RuntimeError) as exc:
                        service.catalog_sync_error = str(exc)
            runtime.call(sync_visible_catalog)
            state = runtime.call(lambda: json.loads(json.dumps(runtime.service.state)))
            problems = problem_chats.classify_problems(state)
            offline = getattr(runtime, "offline_legacy", False)
            hidden_live = "hidden" if offline else ""
            offline_disabled = "data-offline-disabled=yes" if offline else ""
            offline_notice = (f"<section><h2>Офлайн: старый архив</h2><p>Telegram не подключается. Файлы сохраняются в {html.escape(str(runtime.service.exports))}.</p><form method=post action=/open-exports><input type=hidden name=token value='{token}'><button>Открыть папку экспорта</button></form></section>" if offline else "")
            chosen = set(state["selected"])
            selection_hash = selection_fingerprint(state["selected"])
            try:
                default_start = datetime.fromisoformat(state.get("last_selection_exports", {}).get(selection_hash, ""))
                if default_start.tzinfo is None:
                    raise ValueError("Missing timezone")
                default_start = default_start.astimezone()
            except (ValueError, TypeError):
                default_start = datetime.now().astimezone() - timedelta(days=1)
            start_value = default_start.strftime("%Y-%m-%dT%H:%M")
            rows = []
            for d in sorted(state["dialogs"], key=lambda d: (str(d["id"]) not in chosen, str(d.get("title", "")).casefold())):
                title = html.escape(d["title"], quote=True)
                kind = html.escape(d["type"], quote=True)
                ident = html.escape(d["id"], quote=True)
                accessible = d.get("accessible", True)
                member = f"<button class=secondary type=submit form=members name=chat_id value='{ident}'>Export members</button>" if d["type"] == "group" and accessible else ""
                settings = state.get("single_export_settings", {}).get(str(d["id"]), {})
                single_start = html.escape(str(settings.get("start", "")), quote=True)
                single_end = html.escape(str(settings.get("end", "")), quote=True)
                single = f"<button class=secondary type=button data-id='{ident}' data-title='{title}' data-start='{single_start}' data-end='{single_end}' onclick='openSingleExport(this.dataset.id, this.dataset.title, this.dataset.start, this.dataset.end)'>Export chat</button>" if accessible else ""
                username = d.get("username") or ""
                if re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
                    chat_title = f"<a href='tg://resolve?domain={username}' title='Open chat in Telegram'>{title} ↗</a>"
                else:
                    chat_title = f"{title} <small title='Telegram provides no public chat link'>No public link</small>"
                availability = " · inaccessible" if not accessible else ""
                groups = ' '.join(str(key) for key, block in state.get('blocks', {}).items() if str(d['id']) in block.get('chat_ids', []))
                groups = html.escape(groups, quote=True)
                export_checked = " checked" if ident in chosen else ""
                group_checked = " checked" if not groups else ""
                rows.append(f"<div class=chat data-id='{ident}' data-groups='{groups}' data-type='{kind}' data-search='{title.lower()} {kind}'><input type=checkbox name=chat value='{ident}'{export_checked} hidden><span class=chat-title>{chat_title} <small>{kind}{availability}</small></span><label><input type=checkbox class=group-membership{group_checked} onchange='saveGroupMembership(this)'> In this group</label>{single}{member}</div>")
            visible_ids = {d["id"] for d in state["dialogs"]}
            for missing in sorted(chosen - visible_ids):
                ident = html.escape(missing, quote=True)
                rows.append(f"<div class=chat data-id='{ident}' data-type='unavailable' data-search='{ident} unavailable'><input type=checkbox name=chat value='{ident}' checked hidden><span class=chat-title>{ident} <small>Unavailable in latest refresh — selection is stored in chats.csv</small></span><label><input type=checkbox class=group-membership checked onchange='saveGroupMembership(this)'> In this group</label></div>")
            schedule = state.get("schedule", {})
            retention = state.get("retention", {})
            retention_checked = "checked" if retention.get("enabled") else ""
            encryption_checked = "checked" if state.get("encryption_enabled") else ""
            member_enrichment_checked = "checked" if state.get("member_enrichment_enabled") else ""
            retention_days = int(retention.get("days", 30))
            problem_days_value = problem_chats.problem_days(state)
            checked = "checked" if schedule.get("enabled") else ""
            startup_checked = "checked" if autostart.enabled() else ""
            startup_note = ("Старая сессия Telegram Archive обнаруживается автоматически. Чтобы войти под другим аккаунтом, нажмите «Сменить сессию»." if getattr(runtime, "external_session", False) else "При запуске используется сохранённая сессия; сменить её можно кнопкой «Сменить сессию».")
            shortcut_offer = (f"<section><h2>Desktop shortcut</h2><p>Add Signal Archive to your desktop for quick access?</p><form method=post action=/shortcut-offer><input type=hidden name=token value='{token}'><button name=choice value=yes>Add shortcut</button> <button name=choice value=no>Not now</button></form><small>You can create it later under Shortcuts and access.</small></section>" if not state.get('shortcut_offer_answered') and not offline else '')
            gateway = getattr(runtime, "lan_gateway", None)
            if gateway:
                requests = "".join(f"<li>{html.escape(item['device_name'])} · code {html.escape(item['code'])} <form method=post action=/lan-approve><input type=hidden name=token value='{token}'><button name=id value='{html.escape(item['id'], quote=True)}'>Approve</button></form><form method=post action=/lan-deny><input type=hidden name=token value='{token}'><button name=id value='{html.escape(item['id'], quote=True)}'>Deny</button></form></li>" for item in gateway.gate.pending() if not item['approved'])
                devices = "".join(f"<li>{html.escape(record['name'])} <form method=post action=/lan-revoke><input type=hidden name=token value='{token}'><button name=id value='{html.escape(ident, quote=True)}'>Revoke</button></form></li>" for ident, record in list(gateway.store.records.items()))
                lan_html = f"<p>HTTPS gateway active at {html.escape(gateway.origin)}</p><form method=post action=/lan-stop><input type=hidden name=token value='{token}'><button>Stop LAN access</button></form><h3>Pending device approvals</h3><ul>{requests or '<li>None</li>'}</ul><h3>Paired devices</h3><ul>{devices or '<li>None</li>'}</ul>"
            else:
                lan_html = f"<p>LAN access is off. Supply a trusted certificate for a stable DNS hostname; install the LAN extra first. A browser must trust the certificate to use a passkey.</p><form method=post action=/lan-start><input type=hidden name=token value='{token}'><label>Private local IP <input name=bind_ip placeholder='192.168.1.20' required></label><label>HTTPS origin <input name=origin placeholder='https://archive.example:8443' required></label><label>Certificate path <input name=certificate required></label><label>Private key path <input name=private_key required></label><button>Start LAN access</button></form>"
            selected_time = html.escape(schedule.get("time", "03:15"), quote=True)
            last_started = html.escape(str(state.get("job", {}).get("started_at", "")), quote=True)
            last_export_rows = []
            for key, kind_label in (("last_export_messages", "New messages"), ("last_export_recent", "Recent period"), ("last_export_snapshot", "Snapshot")):
                for result in state.get(key, {}).get("result", []):
                    label = html.escape(str(result.get("chat_title") or result.get("chat_id") or "Chat"))
                    path = result.get("path")
                    detail = f"{html.escape(str(path))} — {int(result.get('messages', 0))} messages" if path else "No messages in this export"
                    last_export_rows.append(f"<li>{kind_label}, {label}: {detail}</li>")
            member_result = state.get("last_export_group_members", {}).get("result", {})
            if isinstance(member_result, dict) and member_result.get("path"):
                last_export_rows.append(f"<li>Group members: {html.escape(str(member_result['path']))} — {int(member_result.get('members_exported', 0))} members</li>")
            last_export_html = "".join(last_export_rows)
            block_rows = []
            block_options = []
            for block_id, block in state.get("blocks", {}).items():
                bid = html.escape(str(block_id), quote=True)
                name = html.escape(str(block["name"]), quote=True)
                checks = "".join(f"<label><input type=checkbox name=chat value='{html.escape(str(d['id']), quote=True)}' {'checked' if str(d['id']) in block.get('chat_ids', []) else ''}> {html.escape(str(d['title']))}</label><br>" for d in state["dialogs"])
                block_options.append(f"<label><input type=checkbox name=block value='{bid}'> {name}</label> ")
                block_rows.append(f"<details><summary>{name}</summary><form method=post action=/block-assign><input type=hidden name=token value='{token}'><input type=hidden name=block value='{bid}'><div class=chat-list tabindex=0 aria-label='Chats in {name}'>{checks}</div><button>Save block chats</button></form><form method=post action=/block-rename><input type=hidden name=token value='{token}'><input type=hidden name=block value='{bid}'><input name=name value='{name}' required><button>Rename</button></form><form method=post action=/block-delete onsubmit=\"return confirm('Delete this block?')\"><input type=hidden name=token value='{token}'><input type=hidden name=block value='{bid}'><button>Delete block</button></form></details>")
            blocks_html = ''.join(block_rows)
            block_select_html = ''.join(block_options)
            group_tabs = ''.join(f"<button type=button class=group-tab role=tab aria-selected=false data-group='{html.escape(str(key), quote=True)}' onclick='showGroup(this.dataset.group,this)'>{html.escape(str(block['name']))}</button>" for key, block in state.get('blocks', {}).items())
            try:
                csv_text, csv_hash = runtime.call(runtime.service.catalog_content)
                backups = runtime.call(runtime.service.catalog_backups)
                catalog_editor = html.escape(csv_text)
                catalog_hash = html.escape(csv_hash, quote=True)
                backup_options = ''.join(f"<option value='{html.escape(name, quote=True)}'>{html.escape(name)}</option>" for name in backups)
                catalog_notice = ""
            except (OSError, UnicodeError, ValueError) as exc:
                catalog_editor, catalog_hash, backup_options = "", "", ""
                catalog_notice = "Cannot open catalog: " + html.escape(str(exc))
            if not runtime.call(runtime.service.catalog_is_current):
                error = runtime.call(lambda: getattr(runtime.service, "catalog_sync_error", ""))
                catalog_conflict = ("<section role=alert><strong>Cannot apply changes from chats.csv.</strong> "
                    "The file was kept unchanged; previous chat settings remain active. "
                    + (html.escape(error) + " " if error else "") +
                    "Correct the file or open <button type=button onclick=\"showPage('settings')\">Settings</button> "
                    "to review or restore a backup.</section>")
            else:
                catalog_conflict = ""
            old_archive_html = ""
            if offline and legacy_archive.DEFAULT_DB.is_file():
                try:
                    old_chats = legacy_archive.list_chats(legacy_archive.DEFAULT_DB)
                    options = []
                    for chat in old_chats:
                        account = "none" if chat["account_id"] is None else str(int(chat["account_id"]))
                        chat_id = str(int(chat["id"]))
                        title = html.escape(str(chat["title"]))
                        options.append(f"<option value='{account}:{chat_id}'>{title} · account {account}</option>")
                    if options:
                        old_archive_html = ("<section><h2>Старый архив Telegram Archive</h2>"
                            "<p>Экспорт уже сохранённых сообщений без обращения к Telegram. Старый архив не изменяется; в нём могут отсутствовать новые сообщения.</p>"
                            f"<form method=post action=/legacy-export onsubmit='return ensureSelectionSaved()'><input type=hidden name=token value='{token}'>"
                            f"<select name=source required>{''.join(options)}</select> "
                            "<label>Последние дни (0 = всё) <input type=number name=days min=0 max=3650 value=0 required></label> "
                            "<button>Экспортировать старый архив</button></form></section>")
                    else:
                        old_archive_html = "<section><h2>Старый архив</h2><p>Сохранённых чатов не найдено.</p></section>"
                except Exception as exc:
                    old_archive_html = f"<section><h2>Старый архив</h2><p>Не удалось прочитать базу: {html.escape(type(exc).__name__)}.</p></section>"
            elif offline:
                old_archive_html = f"<section><h2>Старый архив не найден</h2><p>{html.escape(str(legacy_archive.DEFAULT_DB))}</p></section>"
            password_field = "<label>ZIP password (optional) <input type=password name=archive_password autocomplete=new-password></label>"
            lan_section = "" if self.headers.get("X-Signal-Remote") == "1" else f"<details data-page=settings hidden><summary>LAN access and paired devices</summary>{lan_html}</details>"

            stored_problems = {str(key) for key in state.get("problem_chats", {})}
            def problem_row(item):
                ident = html.escape(str(item["id"]), quote=True)
                title = html.escape(str(item["title"]))
                kind = html.escape(str(item["type"]))
                message = html.escape(str(item["message"]))
                groups = html.escape(", ".join(item["groups"]))
                choose = (f'<label><input type=checkbox name=problem_chat value="{ident}"> В экспорт участников</label>'
                          if item["selectable"] else "")
                resolve = ""
                if str(item["id"]) in stored_problems:
                    resolve = (f'<form method=post action=/problem-resolve><input type=hidden name=token value="{token}">'
                               f'<input type=hidden name=chat_id value="{ident}"><button class=secondary>Убрать</button></form>')
                return (f'<div class=problem-row><span><b>{title}</b> <small>{kind}'
                        + (f" · {groups}" if groups else "") + f'</small><br><small class=problem-reason>{message}</small></span>'
                        + f'<span>{choose}{resolve}</span></div>')

            def problem_list(items, empty):
                return "".join(problem_row(item) for item in items) or f"<p><small>{empty}</small></p>"

            if problems["all"]:
                problem_tab = (f'<button class="page-tab problem-tab" data-target=problems aria-selected=false '
                               f'onclick="showPage(\'problems\')">Проблемные чаты ({len(problems["all"])})</button>')
                problem_section = (
                    f'<section data-page=problems {offline_disabled}><h2>Проблемные чаты</h2>'
                    '<p>Красная вкладка появляется только когда есть чаты, требующие внимания.</p>'
                    '<h3>Требуют внимания</h3><p><small>Не ответившие боты, чаты с 0 сообщений и группы/каналы с неподтверждённым запросом на вступление.</small></p>'
                    f'<div class=chat-list>{problem_list(problems["urgent"], "Проблем пока нет.")}</div>'
                    f'<h3>Нет сообщений больше {problems["days"]} дней</h3>'
                    f'<div class=chat-list>{problem_list(problems["stale"], "Таких чатов нет.")}</div>'
                    f'<form method=post action=/members-batch><input type=hidden name=token value="{token}">'
                    f'<input type=hidden name=scope value=problem>'
                    f'<label><input type=checkbox name=enrich_profiles value=yes {member_enrichment_checked}> Биографии и каналы (медленнее)</label> '
                    f'<label>Метка плана <input name=plan placeholder="необязательно" maxlength=200></label> '
                    '<button>Экспорт участников выбранных здесь групп</button></form></section>')
            else:
                problem_tab = ""
                problem_section = ""
            self.send(f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Signal Archive</title><style>
            :root{{color-scheme:dark;--panel:#172331;--line:#304357;--muted:#a7bbcb;--accent:#65d6c0}}*{{box-sizing:border-box}}body{{font:16px/1.5 system-ui,sans-serif;background:#0c1520;color:#edf5f8;max-width:1000px;margin:auto;padding:28px}}h1{{font-size:2rem;margin-bottom:0}}h2{{font-size:1.2rem;margin-top:0}}h3{{font-size:1rem}}section{{background:var(--panel);padding:22px;border:1px solid var(--line);border-radius:16px;margin:16px 0;box-shadow:0 8px 24px #0003}}button,input,select{{font:inherit}}button{{background:var(--accent);color:#07231e;border:0;border-radius:9px;padding:10px 16px;font-weight:650;cursor:pointer}}button:hover{{filter:brightness(1.12)}}button.secondary{{background:#263c4d;color:#e6f4f5}}input:not([type=checkbox]),select{{background:#101d2a;color:inherit;border:1px solid #52697b;border-radius:8px;padding:9px}}input[type=checkbox]{{accent-color:var(--accent);width:18px;height:18px;vertical-align:middle}}.chat-list{{max-height: min(55vh, 32rem);overflow-y:auto;overscroll-behavior:contain;border:1px solid var(--line);border-radius:10px;display:flex;flex-direction:column}}.recent-list{{max-height:12rem;overflow-y:auto;overflow-wrap:anywhere;border:1px solid var(--line);border-radius:10px}}.recent-list ul{{padding-right:16px}}.chat{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border-bottom:1px solid var(--line)}}.chat label{{display:flex;align-items:center;gap:10px}}.chat-title{{display:flex;align-items:center;gap:10px;flex:1;min-width:0}}.chat:last-child{{border-bottom:0}}.chat:has(input[name="chat"]:checked){{order:-1}}dialog{{background:var(--panel);color:inherit;border:1px solid var(--line);border-radius:16px;max-width:min(95vw,600px)}}dialog::backdrop{{background:#000a}}.tabs{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}}[aria-selected=true]{{outline:2px solid white}}.problem-tab{{background:#8c2f2f;color:#fff;font-weight:750}}.problem-tab[aria-selected=true]{{outline:3px solid #ff9b9b}}.problem-row{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px;border-bottom:1px solid var(--line)}}.problem-row:last-child{{border-bottom:0}}.problem-reason{{color:#ffb3b3}}[hidden]{{display:none!important}}small{{color:var(--muted)}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}.activity{{border-color:var(--accent)}}#status{{background:#0e1b27;padding:14px;border-radius:9px;margin:0 0 12px}}a{{color:#83e4d2;text-decoration:none}}a:hover{{text-decoration:underline}}summary{{cursor:pointer;color:#a1ddd3;margin-top:16px}}@media(max-width:600px){{body{{padding:12px}}section{{padding:16px}}.chat{{align-items:flex-start;flex-wrap:wrap}}}}
            </style></head><body><h1>Signal Archive</h1><p>Text-only Telegram exports on this computer. <a href='/codex-guide' title='Codex archive analysis guide'>(i) Codex guide</a></p>{offline_notice}<nav class=tabs role=tablist aria-label='Pages'><button class=page-tab data-target=main aria-selected=true onclick="showPage('main')">Chats and export</button>{problem_tab}<button class=page-tab data-target=settings aria-selected=false onclick="showPage('settings')">Settings</button></nav>
            {shortcut_offer}{catalog_conflict}<section class=activity><h2>Latest activity</h2><pre id=status role=status aria-live=polite>Checking activity…</pre><form method=post action=/cancel><input type=hidden name=token value='{token}'><button class=secondary>Stop current operation</button></form><small>Progress updates automatically. Completed files stay in the export folder.</small><form method=post action=/shutdown onsubmit="return confirm('Close Signal Archive? Any running export will stop.')"><input type=hidden name=token value='{token}'><p><button class=secondary>Close Signal Archive</button></p></form></section>{lan_section}
            <details data-page=settings hidden><summary>Shortcuts and access</summary><p>Create a shortcut to start Signal Archive or open its existing browser interface.</p><form method=post action=/shortcut><input type=hidden name=token value='{token}'><button name=location value=desktop>Create desktop shortcut</button> <button name=location value=application>Create shortcut in application folder</button></form><small>The preview uses a browser for compatibility. The interface is currently accessible only on this computer. <a href='https://qna.habr.com/q/800445' target=_blank rel='noopener noreferrer'>About local network access</a></small></details>
            <section data-page=main {hidden_live} {offline_disabled}><h2>Export</h2><p>{len(chosen)} chats selected. No media files are downloaded.</p><form method=post action=/export onsubmit='return ensureSelectionSaved()'><input type=hidden name=token value='{token}'><input type=hidden name=selection value='{selection_hash}'><label>From <input type=datetime-local name=start value='{start_value}' data-default-instant='{default_start.isoformat()}' required></label> <button>Export through now</button></form><details><summary>More export options</summary><p>Choose a specific time interval.</p><form method=post action=/interval onsubmit='return ensureSelectionSaved()'><input type=hidden name=token value='{token}'><input type=hidden name=selection value='{selection_hash}'><label>From <input type=datetime-local name=start value='{start_value}' data-default-instant='{default_start.isoformat()}' required></label> <label>To <input type=datetime-local name=end required></label> <button>Export interval</button></form></details><p>Files: {html.escape(str(runtime.service.exports))}</p><form method=post action=/open-exports><input type=hidden name=token value='{token}'><button>Open export folder</button></form><form method=post action=/members-batch><input type=hidden name=token value='{token}'><label><input type=checkbox name=enrich_profiles value=yes {member_enrichment_checked}> Also fetch bios and personal channels (slower)</label> <label>Plan <input name=plan placeholder='optional' maxlength=200></label> <button name=scope value=all_groups>Export members of all groups</button> <button class=secondary name=scope value=selected_groups>Export members of selected groups</button></form><h3>Most recent successful exports</h3><div class=recent-list tabindex=0 aria-label="Most recent successful exports"><ul>{last_export_html or '<li>No successful export yet.</li>'}</ul></div></section>
            {old_archive_html}
            <section data-page=settings hidden><h2>Schedule</h2><form method=post action=/schedule><input type=hidden name=token value='{token}'><label><input type=checkbox name=enabled value=yes {checked}> Daily while the app is running</label> <input type=time name=time value='{selected_time}' required> <button>Save schedule</button></form><small>If the app was closed at the scheduled time, it runs when reopened.</small><form method=post action=/encryption><input type=hidden name=token value='{token}'><p><label><input type=checkbox name=enabled value=yes {encryption_checked}> Password-protect all new ZIP archives</label> <input type=password name=password autocomplete=new-password placeholder="Archive password"> <button>Save encryption</button></p><small>Stored in the OS keyring for scheduled exports. AES ZIP needs a compatible archive reader.</small></form><form method=post action=/retention><input type=hidden name=token value='{token}'><p><label><input type=checkbox name=enabled value=yes {retention_checked}> Delete app-created archives older than</label> <input type=number name=days min=1 max=36500 value='{retention_days}' required> days <button>Save retention</button></p></form><form method=post action=/problem-settings><input type=hidden name=token value='{token}'><p><label>чаты в проблемные через N дней <input type=number name=days min=1 max=3650 value='{problem_days_value}' required></label> <button>Сохранить</button></p></form><form method=post action=/switch-session onsubmit="return confirm('Сменить сессию? Приложение закроется. При смене аккаунта прежний список чатов будет сохранён в резервной копии, а для нового аккаунта создан отдельный список. Экспорты сохранятся.')"><input type=hidden name=token value='{token}'><p><button class=secondary>Сменить сессию</button> <small>Использовать другую сессию или аккаунт; старый файл Telegram Archive не удаляется.</small></p></form><form method=post action=/autostart><input type=hidden name=token value='{token}'><p><label><input type=checkbox name=enabled value=yes {startup_checked}> Start Signal Archive when I sign in</label> <button>Save startup setting</button></p></form><small>{html.escape(startup_note)}</small></section>

            <section data-page=main {hidden_live} {offline_disabled}><h2>Choose chats</h2><form id=refreshForm data-started='{last_started}' method=post action=/refresh onsubmit='return rememberRefresh()'><input type=hidden name=token value='{token}'><button>Refresh Telegram chats</button></form><form method=post action=/add-chat onsubmit="return rememberRefresh('add chat')"><input type=hidden name=token value='{token}'><input name=reference placeholder='Chat link, @username, ID or name' required><button>Add chat</button></form><p id=selectionNotice role=status></p><p><input id=search placeholder='Search chats' oninput='filter()'></p><p><button type=button onclick='selectVisible(true)'>Select visible</button> <button type=button onclick='selectVisible(false)'>Clear visible</button></p>
            <small>Member lists contain only data visible to your account.</small>
            <dialog id=single-dialog aria-labelledby=single-chat-title><h2 id=single-chat-title>Export chat</h2><form id=single-export method=post action=/single-export><input type=hidden name=token value='{token}'><input id=single-chat-id type=hidden name=chat_id><p><label>From <input id=single-start type=datetime-local name=start value='{start_value}' data-default-instant='{default_start.isoformat()}' required></label></p><p><label>To (leave blank for now) <input id=single-end type=datetime-local name=end></label></p>{password_field}<p><button>Export chat</button> <button type=button class=secondary onclick="document.getElementById('single-dialog').close()">Cancel</button></p><small>Dates are remembered for this chat. Passwords are not saved here.</small></form></dialog><p>Chat catalog: {html.escape(str(runtime.service.home / 'chats.csv'))}</p>
            <p>Selected chats are shown first. The export checkbox is kept in the catalog instead of being shown here; use <b>Select visible</b>, <b>Clear visible</b>, <b>Select whole blocks</b> or edit <code>chats.csv</code> to change it. Check <b>In this group</b> to change group membership.</p><nav class=tabs role=tablist aria-label='Chat groups'><button type=button class=group-tab role=tab aria-selected=true onclick="showGroup('ungrouped',this)">Ungrouped</button>{group_tabs}</nav><p id=group-notice role=status></p><input id=group-catalog-hash type=hidden value='{catalog_hash}'><form method=post action=/select><input type=hidden name=token value='{token}'><input type=hidden name=selection value='{selection_hash}'><p>Select whole blocks: {block_select_html or 'No blocks yet.'}</p><div class=chat-list tabindex=0 aria-label='Chat list'>{''.join(rows) or '<p>Refresh the list to see your Telegram chats.</p>'}</div><p><button>Save selection</button></p></form></section>
            {problem_section}
            <section data-page=settings hidden><h2>Chat catalog</h2><p>CSV file: <code>{html.escape(str(runtime.service.home / 'chats.csv'))}</code></p><p id=catalog-status role=status>{catalog_notice}</p>
            <p>Groups are stored per chat in the CSV <code>blocks</code> column. Enter one name or several names separated by commas; leave it empty for Ungrouped. For a group name containing a comma, use a JSON list such as <code>[&quot;Work, projects&quot;]</code>. The <code>type</code> column is the Telegram chat type, not the group. Replace or save the CSV and refresh this page to apply valid changes; you can also upload a CSV below. Invalid files are kept unchanged and reported.</p>
            <form method=post action=/catalog-reload onsubmit="previewCatalog(this,'reload'); return false"><input type=hidden name=token value='{token}'><input type=hidden name=expected_hash value='{catalog_hash}'><button>Reload from local CSV</button></form>
            <form method=post action=/catalog-upload onsubmit="previewCatalog(this,'upload'); return false"><input type=hidden name=token value='{token}'><input type=hidden name=expected_hash value='{catalog_hash}'><input type=hidden name=csv><label>Upload CSV <input type=file accept='.csv,text/csv' required></label> <button>Validate and upload</button></form>
            <form method=post action=/catalog-restore onsubmit="previewCatalog(this,'restore'); return false"><input type=hidden name=token value='{token}'><input type=hidden name=expected_hash value='{catalog_hash}'><label>Restore backup <select name=name required>{backup_options or '<option value="">No backups yet</option>'}</select></label> <button>Review and restore</button></form></section>
            <section data-page=settings hidden><h2>Member export options</h2><form id=members method=post action=/members><input type=hidden name=token value='{token}'><label><input type=checkbox name=enrich_profiles value=yes {member_enrichment_checked}> Also fetch visible bios and personal channels (up to 100 profiles; slower)</label></form><small>These options apply when you use Export members next to a group. Phone numbers are included only when Telegram shows them.</small></section><section data-page=settings hidden><h2>Manage groups</h2><form method=post action=/block-create><input type=hidden name=token value='{token}'><input name=name placeholder='New block' maxlength=100 required> <button>Create block</button></form>{blocks_html}</section><script>{UI_SCRIPT}</script></body></html>''')

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.valid_host() or self.headers.get("Origin") != origin:
                self.send("Invalid origin", 403); return
            match = re.fullmatch(r'/instance-shutdown/([0-9a-f]{64})/([0-9a-f]{64})', self.path)
            if match:
                from .shortcut_launcher import prove
                secret = getattr(runtime, 'instance_secret', None)
                if not secret or not secrets.compare_digest(match.group(2), prove(secret, 'shutdown:' + match.group(1))):
                    self.send('Invalid instance proof', 403); return
                self.send('Closing')
                runtime.shutdown()
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                form = parse_qs(self.rfile.read(size).decode("utf-8"))
                if not secrets.compare_digest(form.get("token", [""])[0], token):
                    self.send("Invalid token", 403); return
                if getattr(runtime, "offline_legacy", False) and self.path not in {"/legacy-export", "/open-exports", "/cancel", "/shutdown", "/switch-session"}:
                    raise ValueError("В офлайн-режиме доступен только старый локальный архив.")
                if self.path == "/group-membership":
                    def change_membership():
                        service = runtime.service
                        chat_id = str(int(form.get("chat_id", [""])[0]))
                        group = form.get("group", [""])[0]
                        checked = form.get("checked", [""])[0]
                        expected = form.get("expected_hash", [""])[0]
                        if not re.fullmatch(r"[0-9a-f]{64}", expected) or catalog._file_hash(service.catalog_path) != expected:
                            raise ValueError("Catalog changed. Reload the page before editing groups.")
                        if checked not in {"yes", "no"}:
                            raise ValueError("Invalid group membership.")
                        def mutate(state):
                            catalog.expand_selection(state, [chat_id], [])
                            blocks = state.get("blocks", {})
                            if group == "ungrouped":
                                if checked != "yes":
                                    raise ValueError("Choose a named group to move a chat out of Ungrouped.")
                                for block in blocks.values():
                                    block["chat_ids"] = [item for item in block.get("chat_ids", []) if str(item) != chat_id]
                            else:
                                if group not in blocks:
                                    raise ValueError("Group not found. Reload the page.")
                                ids = [str(item) for item in blocks[group].get("chat_ids", []) if str(item) != chat_id]
                                if checked == "yes":
                                    ids.append(chat_id)
                                catalog.assign_block(state, group, ids)
                        service.mutate_catalog(mutate)
                        groups = [key for key, block in service.state.get("blocks", {}).items() if chat_id in block.get("chat_ids", [])]
                        return {"groups": groups, "hash": catalog._file_hash(service.catalog_path)}
                    try:
                        result = runtime.call(change_membership)
                        self.send(json.dumps(result), content_type="application/json; charset=utf-8")
                    except (ValueError, RuntimeError, OSError) as exc:
                        self.send(json.dumps({"error": str(exc)}), 400, content_type="application/json; charset=utf-8")
                    return
                elif self.path == "/catalog-preview":
                    def preview_catalog():
                        service = runtime.service
                        operation = form.get("operation", [""])[0]
                        if operation in {"edit", "upload"}:
                            content = form.get("csv", [""])[0]
                        elif operation == "reload":
                            content = service.catalog_path.read_bytes()
                        elif operation == "restore":
                            backups = {p.name: p for p in catalog.list_catalog_backups(service.catalog_path)}
                            name = form.get("name", [""])[0]
                            if name not in backups:
                                raise ValueError("Catalog backup not found.")
                            content = backups[name].read_bytes()
                        else:
                            raise ValueError("Unknown catalog operation.")
                        parsed = catalog.parse_catalog(content, service.state)
                        return {"chats": len(parsed.get("dialogs", [])), "selected": len(parsed.get("selected", [])), "blocks": len(parsed.get("blocks", {})), "hash": catalog._file_hash(service.catalog_path)}
                    try:
                        result = runtime.call(preview_catalog)
                        self.send(json.dumps(result), content_type="application/json; charset=utf-8")
                    except (OSError, ValueError) as exc:
                        self.send(json.dumps({"error": str(exc)}), 400, content_type="application/json; charset=utf-8")
                    return
                elif self.path in {"/catalog-edit", "/catalog-upload", "/catalog-reload", "/catalog-restore"}:
                    def change_catalog():
                        service = runtime.service
                        expected = form.get("expected_hash", [""])[0] or None
                        if self.path == "/catalog-restore":
                            if expected is not None and catalog._file_hash(service.catalog_path) != expected:
                                raise ValueError("Catalog changed since preview. Preview it again.")
                            service.restore_catalog(form.get("name", [""])[0], expected_hash=expected)
                        else:
                            content = service.catalog_path.read_bytes() if self.path == "/catalog-reload" else form.get("csv", [""])[0]
                            service.import_catalog(content, expected_hash=expected)
                    runtime.call(change_catalog)
                elif self.path == "/lan-start":
                    runtime.start_lan(form["origin"][0], form["certificate"][0], form["private_key"][0], self.server.server_port, form["bind_ip"][0])
                elif self.path == "/lan-stop":
                    runtime.stop_lan()
                elif self.path in {"/lan-approve", "/lan-deny", "/lan-revoke"}:
                    gateway = getattr(runtime, "lan_gateway", None)
                    if gateway is None:
                        raise ValueError("LAN access is not running.")
                    ident = form["id"][0]
                    if self.path == "/lan-approve":
                        gateway.gate.approve(ident)
                    elif self.path == "/lan-deny":
                        gateway.gate.deny(ident)
                    else:
                        gateway.ceremonies.revoke(ident)
                        gateway.sessions.revoke(ident)
                elif self.path == "/refresh":
                    runtime.submit(runtime.service.refresh_dialogs)
                elif self.path == "/add-chat":
                    reference = form.get("reference", [""])[0]
                    runtime.submit(lambda: runtime.service.add_chat(reference))
                elif self.path == "/export":
                    expected = form.get("selection", [""])[0]
                    if not re.fullmatch(r"[0-9a-f]{64}", expected):
                        raise ValueError("Refresh the page before exporting.")
                    start = parse_form_datetime(form, "start")
                    password = form.get('archive_password', [''])[0] or None
                    runtime.submit(lambda: runtime.service.export_selected(expected_selection=expected, start=start, password=password))
                elif self.path == "/interval":
                    expected = form.get("selection", [""])[0]
                    if not re.fullmatch(r"[0-9a-f]{64}", expected):
                        raise ValueError("Refresh the page before exporting.")
                    start = parse_form_datetime(form, "start")
                    end = parse_form_datetime(form, "end")
                    if start > end:
                        raise ValueError("The start date must be before the end date.")
                    password = form.get('archive_password', [''])[0] or None
                    runtime.submit(lambda: runtime.service.export_selected(expected_selection=expected, start=start, end=end, password=password))
                elif self.path == "/snapshot":
                    expected = form.get("selection", [""])[0]
                    if not re.fullmatch(r"[0-9a-f]{64}", expected):
                        raise ValueError("Refresh the page before exporting.")
                    runtime.submit(lambda: runtime.service.export_selected(snapshot=True, expected_selection=expected))
                elif self.path == "/recent":
                    days = int(form.get("days", ["0"])[0])
                    if not 1 <= days <= 3650:
                        raise ValueError("Choose a recent period of 1 to 3650 days.")
                    expected = form.get("selection", [""])[0]
                    if not re.fullmatch(r"[0-9a-f]{64}", expected):
                        raise ValueError("Refresh the page before exporting.")
                    runtime.submit(lambda: runtime.service.export_selected(days=days, expected_selection=expected))
                elif self.path == "/cancel":
                    runtime.cancel()
                elif self.path == "/shutdown":
                    self.send("<!doctype html><meta charset=utf-8><title>Signal Archive closed</title><p>Signal Archive is closing. You can close this tab.</p>")
                    runtime.shutdown()
                    return
                elif self.path == "/shortcut":
                    from .shortcuts import application_directory, create_shortcut, desktop_directory, launcher_command
                    location = form.get('location', [''])[0]
                    if location not in ('desktop', 'application'):
                        raise ValueError('Choose where to create the shortcut.')
                    directory = desktop_directory() if location == 'desktop' else application_directory()
                    create_shortcut(directory, launcher_command(runtime.service.home, runtime.service.exports, getattr(runtime, 'external_session', False)))
                elif self.path == "/shortcut-offer":
                    choice = form.get('choice', [''])[0]
                    if choice not in ('yes', 'no'):
                        raise ValueError('Choose whether to add the shortcut.')
                    if choice == 'yes':
                        from .shortcuts import create_shortcut, desktop_directory, launcher_command
                        create_shortcut(desktop_directory(), launcher_command(runtime.service.home, runtime.service.exports, getattr(runtime, 'external_session', False)))
                    def remember_shortcut_choice():
                        runtime.service.state['shortcut_offer_answered'] = True
                        runtime.service.save()
                    runtime.call(remember_shortcut_choice)
                elif self.path == "/open-exports":
                    open_exports(runtime.service.exports)
                elif self.path == "/members":
                    chat_id = form["chat_id"][0]
                    enrich_profiles = form.get("enrich_profiles", [""])[0] == "yes"
                    def remember_member_settings():
                        runtime.service.state["member_enrichment_enabled"] = enrich_profiles
                        runtime.service.save()
                    runtime.call(remember_member_settings)
                    password = form.get('archive_password', [''])[0] or None
                    runtime.submit(lambda: runtime.service.export_members(chat_id, enrich_profiles=enrich_profiles, password=password))
                elif self.path == "/members-batch":
                    scope = form.get("scope", [""])[0]
                    if scope not in {"all_groups", "selected_groups", "problem"}:
                        raise ValueError("Выберите, какие группы экспортировать.")
                    def member_ids():
                        runtime.service.sync_catalog()
                        state = runtime.service.state
                        if scope == "all_groups":
                            ids = [str(d["id"]) for d in state["dialogs"] if d.get("type") == "group"]
                        elif scope == "selected_groups":
                            groups = {str(d["id"]) for d in state["dialogs"] if d.get("type") == "group"}
                            ids = [str(item) for item in state["selected"] if str(item) in groups]
                        else:
                            allowed = {item["id"] for item in problem_chats.classify_problems(state)["all"] if item["selectable"]}
                            ids = list(form.get("problem_chat", []))
                            if any(item not in allowed for item in ids):
                                raise ValueError("Выберите группы из вкладки проблемных чатов.")
                        return [str(int(item)) for item in dict.fromkeys(ids) if str(item).lstrip("-").isdigit()]
                    ids = runtime.call(member_ids)
                    if not ids:
                        raise ValueError("В выбранной области нет групп.")
                    enrich_profiles = form.get("enrich_profiles", [""])[0] == "yes"
                    plan = form.get("plan", [""])[0]
                    password = form.get("archive_password", [""])[0] or None
                    runtime.submit(lambda: runtime.service.export_members_batch(
                        ids, plan=plan, enrich_profiles=enrich_profiles, password=password))
                elif self.path == "/problem-settings":
                    days = int(form.get("days", ["30"])[0])
                    if not 1 <= days <= 3650:
                        raise ValueError("Выберите период от 1 до 3650 дней.")
                    def save_problem_days():
                        runtime.service.state["problem_chat_days"] = days
                        runtime.service.save()
                    runtime.call(save_problem_days)
                elif self.path == "/problem-resolve":
                    ident = form.get("chat_id", [""])[0]
                    def resolve_problem():
                        problem_chats.clear_problem(runtime.service.state, ident)
                        runtime.service.save()
                    runtime.call(resolve_problem)
                elif self.path == "/switch-session":
                    session_ops.request_session_switch(runtime.service.home)
                    self.send("<!doctype html><meta charset=utf-8><title>Signal Archive</title><p>Локальный вход будет удалён, приложение закроется. Запустите его снова, чтобы войти заново.</p>")
                    runtime.shutdown()
                    return
                elif self.path == "/single-export":
                    chat_id = form["chat_id"][0]
                    start = parse_form_datetime(form, "start")
                    end = parse_form_datetime(form, "end") if form.get("end", [""])[0] else None
                    if end is not None and start > end:
                        raise ValueError("The start date must be before the end date.")
                    def save_single_settings():
                        catalog.expand_selection(runtime.service.state, [chat_id], [])
                        runtime.service.state.setdefault("single_export_settings", {})[chat_id] = {
                            "start": form["start"][0], "end": form.get("end", [""])[0],
                        }
                        runtime.service.save()
                    runtime.call(save_single_settings)
                    password = form.get('archive_password', [''])[0] or None
                    runtime.submit(lambda: runtime.service.export_selected(chat_ids=[chat_id], start=start, end=end, password=password))
                elif self.path == "/legacy-export":
                    source = form.get("source", [""])[0]
                    if not re.fullmatch(r"(?:none|-?\d+):-?\d+", source):
                        raise ValueError("Выберите чат из старого архива.")
                    account, chat_id = source.split(":", 1)
                    account_id = None if account == "none" else int(account)
                    days = int(form.get("days", ["0"])[0])
                    if not 0 <= days <= 3650:
                        raise ValueError("Выберите период от 0 до 3650 дней.")
                    runtime.submit(lambda: runtime.service.export_legacy_chat(legacy_archive.DEFAULT_DB, account_id, int(chat_id), days=days))
                elif self.path == "/select":
                    expected = form.get("selection", [""])[0]
                    if not re.fullmatch(r"[0-9a-f]{64}", expected):
                        raise ValueError("Refresh the page before saving your selection.")
                    runtime.call(lambda: runtime.service.select(catalog.expand_selection(runtime.service.state, form.get("chat", []), form.get("block", [])), expected_selection=expected))
                elif self.path in {"/block-create", "/block-rename", "/block-delete", "/block-assign"}:
                    def update_block(state):
                        if self.path == "/block-create":
                            catalog.create_block(state, form["name"][0])
                        elif self.path == "/block-rename":
                            catalog.rename_block(state, form["block"][0], form["name"][0])
                        elif self.path == "/block-delete":
                            catalog.delete_block(state, form["block"][0])
                        else:
                            catalog.assign_block(state, form["block"][0], form.get("chat", []))
                    runtime.call(lambda: runtime.service.mutate_catalog(update_block))
                elif self.path == "/schedule":
                    value = form.get("time", [""])[0]
                    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
                        raise ValueError("Choose a valid time.")
                    def save():
                        old = runtime.service.state.get("schedule", {})
                        runtime.service.state["schedule"] = {"enabled": form.get("enabled") == ["yes"], "time": value, "last_day": old.get("last_day")}
                        runtime.service.save()
                    runtime.call(save)
                elif self.path == "/encryption":
                    enabled = form.get("enabled") == ["yes"]
                    password = form.get("password", [""])[0]
                    runtime.call(lambda: runtime.service.configure_encryption(enabled, password))
                elif self.path == "/retention":
                    days = int(form.get("days", ["30"])[0])
                    if not 1 <= days <= 36500:
                        raise ValueError("Choose a retention period from 1 to 36500 days.")
                    def save_retention():
                        runtime.service.state["retention"] = {"enabled": form.get("enabled") == ["yes"], "days": days}
                        runtime.service.save()
                    runtime.call(save_retention)
                elif self.path == "/autostart":
                    autostart.configure(form.get("enabled") == ["yes"], runtime.service.home, runtime.service.exports)
                else:
                    self.send("Not found", 404); return
                self.send_response(303); self.send_header("Location", "/"); self.end_headers()
            except Exception as exc:
                self.send(f"<p>{html.escape(str(exc))}</p><a href='/'>Back</a>", 400)

        def log_message(self, *_):
            pass
    return Handler


async def _connect_client(client, phone: str, require_existing_auth: bool = False, prompt=None):
    # Telethon's start() runs its own loop when called outside a running loop.
    # Calling it inside this coroutine consistently gives us an awaitable.
    if require_existing_auth:
        from telethon import errors, functions
        await client.connect()
        try:
            # is_user_authorized() suppresses every RPCError, including
            # FloodWait. Ask directly so cooldown errors remain observable.
            await client(functions.updates.GetStateRequest())
        except errors.UnauthorizedError:
            raise RuntimeError("Старая сессия больше не авторизована. Новый код автоматически не запрашивался.")
    else:
        if prompt is None:
            await client.start(phone=phone)
        else:
            await client.start(phone=phone,
                code_callback=lambda: prompt("Telegram sign-in code", secret=True),
                password=lambda: prompt("Two-step verification password", secret=True))
    return await client.get_me()


async def _disconnect_client(client):
    # disconnect() also switches between sync and async based on loop state.
    await client.disconnect()


def wait_for_saved_cooldown(service: DirectService) -> None:
    """Wait locally, making no Telegram requests, until the saved deadline."""
    announced = False
    while True:
        remaining = service.cooldown_remaining()
        if remaining <= 0:
            return
        if not announced:
            print(f"Telegram requested a pause until {service.state['cooldown_until']}. Waiting before connecting.", flush=True)
            announced = True
        time.sleep(min(remaining, 60))


def run(home: Path, exports: Path, port: int = 8765, *, open_browser: bool = True, wait_for_cooldown: bool = False, session_file: Path | None = None, require_existing_auth: bool = False, offline_legacy: bool = False, browser_auth: bool = False):
    login = None
    if browser_auth and not offline_legacy:
        from .browser_login import BrowserLogin
        login = BrowserLogin()

    def invoke():
        return _run(home, exports, port, open_browser=open_browser, wait_for_cooldown=wait_for_cooldown,
                    session_file=session_file, require_existing_auth=require_existing_auth,
                    offline_legacy=offline_legacy, login=login)

    try:
        try:
            return invoke()
        except Exception as exc:
            if login is None:
                raise
            login.fail(f"Signal Archive не смог запуститься.\n\n{exc}")
            if not login.reset_requested:
                return
            session_ops.request_session_switch(home)
            return invoke()
    finally:
        if login:
            login.close()


def _run(home: Path, exports: Path, port: int = 8765, *, open_browser: bool = True, wait_for_cooldown: bool = False, session_file: Path | None = None, require_existing_auth: bool = False, offline_legacy: bool = False, login=None):
    from telethon import TelegramClient
    from telethon import errors
    from telethon.errors import FloodWaitError, FloodPremiumWaitError, SlowModeWaitError, FloodTestPhoneWaitError
    if os.name != "nt":
        # Telethon creates the SQLite session and sidecars itself; a restrictive
        # process umask protects newly created files even in an existing folder.
        os.umask(0o077)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    with SessionLock(home / "session.lock"):
        service = DirectService(None, home, exports)
        finish_session_switch(home, service)
        if offline_legacy and (session_file is not None or require_existing_auth or wait_for_cooldown):
            raise ValueError("Офлайн-режим нельзя совмещать с параметрами сессии.")
        if not offline_legacy:
            session_file, require_existing_auth = session_ops.resolve_startup_session(
                service.state, session_file, require_existing_auth)
            if session_file is not None:
                session_file = session_file.expanduser().absolute()
                if session_file.is_symlink() or not session_file.is_file() or session_file.suffix != ".session":
                    raise ValueError("Старый файл сессии не найден или недопустим.")
            protect_existing_session(home, session_file)
            if wait_for_cooldown:
                try:
                    wait_for_saved_cooldown(service)
                except KeyboardInterrupt:
                    return
            else:
                service._check_cooldown()
            api_id, api_hash, phone = credentials(home, prompt=login.prompt) if login else credentials(home)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = None
        try:
            if not offline_legacy:
                client = TelegramClient(str(session_file if session_file is not None else home / "telegram"), api_id, api_hash, flood_sleep_threshold=0, loop=loop)
                service.client = client
                while True:
                    try:
                        connection = _connect_client(client, phone, require_existing_auth, prompt=login.prompt) if login else _connect_client(client, phone, require_existing_auth)
                        account = loop.run_until_complete(connection)
                        break
                    except (FloodWaitError, FloodPremiumWaitError, SlowModeWaitError, FloodTestPhoneWaitError) as exc:
                        message = service.record_wait(exc.seconds, job_name="connect")
                        if not wait_for_cooldown:
                            raise RuntimeError(message) from None
                        wait_for_saved_cooldown(service)
                    except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.SessionExpiredError, errors.AuthKeyDuplicatedError):
                        raise RuntimeError("Telegram отклонил сохранённую сессию. Откройте Settings → «Сменить сессию» (или используйте forget-telegram-login) и войдите заново; экспорт и настройки будут сохранены.") from None
                    except (errors.PhoneNumberInvalidError, errors.ApiIdInvalidError):
                        raise RuntimeError("Telegram rejected the phone number or API credentials. Проверьте данные и выберите «Сменить сессию» в Settings.") from None
                    except errors.PhoneNumberBannedError:
                        raise RuntimeError("Telegram reports that this phone number is banned. Resolve this with Telegram before trying again.") from None
                    except errors.RPCError as exc:
                        raise RuntimeError(f"Telegram rejected startup ({type(exc).__name__}). Check the account and try again later.") from None
                if account is None:
                    raise RuntimeError("Telegram did not return the authorized account. Please sign in again.")
                service.bind_account(account.id)
            runtime = Runtime(loop, service, external_session=session_file is not None, offline_legacy=offline_legacy)
            from .shortcuts import ensure_application_shortcut
            ensure_application_shortcut(home, exports, session_file is not None)
            server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(runtime, secrets.token_urlsafe(32)))
            from .shortcut_launcher import save_identity
            runtime.instance_secret = save_identity(home, server.server_port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            server_started = True
            url = f"http://127.0.0.1:{server.server_port}/"
            print(f"Signal Archive is running at {url}. Keep this window open; press Ctrl+C to exit.")
            if login and login.opened:
                login.finish(url)
            elif open_browser:
                webbrowser.open(url)
            if not offline_legacy:
                loop.create_task(scheduler(runtime))
            loop.run_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if 'runtime' in locals() and getattr(runtime, 'lan_gateway', None):
                runtime.stop_lan()
            if 'runtime' in locals() and getattr(runtime, 'instance_secret', None):
                from .shortcut_launcher import remove_identity
                remove_identity(home, runtime.instance_secret)
            if 'server' in locals():
                if locals().get('server_started', False):
                    server.shutdown(); thread.join()
                server.server_close()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            if client is not None:
                loop.run_until_complete(_disconnect_client(client))
            loop.close()
            finish_session_switch(home, service)
