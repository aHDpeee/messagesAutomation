import io
import json
import types
import threading
import unittest
from unittest.mock import patch

from signal_archive.lan_gateway import LanGateway, _private_ipv4
from signal_archive.lan_pairing import PairingGate
from signal_archive.lan_sessions import DeviceSessions


class GatewayTests(unittest.TestCase):
    def test_only_rfc1918_ipv4_peers_are_allowed(self):
        for host in ("10.0.0.5", "172.16.0.1", "172.31.255.255", "192.168.1.2"):
            self.assertTrue(_private_ipv4(host), host)
        for host in ("8.8.8.8", "127.0.0.1", "172.32.0.1", "169.254.1.1", "::1", "invalid"):
            self.assertFalse(_private_ipv4(host), host)

    def setUp(self):
        self.gateway = LanGateway.__new__(LanGateway)
        self.gateway.origin = "https://archive.example.test:8443"
        self.gateway.host_header = "archive.example.test:8443"
        self.gateway.local_port = 8765
        self.gateway.gate = PairingGate()
        self.gateway.sessions = DeviceSessions({"device": {}})
        self.gateway._rate = {}
        self.gateway._rate_lock = threading.Lock()
        self.handler = self.gateway._handler()

    def request(self, method, path, payload=None, *, origin=None, host=None, cookie=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        h = self.handler.__new__(self.handler)
        h.path, h.request_version, h.requestline = path, "HTTP/1.1", method + " " + path + " HTTP/1.1"
        h.headers = {"Host": host or self.gateway.host_header, "Content-Length": str(len(body))}
        if origin is not None: h.headers["Origin"] = origin
        if cookie is not None: h.headers["Cookie"] = cookie
        h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
        h.client_address = ("192.0.2.1", 12345)
        getattr(h, "do_" + method)()
        return h.wfile.getvalue().decode()

    def test_origin_host_and_auth_required(self):
        self.assertIn("403", self.request("GET", "/", host="evil.test"))
        self.assertIn("303", self.request("GET", "/"))
        self.assertIn("403", self.request("POST", "/lan/pair", {"name": "Phone"}, origin="https://evil.test"))
        self.assertIn("403", self.request("POST", "/export", {}, origin=self.gateway.origin))

    def test_approval_status_requires_request_secret(self):
        reply = self.request("POST", "/lan/pair", {"name": "Phone"}, origin=self.gateway.origin)
        request = json.loads(reply.split("\r\n\r\n", 1)[1])
        self.assertIn('"approved": false', self.request("POST", "/lan/pair-status", request, origin=self.gateway.origin))
        self.gateway.gate.approve(request["id"])
        self.assertIn('"approved": true', self.request("POST", "/lan/pair-status", request, origin=self.gateway.origin))
        request["token"] = "incorrect"
        self.assertIn("400", self.request("POST", "/lan/pair-status", request, origin=self.gateway.origin))

    def test_sensitive_host_only_routes_are_denied_even_for_paired_device(self):
        token = self.gateway.sessions.issue("device")
        cookie = "__Host-signal-session=" + token
        for path in ("/shutdown", "/encryption", "/instance-shutdown/test", "/shortcut", "/shortcut-offer", "/open-exports", "/autostart", "/retention", "/lan-approve"):
            self.assertIn("403", self.request("POST", path, {}, origin=self.gateway.origin, cookie=cookie))

    def test_catalog_routes_require_paired_device_and_exact_origin(self):
        from unittest.mock import MagicMock
        token = self.gateway.sessions.issue("device")
        cookie = "__Host-signal-session=" + token
        routes = ("/catalog-preview", "/catalog-edit", "/catalog-upload", "/catalog-reload", "/catalog-restore")
        for path in routes:
            self.assertIn("403", self.request("POST", path, {}, origin=self.gateway.origin))
            self.assertIn("403", self.request("POST", path, {}, origin="https://evil.test", cookie=cookie))
            response = MagicMock(status=200)
            response.read.return_value = b'{}'
            response.getheader.return_value = None
            connection = MagicMock()
            connection.getresponse.return_value = response
            with patch("signal_archive.lan_gateway.http.client.HTTPConnection", return_value=connection):
                self.assertIn("200", self.request("POST", path, {"token": "csrf"}, origin=self.gateway.origin, cookie=cookie))
            args, kwargs = connection.request.call_args
            self.assertEqual(args[:2], ("POST", path))
            self.assertEqual(kwargs["headers"]["Origin"], "http://127.0.0.1:8765")
            self.assertIn(b'"token": "csrf"', kwargs["body"])

    def test_public_loopback_and_wildcard_bind_are_rejected_before_socket(self):
        from pathlib import Path
        for host in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "169.254.1.2", "example.com"):
            with self.assertRaises(ValueError):
                LanGateway("https://archive.test:8443", Path("cert"), Path("key"), Path("."), 8765, host=host)
