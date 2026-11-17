"""Temporary local first-run form; secrets are sent only in POST bodies."""
from __future__ import annotations

import html
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


class BrowserLogin:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.condition = threading.Condition()
        self.stage = 0
        self.sequence = 0
        self.label = 'Connecting…'
        self.secret = False
        self.value = None
        self.cancelled = False
        self.opened = False
        self.destination = None
        self.error = ""
        self.reset_requested = False
        self.done = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, body, content_type='text/html; charset=utf-8'):
                data = body.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(data)

            def valid(self):
                return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

            def do_GET(self):
                if not self.valid() or self.path != '/' + owner.token:
                    self.reply(404, 'Not found')
                    return
                with owner.condition:
                    label, stage, secret, destination, error = owner.label, owner.stage, owner.secret, owner.destination, owner.error
                if error:
                    self.reply(200, '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                        '<title>Signal Archive — error</title><style>body{font:16px/1.5 system-ui;background:#0c1520;color:#edf5f8;max-width:620px;margin:8vh auto;padding:24px}'
                        'main{background:#172331;padding:28px;border-radius:16px;border:1px solid #8c2f2f}button{padding:12px 20px;margin:8px 8px 0 0;background:#65d6c0;border:0;border-radius:8px;font-weight:bold}'
                        'button.secondary{background:#263c4d;color:#edf5f8}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
                        '<main><h1>Signal Archive</h1><p>Не удалось запустить приложение. Ничего не удалено без вашего выбора.</p>'
                        '<pre>' + html.escape(error) + '</pre><form method="post"><input type="hidden" name="reset" value="yes">'
                        '<button>Сбросить вход и войти заново</button></form>'
                        '<form method="post"><input type="hidden" name="close" value="yes"><button class=secondary>Просто закрыть</button></form></main>')
                    return
                if destination:
                    self.send_response(303)
                    self.send_header('Location', destination)
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                form = (f'<form method="post"><input type="hidden" name="stage" value="{stage}">'
                        f'<label>{html.escape(label)}<br><input name="value" type="{"password" if secret else "text"}" '
                        'autocomplete="off" required autofocus></label><p><button>Continue</button></p></form>') if stage else '<p>Connecting to Telegram…</p>'
                self.reply(200, '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                    + ('<meta http-equiv="refresh" content="2">' if not stage else '') +
                    '<title>Signal Archive — sign in</title><style>body{font:16px/1.5 system-ui;background:#0c1520;color:#edf5f8;max-width:600px;margin:8vh auto;padding:24px}main{background:#172331;padding:28px;border-radius:16px}input{width:100%;box-sizing:border-box;padding:12px;margin-top:8px}button{padding:12px 24px;background:#65d6c0;border:0;border-radius:8px;font-weight:bold}a{color:#83e4d2}</style>'
                    '<main><h1>Connect Telegram</h1><p>Create an API ID and API hash at '
                    '<a href="https://my.telegram.org" target="_blank" rel="noopener noreferrer">my.telegram.org ↗</a>.</p>'
                    + form + '<form method="post"><input type="hidden" name="cancel" value="yes"><button>Cancel and exit</button></form></main>')

            def do_POST(self):
                origin = f'http://127.0.0.1:{self.server.server_port}'
                if not self.valid() or self.path != '/' + owner.token or self.headers.get('Origin') != origin:
                    self.reply(403, 'Invalid origin')
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 8192:
                        raise ValueError()
                    data = parse_qs(self.rfile.read(size).decode('utf-8'), strict_parsing=True)
                    if data.get('cancel') == ['yes']:
                        with owner.condition:
                            owner.cancelled = True
                            owner.condition.notify_all()
                        self.reply(200, 'Sign-in cancelled. You can close this tab.')
                        return
                    if data.get('reset') == ['yes']:
                        owner.reset_requested = True
                        owner.done.set()
                        self.reply(200, 'Локальный вход будет сброшен. Перезапустите Signal Archive.')
                        return
                    if data.get('close') == ['yes']:
                        owner.done.set()
                        self.reply(200, 'You can close this tab.')
                        return
                    stage = int(data['stage'][0])
                    value = data['value'][0]
                    if not value or len(value) > 4096:
                        raise ValueError()
                except (ValueError, KeyError, UnicodeError):
                    self.reply(400, 'Invalid input')
                    return
                with owner.condition:
                    if stage != owner.stage or owner.value is not None:
                        self.reply(409, 'This step has already been submitted. Return to the sign-in page.')
                        return
                    owner.value = value
                    owner.stage = 0
                    owner.condition.notify()
                # The next step is produced by the application, after it consumes
                # this input. Refresh until the new stage is ready.
                self.reply(200, '<!doctype html><meta charset="utf-8"><title>Signal Archive</title>'
                    '<p>Connecting… Please wait.</p><script>setTimeout(()=>location.replace(location.pathname),1500)</script>')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/{self.token}'

    def open(self):
        webbrowser.open(self.url)

    def prompt(self, label, secret=False):
        if not self.opened:
            self.opened = True
            self.open()
        with self.condition:
            self.value = None
            self.label = label
            self.secret = secret
            self.sequence += 1
            self.stage = self.sequence
            while self.value is None and not self.cancelled:
                self.condition.wait()
            if self.cancelled:
                raise RuntimeError('Sign-in cancelled.')
            value, self.value = self.value, None
            return value

    def close(self):
        if getattr(self, '_closed', False):
            return
        self._closed = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def fail(self, message):
        """Show a local error page and wait for the user's choice."""
        if not self.opened:
            self.open()
        with self.condition:
            self.error = str(message)
            self.destination = None
            self.condition.notify_all()
        self.done.wait()

    def finish(self, destination):
        with self.condition:
            self.destination = destination
