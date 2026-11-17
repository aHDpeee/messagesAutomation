#!/usr/bin/env python3
"""Local Telegram export manager for the existing telegram-archive installation.

The archive daemon remains the owner of Telegram synchronization. This manager only
reads its local SQLite chat index and invokes the installed CLI for selected-chat exports.
"""
from __future__ import annotations
import argparse, fcntl, html, json, os, re, secrets, shutil, sqlite3, subprocess, sys, threading, uuid, csv, tempfile
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "backups" / "telegram_backup.db"
EXPORTS = Path(os.environ.get("XDG_DOWNLOAD_DIR", str(Path.home() / "Downloads"))) / "Signal Archive"
STATE = ROOT / "export-state.json"
CONFIG = ROOT / "export-config.json"
ENVFILE = ROOT / ".env"
PY = ROOT / ".venv" / "bin" / "python"
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"
EXPORT_SERVICE = "telegram-archive-export.service"
EXPORT_TIMER = "telegram-archive-export.timer"
RUN_LOCK = threading.Lock()
PROCESS_LOCK = ROOT / ".export.lock"
JOB_FILE = ROOT / "export-job.json"


def load_json(path: Path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError): return default


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600); tmp.replace(path)


def run_systemctl(*args, check=True):
    if not shutil.which("systemctl"):
        raise RuntimeError("systemctl is unavailable; scheduled exports require systemd user services.")
    p = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
    if check and p.returncode: raise RuntimeError((p.stderr or p.stdout or "systemctl failed").strip())
    return p


def archived_chat_ids() -> set[int]:
    if not DB.exists(): return set()
    con = sqlite3.connect(DB)
    try:
        return {int(row[0]) for row in con.execute("SELECT DISTINCT id FROM chats")}
    except sqlite3.Error:
        return set()
    finally:
        con.close()


