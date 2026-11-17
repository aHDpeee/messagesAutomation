from __future__ import annotations
import html
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from .core import Archive

CSS = """
:root{color-scheme:dark;font:15px system-ui;background:#111315;color:#e8e9eb}
body{max-width:1050px;margin:32px auto;padding:0 18px}
h1{font-size:30px;margin-bottom:4px}.muted{color:#92979f}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:14px}
.card{background:#1b1e22;border:1px solid #30343a;border-radius:14px;padding:18px;margin:14px 0}
input,select,button{font:inherit;padding:10px;border-radius:8px;border:1px solid #414750;background:#121518;color:#eee}
input[type=text]{width:min(100%,420px)}button{background:#ff5500;border-color:#ff5500;color:white;cursor:pointer}
a{color:#ff8752}.tag{color:#ff9b70}.source{padding:10px 0;border-bottom:1px solid #30343a}
small{color:#9ba1aa}.stat{font-size:25px;font-weight:650}
pre{white-space:pre-wrap;overflow-wrap:anywhere}.notice{border-left:3px solid #ff5500;padding:10px 14px;background:#25211f}
"""
class Handler(BaseHTTPRequestHandler):
    archive: Archive
    token: str

    def _send(self, body: str, status: int = 200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _page(self, content: str):
        self._send(f"""<!doctype html><html><head><meta charset=utf-8>
        <meta name=viewport content='width=device-width,initial-scale=1'>
        <title>Signal Archive 0.2</title><style>{CSS}</style></head><body>
        <header><div class=tag>SIGNAL ARCHIVE / 0.2</div><h1>Research workspace</h1>
        <div class=muted>Local-first archive · no external AI requests are made</div></header>
        {content}</body></html>""")

    def do_GET(self):
        path = urlparse(self.path).path
        sources = self.archive.list_sources()
        total = sum(s["message_count"] for s in sources)
        collections = self.archive.list_collections()
        collection_html = "".join(
            f"<div class=source><b>{html.escape(c['title'])}</b> "
            f"<small>({html.escape(c['collection_id'])}) · {c['source_count']} sources</small></div>"
            for c in collections
        ) or "<p class=muted>No collections yet.</p>"
        src_html = "".join(
            f"<div class=source><b>{html.escape(s['title'])}</b> "
            f"<small>({html.escape(s['source_id'])}) · {s['message_count']} messages</small></div>"
            for s in sources
        ) or "<p class=muted>No sources yet. Add a source and import a JSON/JSONL file.</p>"
        source_options = "".join(
            f"<option value='{html.escape(s['source_id'])}'>{html.escape(s['title'])}</option>"
            for s in sources
        )
        collection_options = "".join(
            f"<option value='{html.escape(c['collection_id'])}'>{html.escape(c['title'])}</option>"
            for c in collections
        )
        content = f"""
        <div class=grid>
          <section class=card><div class=muted>Sources</div><div class=stat>{len(sources)}</div></section>
          <section class=card><div class=muted>Archived messages</div><div class=stat>{total}</div></section>
        </div>
        <section class=card><h2>1. Sources</h2>
        <form method=post action=/source>
          <input type=hidden name=token value='{self.token}'>
          <p><input name=source_id placeholder='source-id' required>
          <input name=title placeholder='Display name' required></p>
          <p><select name=source_type><option value=telegram>Telegram export</option><option value=rss>Licensed RSS source</option><option value=other>Other permitted source</option></select>
          <button>Add source</button></p>
        </form>{src_html}</section>
        <section class=card><h2>2. Collections</h2>
        <form method=post action=/collection>
          <input type=hidden name=token value='{self.token}'>
          <p><input name=collection_id placeholder='collection-id' required>
          <input name=title placeholder='Collection title' required>
          <button>Create collection</button></p>
        </form>
        <form method=post action=/assign>
          <input type=hidden name=token value='{self.token}'>
          <p><select name=source_id required>{source_options}</select>
          <select name=collection_id required>{collection_options}</select>
          <button>Assign source</button></p>
        </form>{collection_html}
        </section>
        <section class=card><h2>3. Import local data</h2>
        <p class=muted>Import a JSON array or JSONL file exported by a source you are authorized to archive.
        Import is local; no network request is made.</p>
        <form method=post action=/import>
          <input type=hidden name=token value='{self.token}'>
          <p><input name=path type=text placeholder='Absolute path to .json or .jsonl' required>
          <input name=fallback_source placeholder='source-id' required></p>
          <button>Import file</button>
        </form></section>
        <section class=card><h2>4. Search</h2>
        <form method=get action=/search><input name=q placeholder='Search indexed messages' required><button>Search</button></form>
        </section>
        <section class=card><h2>5. Export profile</h2>
        <form method=post action=/export>
          <input type=hidden name=token value='{self.token}'>
          <p><select name=profile><option>daily-news</option><option>research</option><option>technical</option><option>community</option><option>full-archive</option></select>
          <input name=since placeholder='Since ISO date (optional)'>
          <input name=source_ids placeholder='Source IDs, comma-separated (optional)'></p>
          <p><input name=output_dir placeholder='Output folder (e.g. exports/today)' required></p>
          <button>Generate export</button>
        </form></section>
        <p class=notice>External AI processing is intentionally not enabled in v0.2. Telegram-derived content must not be sent to an AI provider unless the applicable licensing and consent requirements have been satisfied.</p>
        """
        if path == "/search":
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            try:
                results = self.archive.search(q)
                content += "<section class=card><h2>Search results</h2>"
                for r in results:
                    content += (f"<article class=source><b>{html.escape(r['source_id'])}</b> · "
                                f"{html.escape(r['date'] or '')}<p>{html.escape(r['text'][:1200])}</p></article>")
                content += f"<p class=muted>{len(results)} results shown</p></section>"
            except Exception as e:
                content += f"<section class=card>Search error: {html.escape(str(e))}</section>"
        self._page(content)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            self._send("Request too large", 413); return
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        if form.get("token", [""])[0] != self.token:
            self._send("Invalid form token. Reload the page.", 403); return
        origin = self.headers.get("Origin")
        if origin:
            from urllib.parse import urlparse as _urlparse
            parsed_origin = _urlparse(origin)
            if parsed_origin.hostname not in ("127.0.0.1", "localhost", "::1") or parsed_origin.port != self.server.server_port:
                self._send("Cross-origin request rejected", 403); return
        try:
            if parsed.path == "/source":
                self.archive.add_source(form["source_id"][0], form["title"][0],
                                        form.get("source_type", ["other"])[0])
                msg = "Source saved."
            elif parsed.path == "/collection":
                self.archive.add_collection(form["collection_id"][0], form["title"][0])
                msg = "Collection saved."
            elif parsed.path == "/assign":
                self.archive.assign_source(form["source_id"][0], form["collection_id"][0])
                msg = "Source assigned to collection."
            elif parsed.path == "/import":
                result = self.archive.import_file(Path(form["path"][0]), form["fallback_source"][0])
                msg = f"Import finished: {result}"
            elif parsed.path == "/export":
                ids = [x.strip() for x in form.get("source_ids", [""])[0].split(",") if x.strip()]
                out = Path(form["output_dir"][0])
                if not out.is_absolute():
                    out = self.archive.home / out
                manifest = self.archive.export(out, ids or None,
                    form.get("since", [""])[0] or None, form.get("profile", ["daily-news"])[0])
                msg = f"Export complete: {manifest['counts']}"
            else:
                self._send("Not found", 404); return
            self._page(f"<section class=card><h2>{html.escape(msg)}</h2><p><a href='/'>Back to workspace</a></p></section>")
        except Exception as e:
            self._page(f"<section class=card><h2>Operation failed</h2><pre>{html.escape(str(e))}</pre><p><a href='/'>Back</a></p></section>")

    def log_message(self, *_):
        pass

def serve(archive: Archive, host: str = "127.0.0.1", port: int = 8765):
    token = secrets.token_urlsafe(32)
    cls = type("BoundHandler", (Handler,), {"archive": archive, "token": token})
    server = ThreadingHTTPServer((host, port), cls)
    print(f"Signal Archive 0.2: http://{host}:{port}/")
    print("Local-only server. Keep this terminal open; press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\\nStopping.")
    finally:
        server.server_close()
