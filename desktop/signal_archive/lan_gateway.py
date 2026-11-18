"""Optional HTTPS gateway, separate from the loopback application listener.

An owner must supply a trusted certificate and approve each pairing locally.
The host app must start/stop this gateway and expose owner-only approval controls.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .lan_device_store import DeviceStore
from .lan_pairing import PairingGate
from .lan_sessions import DeviceSessions
from .lan_tls import server_context
from .lan_webauthn import PasskeyCeremonies


LOGIN_PAGE = r'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Signal Archive access</title>
<h1>Signal Archive</h1><p>Pair this device with approval on the computer, or sign in with its passkey.</p>
<label>Device name <input id="name" maxlength="80" value="Phone"></label><p><button id="pair">Request pairing</button> <button id="login">Sign in</button></p><p id="status" role="status"></p>
<script>
const statusEl=document.getElementById('status');
async function call(path, data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});let v=await r.json();if(!r.ok)throw Error(v.error||'Request failed');return v;}
function supported(){if(!window.PublicKeyCredential||!PublicKeyCredential.parseCreationOptionsFromJSON||!PublicKeyCredential.parseRequestOptionsFromJSON)throw Error('This browser does not support passkeys on this HTTPS site.');}
document.getElementById('pair').onclick=async()=>{try{supported();const p=await call('/lan/pair',{name:document.getElementById('name').value});statusEl.textContent='Verify code '+p.code+' on the computer, then approve.';let approved=false;for(let i=0;i<60;i++){await new Promise(r=>setTimeout(r,2000));const q=await call('/lan/pair-status',p);if(q.approved){approved=true;break;}}if(!approved)throw Error('Pairing timed out.');const v=await call('/lan/register-options',p);const c=await navigator.credentials.create({publicKey:PublicKeyCredential.parseCreationOptionsFromJSON(v.options)});await call('/lan/register-finish',{id:v.id,credential:c.toJSON()});statusEl.textContent='Paired. Sign in with your device passkey.';}catch(e){statusEl.textContent=e.message;}};
document.getElementById('login').onclick=async()=>{try{supported();const v=await call('/lan/auth-options',{});const c=await navigator.credentials.get({publicKey:PublicKeyCredential.parseRequestOptionsFromJSON(v.options)});await call('/lan/auth-finish',{id:v.id,credential:c.toJSON()});location.replace('/');}catch(e){statusEl.textContent=e.message;}};
</script>'''


_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(network) for network in
                          ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _private_ipv4(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4 and any(address in network for network in _PRIVATE_NETWORKS)


class LimitedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # A private listening address alone does not prevent a router from
        # forwarding public traffic to it. Reject non-LAN peers before TLS.
        if not _private_ipv4(client_address[0]):
            self.shutdown_request(request)
            return
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class LanGateway:
    def __init__(self, origin, certificate, private_key, home, local_port, *, host):
        if not _private_ipv4(host):
            raise ValueError("Choose a specific private IPv4 address for LAN access.")
        url = urlsplit(origin)
        if not url.port:
            raise ValueError("Include the explicit HTTPS port in the LAN origin.")
        self.origin = origin
        self.local_port = int(local_port)
        self.gate = PairingGate()
        self.store = DeviceStore(home / "lan-devices.json")
        self.ceremonies = PasskeyCeremonies(origin, self.store.records, self.store.save, self.gate)
        self.sessions = DeviceSessions(self.store.records)
        self._rate_lock = threading.Lock()
        self._rate = {}
        self.host_header = url.netloc
        context = server_context(certificate, private_key)
        self.server = LimitedHTTPServer((host, url.port), self._handler())
        try:
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True, do_handshake_on_connect=False)
        except BaseException:
            self.server.server_close()
            raise
        self.thread = threading.Thread(target=self.server.serve_forever, name="lan-https", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def _handler(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(10)
                self.request.do_handshake()
                super().setup()

            def log_message(self, *_):
                pass

            def reply(self, status, body, *, content_type="application/json; charset=utf-8", cookie=None):
                data = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(data)

            def valid_host(self):
                return self.headers.get("Host") == owner.host_header

            def authenticated(self):
                try:
                    cookie = SimpleCookie(self.headers.get("Cookie", ""))
                    token = cookie.get("__Host-signal-session")
                    return bool(token and owner.sessions.valid(token.value))
                except Exception:
                    return False

            def do_GET(self):
                if not self.valid_host():
                    self.reply(403, '{"error":"Invalid host"}'); return
                if self.path == "/lan/login":
                    self.reply(200, LOGIN_PAGE, content_type="text/html; charset=utf-8"); return
                if not self.authenticated():
                    self.send_response(303); self.send_header("Location", "/lan/login"); self.send_header("Content-Length", "0"); self.end_headers(); return
                self.proxy("GET")

            def do_POST(self):
                if not self.valid_host() or self.headers.get("Origin") != owner.origin:
                    self.reply(403, '{"error":"Invalid origin"}'); return
                if self.path.startswith("/lan/"):
                    self.auth_action(); return
                if not self.authenticated():
                    self.reply(403, '{"error":"Sign in required"}'); return
                self.proxy("POST")

            def body(self):
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8 * 1024 * 1024:
                    raise ValueError("Invalid request size.")
                return self.rfile.read(size)

            def auth_action(self):
                try:
                    now = time.monotonic()
                    address = self.client_address[0]
                    with owner._rate_lock:
                        owner._rate = {key: val for key, val in owner._rate.items() if val[1] > now}
                        count, deadline = owner._rate.get(address, (0, now + 60))
                        if count >= 60 or (address not in owner._rate and len(owner._rate) >= 1024):
                            self.reply(429, '{"error":"Too many requests; try later."}'); return
                        owner._rate[address] = (count + 1, deadline)
                    data = json.loads(self.body())
                    if not isinstance(data, dict):
                        raise ValueError("Invalid request.")
                    cookie = None
                    if self.path == "/lan/pair":
                        ident, token = owner.gate.request(data.get("name", ""))
                        result = {"id": ident, "token": token, "code": owner.gate.code(ident, token)}
                    elif self.path == "/lan/pair-status":
                        result = {"approved": owner.gate.approved(data["id"], data["token"])}
                    elif self.path == "/lan/register-options":
                        ident, options = owner.ceremonies.begin_registration(data["id"], data["token"])
                        result = {"id": ident, "options": json.loads(options)}
                    elif self.path == "/lan/register-finish":
                        owner.ceremonies.finish_registration(data["id"], data["credential"])
                        result = {"ok": True}
                    elif self.path == "/lan/auth-options":
                        ident, options = owner.ceremonies.begin_authentication()
                        result = {"id": ident, "options": json.loads(options)}
                    elif self.path == "/lan/auth-finish":
                        ident = owner.ceremonies.finish_authentication(data["id"], data["credential"])
                        cookie = owner.sessions.cookie(owner.sessions.issue(ident))
                        result = {"ok": True}
                    else:
                        self.reply(404, '{"error":"Not found"}'); return
                    self.reply(200, json.dumps(result), cookie=cookie)
                except Exception:
                    self.reply(400, '{"error":"Request failed or expired. Check approval and try again."}')

            def proxy(self, method):
                if self.path not in ({"/", "/status", "/codex-guide", "/training"} if method == "GET" else {"/refresh", "/add-chat", "/export", "/interval", "/cancel", "/members", "/single-export", "/select", "/block-create", "/block-rename", "/block-delete", "/block-assign", "/group-membership", "/catalog-preview", "/catalog-edit", "/catalog-upload", "/catalog-reload", "/catalog-restore", "/telegram-folders-refresh", "/telegram-folder-preview", "/telegram-folder-import"}):
                    self.reply(403, '{"error":"Available only on the host computer"}'); return
                try:
                    body = self.body() if method == "POST" else None
                    conn = http.client.HTTPConnection("127.0.0.1", owner.local_port, timeout=15)
                    headers = {"Host": f"127.0.0.1:{owner.local_port}", "Origin": f"http://127.0.0.1:{owner.local_port}", "X-Signal-Remote": "1"}
                    if body is not None:
                        headers["Content-Type"] = self.headers.get("Content-Type", "application/x-www-form-urlencoded")
                    conn.request(method, self.path, body=body, headers=headers)
                    response = conn.getresponse()
                    content = response.read(16 * 1024 * 1024 + 1)
                    if len(content) > 16 * 1024 * 1024:
                        raise ValueError("Response too large")
                    self.send_response(response.status)
                    for name in ("Content-Type", "Location", "Cache-Control", "Content-Security-Policy", "X-Content-Type-Options"):
                        if response.getheader(name): self.send_header(name, response.getheader(name))
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers(); self.wfile.write(content)
                    conn.close()
                except Exception:
                    self.reply(502, '{"error":"Local application unavailable"}')
        return Handler
