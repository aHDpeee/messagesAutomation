import asyncio
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from signal_archive.browser_login import BrowserLogin
from signal_archive.direct_app import _connect_client, credentials


class FakeServer:
    server_port = 8765
    def __init__(self, address, handler):
        self.handler = handler
    def serve_forever(self): pass
    def shutdown(self): pass
    def server_close(self): pass


class BrowserLoginTests(unittest.TestCase):
    def setUp(self):
        self.server_patch = patch('signal_archive.browser_login.ThreadingHTTPServer', FakeServer)
        self.server_patch.start()
        self.addCleanup(self.server_patch.stop)
        self.open_patch = patch('signal_archive.browser_login.webbrowser.open')
        self.open_patch.start()
        self.addCleanup(self.open_patch.stop)
        self.login = BrowserLogin()
        self.addCleanup(self.login.close)

    def request(self, method, body='', origin='http://127.0.0.1:8765', path=None):
        handler = self.login.server.handler.__new__(self.login.server.handler)
        handler.server = self.login.server
        handler.path = path or '/' + self.login.token
        handler.headers = {'Host': '127.0.0.1:8765', 'Origin': origin, 'Content-Length': str(len(body.encode()))}
        handler.rfile = io.BytesIO(body.encode())
        handler.wfile = io.BytesIO()
        handler.request_version = 'HTTP/1.1'
        handler.requestline = method + ' ' + handler.path + ' HTTP/1.1'
        getattr(handler, 'do_' + method)()
        return handler.wfile.getvalue().decode()

    def test_form_link_csrf_and_secret_post(self):
        result = []
        worker = threading.Thread(target=lambda: result.append(self.login.prompt('API Hash', secret=True)))
        worker.start()
        for _ in range(100):
            if self.login.stage: break
            threading.Event().wait(.001)
        page = self.request('GET')
        self.assertIn('https://my.telegram.org', page)
        self.assertIn('type="password"', page)
        body = urlencode({'stage': self.login.stage, 'value': 'sensitive'})
        self.assertIn('403', self.request('POST', body, origin='https://evil.example'))
        self.assertIn('200', self.request('POST', body))
        worker.join(timeout=1)
        self.assertEqual(result, ['sensitive'])
        self.assertNotIn('sensitive', page)

    def test_cancel_unblocks_prompt(self):
        result = []
        def prompt():
            try: self.login.prompt('Code')
            except RuntimeError as exc: result.append(str(exc))
        worker = threading.Thread(target=prompt)
        worker.start()
        self.assertIn('200', self.request('POST', 'cancel=yes'))
        worker.join(timeout=1)
        self.assertEqual(result, ['Sign-in cancelled.'])

    def test_success_redirects_to_main_application(self):
        self.login.finish('http://127.0.0.1:8765/')
        response = self.request('GET')
        self.assertIn('303 See Other', response)
        self.assertIn('Location: http://127.0.0.1:8765/', response)

    def wait_for(self, condition):
        for _ in range(200):
            if condition():
                return
            threading.Event().wait(.001)
        self.fail('Timed out waiting for the login page to update.')

    def test_fail_shows_error_page_and_reset_choice(self):
        self.addCleanup(self.login.done.set)
        worker = threading.Thread(target=self.login.fail, args=('Boom <bad>',))
        worker.start()
        self.wait_for(lambda: bool(self.login.error))
        page = self.request('GET')
        self.assertIn('Boom &lt;bad&gt;', page)
        self.assertIn('Сбросить вход и войти заново', page)
        self.assertIn('Просто закрыть', page)
        self.assertIn('name="reset" value="yes"', page)
        self.request('POST', 'reset=yes')
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.login.reset_requested)

    def test_fail_close_choice_does_not_request_reset(self):
        worker = threading.Thread(target=self.login.fail, args=('Problem',))
        worker.start()
        self.wait_for(lambda: bool(self.login.error))
        self.request('POST', 'close=yes')
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.login.reset_requested)

    def test_telethon_uses_browser_code_and_password_callbacks(self):
        client = SimpleNamespace(start=AsyncMock(), get_me=AsyncMock(return_value='account'))
        prompts = []
        def prompt(label, secret=False):
            prompts.append((label, secret))
            return 'answer'
        self.assertEqual(asyncio.run(_connect_client(client, '+12345678901', prompt=prompt)), 'account')
        args = client.start.call_args.kwargs
        self.assertEqual(args['code_callback'](), 'answer')
        self.assertEqual(args['password'](), 'answer')
        self.assertEqual(len(prompts), 2)
        self.assertTrue(all(secret for _, secret in prompts))

    def test_credentials_use_browser_without_storing_hash_in_config(self):
        class Keyring:
            value = None
            def get_password(self, *args): return self.value
            def set_password(self, service, account, value): self.value = value
        answers = iter(['123', 'a' * 32, '+12345678901'])
        with tempfile.TemporaryDirectory() as tmp, patch('signal_archive.direct_app._keyring', return_value=Keyring()):
            home = Path(tmp)
            self.assertEqual(credentials(home, prompt=lambda *a, **k: next(answers))[0], 123)
            self.assertNotIn('a' * 32, (home / 'account.json').read_text())
