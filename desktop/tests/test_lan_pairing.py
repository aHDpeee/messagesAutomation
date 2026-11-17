import unittest
from signal_archive.lan_pairing import PairingGate


class PairingGateTests(unittest.TestCase):
    def test_approval_and_single_use_are_required(self):
        gate = PairingGate()
        ident, token = gate.request("My phone")
        self.assertEqual(gate.pending()[0]["device_name"], "My phone")
        with self.assertRaises(ValueError):
            gate.consume(ident, token)
        gate.approve(ident)
        with self.assertRaises(ValueError):
            gate.consume(ident, "wrong")
        self.assertEqual(gate.consume(ident, token), "My phone")
        with self.assertRaises(ValueError):
            gate.consume(ident, token)

    def test_expiry_and_limits(self):
        now = [0]
        gate = PairingGate(ttl_seconds=10, max_pending=1, clock=lambda: now[0])
        ident, token = gate.request("Phone")
        with self.assertRaises(RuntimeError):
            gate.request("Other")
        gate.approve(ident)
        now[0] = 10
        with self.assertRaises(ValueError):
            gate.consume(ident, token)
        self.assertEqual(gate.pending(), [])
        gate.request("Other")

    def test_denied_request_cannot_be_consumed(self):
        gate = PairingGate()
        ident, token = gate.request("Phone")
        gate.approve(ident)
        gate.deny(ident)
        with self.assertRaises(ValueError):
            gate.consume(ident, token)