def chats_from_db():
    """Archived-only fallback; live dialogs are returned by scheduler IPC."""
    if not DB.exists(): return [], "Archive database is not ready. Live Telegram discovery requires the scheduler service."
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT id, title, type, username, updated_at, participants_count
            FROM chats ORDER BY updated_at DESC, title COLLATE NOCASE LIMIT 5000
        """).fetchall()
        result=[]
        for row in rows:
            item=dict(row); cid=str(item["id"])
            result.append({"id":cid,"title":str(item.get("title") or cid),
                           "type":str(item.get("type") or "Telegram chat"),
                           "username":item.get("username") or "",
                           "participants_count":item.get("participants_count"),
                           "updated":str(item.get("updated_at") or ""),
                           "processed":True})
        return result, ""
    except Exception as exc:
        return [], "Could not read chat list: " + type(exc).__name__
    finally:
        con.close()


def export_recent_from_db(ids: list[str], days: int, hours: int) -> dict:
    """Export exact message-date interval from the upstream SQLite archive."""
    if days < 0 or days > 3650 or hours < 0 or hours > 23 or (days == 0 and hours == 0):
        raise ValueError("Choose a period greater than zero (up to 10 years).")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    now = datetime.now(timezone.utc)
    parsed_ids = [int(x) for x in ids]
    if not parsed_ids:
        raise ValueError("Select at least one Telegram source.")
    placeholders = ",".join("?" for _ in parsed_ids)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"""SELECT account_id, chat_id, id, date, sender_id, sender_name, text,
                       reply_to_msg_id, reply_to_top_id, raw_data, edit_date, is_deleted
                FROM messages
                WHERE chat_id IN ({placeholders}) AND date >= ? AND date <= ?
                ORDER BY date, account_id, chat_id, id""",
            [*parsed_ids, cutoff.replace(tzinfo=None).isoformat(), now.replace(tzinfo=None).isoformat()]
        ).fetchall()
        chats = con.execute(
            f"SELECT account_id,id,type,title,username FROM chats WHERE id IN ({placeholders})",
            parsed_ids
        ).fetchall()
    finally:
        con.close()
    EXPORTS.mkdir(parents=True, exist_ok=True, mode=0o700); os.chmod(EXPORTS, 0o700)
    out = EXPORTS / f"recent_{days}d_{hours}h_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}.jsonl"
    fd, tmp_name = tempfile.mkstemp(prefix=".recent-", suffix=".part", dir=EXPORTS)
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for row in rows:
                item = dict(row)
                raw = item.get("raw_data")
                try: item["raw"] = json.loads(raw) if raw else None
                except ValueError: item["raw"] = None
                item.pop("raw_data", None)
                item["date"] = str(item["date"])
                stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
                count += 1
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(tmp_name, 0o600); os.replace(tmp_name, out)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    manifest = {"kind":"recent-period", "from":cutoff.isoformat(), "to":now.isoformat(),
                "source_ids":parsed_ids, "messages":count, "file":str(out),
                "format":"JSONL", "note":"Messages are filtered by their Telegram message date. Media blobs and full version history are not included in this lightweight export."}
    save_json(ROOT / "last-recent-export.json", manifest)
    return manifest


def save_selection(ids):
    ids=list(dict.fromkeys(map(str,ids)))
    if not ids: raise ValueError("Select at least one chat.")
    available={c["id"] for c in chats_from_db()[0]}
    if any(cid not in available for cid in ids):
        raise ValueError("A selected chat is no longer in the archive index. Refresh and try again.")
    cfg=load_json(CONFIG,{"time":"03:15"}); cfg["selected"]=ids; save_json(CONFIG,cfg)
    # Deliberately do not write CHAT_IDS or restart the acquisition service.
    install_export_timer(cfg.get("time","03:15"))
    return ids


def write_env(values: dict[str, str]):
    lines=ENVFILE.read_text(encoding="utf-8").splitlines() if ENVFILE.exists() else []
    keys=set(values); lines=[line for line in lines if not any(line.startswith(k+"=") for k in keys)]
    lines += [f"{k}={v}" for k,v in values.items()]
    tmp=ENVFILE.with_suffix(".env.tmp"); tmp.write_text("\n".join(lines)+"\n",encoding="utf-8")
    os.chmod(tmp,0o600); tmp.replace(ENVFILE)


def install_export_timer(tm: str):
    hour,minute=map(int,tm.split(":")); calendar=f"*-*-* {hour:02d}:{minute:02d}:00"
    UNIT_DIR.mkdir(parents=True,exist_ok=True)
    # systemd path directives are plain absolute paths, not JSON-quoted strings.
    def arg(p): return '"' + str(p).replace('\\','\\\\').replace('"','\\"') + '"'
    service=("[Unit]\nDescription=Telegram Archive selected-chat delta export\n\n[Service]\nType=oneshot\n"
             f"WorkingDirectory={ROOT}\nEnvironmentFile={ENVFILE}\nUMask=0077\nExecStart={arg(PY)} {arg(ROOT/'manager.py')} --export\n")
    timer=("[Unit]\nDescription=Daily Telegram Archive delta export\n\n[Timer]\n"
           f"OnCalendar={calendar}\nPersistent=true\nUnit={EXPORT_SERVICE}\n\n[Install]\nWantedBy=timers.target\n")
    (UNIT_DIR/EXPORT_SERVICE).write_text(service,encoding="utf-8")
    (UNIT_DIR/EXPORT_TIMER).write_text(timer,encoding="utf-8")
    run_systemctl("daemon-reload"); run_systemctl("enable","--now",EXPORT_TIMER)


def validate_export(path: Path):
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Export command returned success but produced no file.")
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Export file is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list) or not isinstance(payload.get("chats"), list):
        raise RuntimeError("Export JSON is missing the expected chats/messages structure.")
    os.chmod(path, 0o600)
    return payload


def export_job(recent_hours: float | None = None):
    if not RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("An export is already running in this manager.")
    lock_handle=None
    job_id=uuid.uuid4().hex
    try:
        PROCESS_LOCK.parent.mkdir(parents=True,exist_ok=True)
        lock_handle=PROCESS_LOCK.open("a+")
        os.chmod(PROCESS_LOCK,0o600)
        try: fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError("Another export process is already running. The duplicate request was not started.")
        cfg=load_json(CONFIG,{}) ; ids=list(dict.fromkeys(map(str,cfg.get("selected",[]))))
        if not ids: raise RuntimeError("Select and save at least one chat first.")
        state=load_json(STATE,{}) ; started=datetime.now(timezone.utc); completed=[]
        EXPORTS.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(EXPORTS,0o700)
        save_json(JOB_FILE,{"job_id":job_id,"status":"running","started_at":started.isoformat(),"completed":[]})
        for cid in ids:
            prev=state.get(str(cid)); changed_since=None
            folder=EXPORTS/re.sub(r"[^0-9A-Za-z_-]","_",str(cid)); folder.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(folder,0o700)
            stamp=started.strftime("%Y-%m-%d_%H-%M-%S_%f_UTC")
            outfile=folder/(stamp+".json")
            part=folder/("."+stamp+"."+job_id+".part.json")
            cmd=[str(PY),"-m","telegram_archive","--data-dir",str(DATA),"export","-o",str(part),"-c",str(cid)]
            if recent_hours is not None:
                start=(started-timedelta(hours=recent_hours)).isoformat(timespec="microseconds").replace("+00:00","Z")
                end=started.isoformat(timespec="microseconds").replace("+00:00","Z")
                cmd=[str(PY),"-m","telegram_archive","--data-dir",str(DATA),"export-chat",f"--chat={cid}","--from",start,"--to",end,"-o",str(part)]
            elif prev:
                parsed=datetime.fromisoformat(prev.replace("Z","+00:00"))
                changed_since=(parsed-timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
                cmd += ["--changed-since",changed_since]
            try:
                proc=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,preexec_fn=lambda: os.umask(0o077))
                if proc.returncode: raise RuntimeError(f"Export failed for chat {cid}: {(proc.stderr or proc.stdout)[-2000:]}")
                validate_export(part)
                if outfile.exists(): raise RuntimeError("Refusing to overwrite an existing export file.")
                part.replace(outfile)
            except Exception:
                try: part.unlink()
                except FileNotFoundError: pass
                raise
            if recent_hours is None:
                state[str(cid)]=started.isoformat(timespec="seconds").replace("+00:00","Z"); save_json(STATE,state)
            completed.append(str(cid))
            save_json(JOB_FILE,{"job_id":job_id,"status":"running","started_at":started.isoformat(),"completed":completed,"current_chat":None})
        result={"job_id":job_id,"started_at":started.isoformat(),"completed":completed,"status":"success","mode":f"recent_{recent_hours:g}h" if recent_hours is not None else "delta"}
        save_json(ROOT/"last-export.json",result); save_json(JOB_FILE,result)
        return completed
    except Exception as exc:
        failed={"job_id":job_id,"started_at":datetime.now(timezone.utc).isoformat(),"status":"failed","error":str(exc)}
        save_json(ROOT/"last-export.json",failed); save_json(JOB_FILE,failed)
        raise
    finally:
        if lock_handle:
            try: fcntl.flock(lock_handle.fileno(),fcntl.LOCK_UN); lock_handle.close()
            except OSError: pass
        RUN_LOCK.release()


CSS="""
:root{color-scheme:dark;font:15px system-ui;background:#101214;color:#eceef0}*{box-sizing:border-box}body{width:min(100%,460px);margin:0 auto;padding:18px 14px 36px}header{position:sticky;top:0;background:#101214;z-index:4;padding:8px 0} .source-list{max-height:58vh;overflow:auto;overscroll-behavior:contain;padding-right:4px}.category-tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin:10px 0}.category-tabs button{padding:9px 4px;font-size:12px}.chat[hidden]{display:none}header{margin-bottom:22px}h1{font-size:30px;margin:4px 0}.muted,small{color:#9299a2}.tabs{display:flex;gap:8px;margin:18px 0}.tabs a,.button{display:inline-block;padding:10px 15px;border-radius:9px;background:#ff5500;color:white;text-decoration:none;border:0;cursor:pointer;font:inherit}.tabs a.secondary{background:#252a30;color:#ddd}.card{background:#191d21;border:1px solid #30363d;border-radius:14px;padding:18px;margin:14px 0}.chat{display:flex;gap:12px;align-items:center;padding:12px 4px;border-bottom:1px solid #30363d}.chat input{width:18px;height:18px;accent-color:#ff5500}.chat b{display:block}.chat small{display:block;margin-top:2px}.search,input[type=time],input[type=text],input[type=password]{font:inherit;background:#101316;color:#eee;border:1px solid #414951;border-radius:8px;padding:11px}.search{width:100%;margin:8px 0 12px}.button.secondary{background:#30363d}.notice{border-left:3px solid #ff5500;padding:10px 14px;background:#24201e}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.row>*{margin:4px 0}.error{color:#ff9d8c}.success{color:#9be0ae}.busy-overlay[hidden]{display:none}.busy-overlay{position:fixed;inset:0;background:#101214ee;z-index:20;display:grid;place-items:center}.busy-panel{background:#191d21;border:1px solid #30363d;border-radius:16px;padding:24px;text-align:center;width:min(85%,320px)}.spinner{width:34px;height:34px;border:3px solid #414951;border-top-color:#ff5500;border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 14px}@keyframes spin{to{transform:rotate(360deg)}}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#101316;padding:12px;border-radius:8px}
"""

class Handler(BaseHTTPRequestHandler):
    token=""
    def send(self,body,code=200):
        data=body.encode(); self.send_response(code); self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(data)
    def page(self,body):
        self.send(f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Signal Archive</title><style>{CSS}</style></head><body><div id=busy class=busy-overlay hidden><div class=busy-panel><div class=spinner></div><b>Working…</b><p class=muted>Keep this page open. The request will not be submitted again by this form.</p></div></div><header><div class=muted>SIGNAL ARCHIVE / TELEGRAM</div><h1>Export workspace</h1><div class=muted>Local control panel · Telegram archive stays on this device</div></header><nav class=tabs><a href='/'>Sources</a><a class=secondary href='/settings'>Settings</a></nav>{body}</body></html>")
    def _local_request(self, require_origin=False):
        expected=f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host", "").lower() != expected:
            self.send("Invalid Host header", 400); return False
        origin=self.headers.get("Origin")
        if require_origin and origin != f"http://{expected}":
            self.send("Missing or invalid Origin header", 403); return False
        if origin and origin != f"http://{expected}":
            self.send("Cross-origin request rejected", 403); return False
        return True
    def do_GET(self):
        if not self._local_request(): return
        path=urlparse(self.path).path
        if path=="/file":
            query=parse_qs(urlparse(self.path).query)
            if query.get("token",[""])[0] != self.token: self.send("Unauthorized",403); return
            rel=query.get("name",[""])[0]
            target=(EXPORTS/rel).resolve()
            if not target.is_file() or EXPORTS.resolve() not in target.parents:
                self.send("File not found",404); return
            data=target.read_bytes(); self.send_response(200); self.send_header("Content-Type","application/json"); self.send_header("Content-Disposition",f'attachment; filename="{target.name}"'); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path=="/settings":
            cfg=load_json(CONFIG,{"selected":[],"time":"03:15"}); env={}
            if ENVFILE.exists():
                for line in ENVFILE.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.startswith("#"):
                        k,v=line.split("=",1); env[k]=v
            schedule=env.get("SCHEDULE","0 3 * * *").split()
            backup_time=f"{int(schedule[1]):02d}:{int(schedule[0]):02d}" if len(schedule)==5 and schedule[0].isdigit() and schedule[1].isdigit() else "03:00"
            body=f"<section class=card><h2>Backup & export schedule</h2><form method=post action=/settings><input type=hidden name=token value='{self.token}'><p>Full archive backup time (24-hour local clock)<br><input type=time name=backup_time value='{backup_time}'><br><small>When Telegram Archive starts its daily full synchronization. Restarting that service triggers an immediate initial backup, so changing this field does not restart it immediately.</small></p><p>Selected-chat delta export time (24-hour local clock)<br><input type=time name=export_time value='{html.escape(cfg.get('time','03:15'))}'><br><small>When Signal Archive exports changes for the saved selection. This is a separate local-database export and does not change Telegram's acquisition settings.</small></p><p class=muted>These are separate jobs. The Telegram archive service owns the full backup/listener; the manager schedules selected-chat delta exports.</p><button class=button>Save settings</button></form></section><section class=card><h2>Telegram API credentials</h2><p>API ID and API Hash are issued by Telegram at <a href='https://my.telegram.org'>my.telegram.org</a> → API development tools. API ID is numeric; API Hash is a secret string. Never share the Hash or login codes.</p><p class=muted>API Hash is stored in the desktop OS keyring (GNOME Keyring or KDE Wallet), never in .env. Existing secrets are never displayed. Leave a field empty to keep its current value. The Telegram session is still a sensitive SQLite file protected by filesystem permissions; enable full-disk encryption for protection at rest.</p><form method=post action=/credentials><input type=hidden name=token value='{self.token}'><p>API ID<br><input type=text name=api_id inputmode=numeric autocomplete=off placeholder='New API ID'><br><small>Numeric application identifier from my.telegram.org → API development tools. This is not your Telegram user ID.</small></p><p>API Hash<br><input type=password name=api_hash autocomplete=new-password placeholder='New API Hash'><br><small>32 hexadecimal characters from the same page. Secret: saved to GNOME Keyring / KDE Wallet and never written to .env. Leave empty to keep the current value.</small></p><p>Phone<br><input type=text name=phone autocomplete=off placeholder='+79991234567'><br><small>Your Telegram account phone in international format, including + and country code. Not a bot token. Leave empty to keep the current value.</small></p><button class=button>Save credentials and stop Telegram service</button></form><p>After changing API ID or Hash, re-authorize using the local Telegram CLI. The one-time Telegram login code and optional Two-Step Verification password are entered only into that terminal prompt, never into this web form. API Hash identifies the app; the login code and 2FA password authorize your account.</p></section><section class=card><h2>Autostart</h2><p>Control whether background services start with your Linux user session. Browser auto-open is managed separately.</p><form method=post action=/autostart><input type=hidden name=token value='{self.token}'><label><input type=checkbox name=enabled value=yes {'checked' if subprocess.run(['systemctl','--user','is-enabled','telegram-archive.service'],capture_output=True,text=True).stdout.strip()=='enabled' else ''}> Start Telegram archive, viewer and manager automatically</label><p><button class=button>Apply autostart setting</button></p></form><form method=post action=/browser-autostart><input type=hidden name=token value='{self.token}'><button class='button secondary'>Toggle browser auto-open on login</button></form><form method=post action=/service-start><input type=hidden name=token value='{self.token}'><button class='button secondary'>Start Telegram services now</button></form></section><section class=card><h2>Data locations</h2><p>Archive: <code>{html.escape(str(DATA))}</code></p><p>Exports: <code>{html.escape(str(EXPORTS))}</code></p></section>"
            body += f"<section class=card><h2>Uninstall Signal Archive</h2><p class=muted>Recommended: remove the app, API Hash and Telegram session while retaining your local message archive and exported files.</p><form method=post action=/uninstall><input type=hidden name=token value='{self.token}'><button class='button secondary' name=mode value=keep>Remove app and credentials, keep archive</button></form><details><summary>Delete everything</summary><p class=error>This permanently deletes the local database, media, session, credentials, preserved copies and exported files. It cannot revoke a Telegram session on Telegram servers.</p><form method=post action=/uninstall><input type=hidden name=token value='{self.token}'><input type=text name=confirm placeholder='Type DELETE ALL DATA'><button class=button name=mode value=purge>Delete all local data</button></form></details></section>"
            self.page(body); return
        try:
            sync_state=run_systemctl("is-active","telegram-archive.service",check=False).stdout.strip() or "unknown"
        except Exception:
            sync_state="unavailable"
        chats,err=chats_from_db(); cfg=load_json(CONFIG,{"selected":[],"time":"03:15"}); chosen=set(map(str,cfg.get("selected",[])))
        items="".join(f"<label class=chat data-type='{html.escape(c['type'].casefold(),quote=True)}' data-updated='{html.escape(c.get('updated',''),quote=True)}' data-search='{html.escape((c['title']+' '+c['type']+' '+c['id']).casefold(),quote=True)}'><input type=checkbox name=chat value='{html.escape(c['id'],quote=True)}' {'checked' if c['id'] in chosen else ''}><span><b>{html.escape(c['title'])}</b><small>{html.escape(c['type'])} · {html.escape(c['id'])}</small></span></label>" for c in chats)
        last=load_json(ROOT/"last-export.json",{}); last_html=(f"<p class={'success' if last.get('status')=='success' else 'error'}>{html.escape(last.get('status','No export yet'))}</p><small>{html.escape(last.get('started_at',''))}</small>" if last else "<p class=muted>No export has run yet.</p>")
        files = sorted(EXPORTS.rglob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:20] if EXPORTS.exists() else []
        file_rows = ''.join(f"<li><a href='/file?name={html.escape(str(p.relative_to(EXPORTS)), quote=True)}&token={self.token}'>{html.escape(p.name)}</a> <small>{p.stat().st_size:,} bytes</small></li>" for p in files)
        body=f"<section class=card><p class=muted>Telegram sync service: <b>{html.escape(sync_state)}</b>. This screen exports from the local archive database and does not send Telegram API requests. If sync stopped after a FloodWait or authorization error, inspect the service log and respect Telegram's cooldown before restarting it.</p><div class=row><div><h2>Choose Telegram chats</h2><div class=muted>{len(chats)} archived sources · {len(chosen)} selected</div></div></div>{('<p class=error>'+html.escape(err)+'</p>') if err else ''}<form method=post action=/save><input type=hidden name=token value='{self.token}'><input id=q class=search placeholder='Search Telegram archive' oninput='filterChats()'><div class=category-tabs><button type=button class=button onclick=tab('recent')>Recent</button><button type=button class='button secondary' onclick=tab('channel')>Channels</button><button type=button class='button secondary' onclick=tab('group')>Groups</button><button type=button class='button secondary' onclick=tab('chat')>Chats/Bots</button></div><details><summary>More filters</summary><p class=muted>These filters apply only to sources already present in the local archive index. Unprocessed Telegram dialogs are not yet discoverable in this build.</p></details><div class=row><button type=button class='button secondary' onclick='selectVisible(true)'>Select visible</button><button type=button class='button secondary' onclick='selectVisible(false)'>Clear visible</button></div><div class=source-list id=sourceList>{items or '<p class=muted>No archived sources found yet.</p>'}</div><p class=muted>Only already archived sources are listed. Telegram's live dialog discovery is not enabled in this build.</p><p><button class=button>Save selection</button></p></form></section><section class=card><h2>Export</h2><p class=muted>Incremental export uses per-chat checkpoints and a 10-second overlap. The recent-period export does not move those checkpoints.</p><form method=post action=/run onsubmit='return showBusy(this)'><input type=hidden name=token value='{self.token}'><button class=button {'disabled' if not chosen else ''}>Export changes since last export</button></form><form method=post action=/recent onsubmit='return showBusy(this)'><input type=hidden name=token value='{self.token}'><p>Export messages whose Telegram message date falls within the exact UTC interval ending now. This does not include older messages merely edited recently. Set either field to zero if not needed; total must be greater than zero.</p><p>Days (0–3650) <input type=number name=days min=0 max=3650 value=2 style='width:80px'> Hours (0–23) <input type=number name=hours min=0 max=23 value=6 style='width:70px'></p><button class='button secondary' {'disabled' if not chosen else ''}>Export recent period</button></form>{last_html}</section><section class=card><h2>Recent exports</h2><p class=muted>Saved to your Downloads folder: {html.escape(str(EXPORTS))}</p><div class=row><form method=post action=/open-folder><input type=hidden name=token value='{self.token}'><button class='button secondary'>Open folder</button></form><button class='button secondary' onclick='navigator.clipboard.writeText({json.dumps(str(EXPORTS))})'>Copy folder path</button></div><ul>{file_rows or '<li class=muted>No exports yet.</li>'}</ul></section><script>let activeTab='recent',shown=40;function matches(x){{let q=document.getElementById('q').value.toLowerCase(),s=x.dataset.search,t=x.dataset.type;return s.includes(q)&&(activeTab==='recent'||(activeTab==='channel'&&t.includes('channel'))||(activeTab==='group'&&t.includes('group'))||(activeTab==='chat'&&(!t.includes('channel')&&!t.includes('group'))))}}function render(){{let rows=[...document.querySelectorAll('.chat')].filter(matches);document.querySelectorAll('.chat').forEach(x=>x.hidden=true);rows.slice(0,shown).forEach(x=>x.hidden=false)}}function tab(t){{activeTab=t;render()}}function filterChats(){{render()}}function showBusy(f){{document.getElementById('busy').hidden=false;f.querySelectorAll('button').forEach(b=>b.disabled=true);return true}}function selectVisible(v){{document.querySelectorAll('.chat').forEach(x=>{{if(!x.hidden)x.querySelector('input').checked=v}})}}document.getElementById('sourceList').addEventListener('scroll',()=>{{let e=document.getElementById('sourceList');if(e.scrollTop+e.clientHeight>=e.scrollHeight-80){{shown+=40;render()}}}});render()</script>"
        self.page(body)
    def do_POST(self):
        if not self._local_request(require_origin=True): return
        path=urlparse(self.path).path; length=int(self.headers.get("Content-Length","0"))
        if length>1_000_000: self.send("Request too large",413); return
        form=parse_qs(self.rfile.read(length).decode("utf-8","replace"))
        if form.get("token",[""])[0]!=self.token: self.send("Invalid form token. Reload the page.",403); return
        try:
            if path=="/uninstall":
                script=ROOT/"uninstall-linux.sh"
                mode=form.get("mode",[""])[0]
                if mode=="keep":
                    proc=subprocess.run(["systemd-run","--user","--unit=signal-archive-uninstall","--collect","--no-block","bash",str(script),"--keep-archives-remove-credentials"],capture_output=True,text=True)
                    if proc.returncode: raise RuntimeError((proc.stderr or "Could not schedule uninstall.").strip())
                    self.page("<section class=card><h2>Uninstall scheduled</h2><p>Application services and local credentials will be removed. The archive database, media and Downloads exports will remain. Review Telegram Settings → Devices to revoke the old authorization remotely.</p></section>")
                    return
                if mode=="purge" and form.get("confirm",[""])[0]=="DELETE ALL DATA":
                    proc=subprocess.run(["systemd-run","--user","--unit=signal-archive-uninstall","--collect","--no-block","bash",str(script),"--purge","--confirm-purge"],capture_output=True,text=True)
                    if proc.returncode: raise RuntimeError((proc.stderr or "Could not schedule full removal.").strip())
                    self.page("<section class=card><h2>Full removal scheduled</h2><p>All local application and archive data will be deleted. Telegram authorization must still be revoked separately in Telegram Settings → Devices.</p></section>")
                    return
                raise ValueError("Choose a removal mode and type DELETE ALL DATA for full deletion.")
            if path=="/save":
                ids=save_selection(form.get("chat",[]))
                self.page("<section class=card><h2>Selection saved</h2><p>Selected chats are now saved for future exports.</p><a href='/'>Back to sources</a></section>"); return
            if path=="/credentials":
                values={}
                api_id=form.get("api_id",[""])[0].strip(); api_hash=form.get("api_hash",[""])[0].strip(); phone=form.get("phone",[""])[0].strip()
                if api_id:
                    if not api_id.isdigit(): raise ValueError("API ID must contain digits only.")
                    values["TELEGRAM_API_ID"]=api_id
                if api_hash:
                    if not re.fullmatch(r"[0-9a-fA-F]{32}",api_hash): raise ValueError("API Hash must be the 32-character hexadecimal value shown by my.telegram.org.")
                if phone:
                    if not re.fullmatch(r"\+[0-9]{7,15}",phone): raise ValueError("Phone must use international format, for example +79991234567.")
                    values["TELEGRAM_PHONE"]=phone
                if not values: raise ValueError("Enter at least one new credential.")
                if api_hash:
                    helper=ROOT/"secret_runner.py"
                    proc=subprocess.run([str(PY),str(helper),"--set-api-hash-stdin"],input=api_hash,text=True,capture_output=True)
                    if proc.returncode: raise RuntimeError((proc.stderr or "Could not store API Hash in the OS keyring.").strip())
                write_env(values); run_systemctl("stop","telegram-archive.service",check=False)
                self.page("<section class=card><h2>Credentials saved</h2><p>The archive service is stopped so it cannot loop with an old authorization. Run the displayed local auth command, complete Telegram code/2FA login, then return and start services.</p><a href='/settings'>Back to settings</a></section>"); return
            if path=="/autostart":
                enabled=form.get("enabled",[""])[0]=="yes"
                names=["telegram-archive.service","telegram-viewer.service","telegram-manager.service"]
                if enabled: run_systemctl("enable",*names)
                else: run_systemctl("disable",*names)
                self.page("<section class=card><h2>Autostart updated</h2><p>Background services will " + ("start automatically with your user session." if enabled else "not start automatically. You can still launch them manually.") + "</p><a href='/settings'>Back to settings</a></section>"); return
            if path=="/browser-autostart":
                autostart=Path(os.environ.get("XDG_CONFIG_HOME",Path.home()/".config"))/"autostart"/"signal-archive.desktop"
                autostart.parent.mkdir(parents=True,exist_ok=True)
                if autostart.exists(): autostart.unlink(); message="Browser auto-open disabled."
                else:
                    autostart.write_text("[Desktop Entry]\nType=Application\nName=Signal Archive\nExec=xdg-open http://127.0.0.1:8001\nX-GNOME-Autostart-enabled=true\n",encoding="utf-8"); message="Browser will open Signal Archive after login."
                self.page(f"<section class=card><h2>Browser startup setting</h2><p>{message}</p><a href='/settings'>Back to settings</a></section>"); return
            if path=="/service-start":
                run_systemctl("start","telegram-archive.service","telegram-viewer.service","telegram-manager.service")
                self.page("<section class=card><h2>Services started</h2><p>Open the workspace to check connection and archive status.</p><a href='/'>Open workspace</a></section>"); return
            if path=="/settings":
                bt=form.get("backup_time",["03:00"])[0]; et=form.get("export_time",["03:15"])[0]
                datetime.strptime(bt,"%H:%M"); datetime.strptime(et,"%H:%M")
                write_env({"SCHEDULE":f"{int(bt[3:]):d} {int(bt[:2]):d} * * *"})
                cfg=load_json(CONFIG,{"selected":[]}); cfg["time"]=et; save_json(CONFIG,cfg)
                install_export_timer(et)
                # Upstream schedule is read at service start; do not restart it here because schedule starts with an immediate full backup.
                self.page("<section class=card><h2>Settings saved</h2><p>Export schedule updated. The full-backup schedule value is saved and will apply on the next planned service restart; the current archive service was not restarted to avoid an immediate duplicate backup.</p><a href='/settings'>Back to settings</a></section>"); return
            if path=="/recent":
                days=int(form.get("days",["0"])[0]); hours=int(form.get("hours",["0"])[0])
                if days<0 or days>3650 or hours<0 or hours>23 or (days==0 and hours==0): raise ValueError("Choose a period greater than zero (up to 10 years).")
                selected=load_json(CONFIG,{}).get("selected",[])
                if not selected: raise RuntimeError("Save a chat selection before exporting.")
                total=days*24+hours
                proc=subprocess.run([str(PY),str(ROOT/"manager.py"),"--recent-hours",str(total)],cwd=ROOT,capture_output=True,text=True)
                if proc.returncode: raise RuntimeError((proc.stderr or proc.stdout or "Recent export failed")[-3000:])
                result=load_json(ROOT/"last-export.json",{})
                self.page(f"<section class=card><h2 class=success>Recent-period export completed</h2><p>{len(result.get('completed',[]))} chats processed. Existing delta checkpoints were not changed.</p><a href='/'>Back to workspace</a></section>"); return
            if path=="/open-folder":
                EXPORTS.mkdir(parents=True,exist_ok=True)
                subprocess.Popen(["xdg-open",str(EXPORTS)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                self.page("<section class=card><h2>Downloads folder opened</h2><a href='/'>Back to workspace</a></section>"); return
            if path=="/run":
                selected=load_json(CONFIG,{}).get("selected",[])
                if not selected: raise RuntimeError("Save a chat selection before exporting.")
                proc=subprocess.run([str(PY),str(ROOT/"manager.py"),"--export"],cwd=ROOT,capture_output=True,text=True)
                if proc.returncode: raise RuntimeError((proc.stderr or proc.stdout or "Export script failed")[-3000:])
                result=load_json(ROOT/"last-export.json",{})
                count=len(result.get("completed",[]))
                self.page(f"<section class=card><h2 class=success>Export completed</h2><p>{count} selected chats processed by the export script.</p><a href='/'>Back to workspace</a></section>"); return
            self.send("Not found",404)
        except Exception as exc:
            self.page(f"<section class=card><h2 class=error>Operation failed</h2><pre>{html.escape(str(exc))}</pre><a href='/'>Back</a></section>")
    def log_message(self,*args): pass


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--export",action="store_true"); parser.add_argument("--recent-hours",type=float); parser.add_argument("--host",default="127.0.0.1"); parser.add_argument("--port",type=int,default=8001); parser.add_argument("--install-timer",action="store_true"); args=parser.parse_args()
    if args.install_timer:
        cfg=load_json(CONFIG,{})
        if cfg.get("selected"): install_export_timer(cfg.get("time","03:15"))
        return
    if args.export or args.recent_hours is not None:
        try: export_job(args.recent_hours)
        except Exception as exc: print(str(exc),file=sys.stderr); raise SystemExit(1)
        return
    token=secrets.token_urlsafe(32); cls=type("BoundHandler",(Handler,),{"token":token})
    if args.host != "127.0.0.1": raise SystemExit("The manager must bind only to 127.0.0.1.")
    server=ThreadingHTTPServer((args.host,args.port),cls)
    print(f"Signal Archive manager: http://{args.host}:{args.port}/")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=="__main__": main()
