"""Tests for the public-edge security layer (nexus.security).

Network-free: the only outbound call (Turnstile) is exercised via the
disabled-secret path and an injected secret with a monkeypatched verifier.
"""
import unittest

from nexus import security
from nexus.security import (
    IPRateLimiter,
    client_ip,
    security_headers,
    verify_turnstile,
)


class TestRateLimiter(unittest.TestCase):
    def test_general_bucket_allows_then_blocks(self):
        rl = IPRateLimiter(general_capacity=3, general_window_s=10,
                           login_capacity=2, login_window_s=60)
        t = 1000.0
        for _ in range(3):
            ok, _retry = rl.check("1.2.3.4", now=t)
            self.assertTrue(ok)
        ok, retry = rl.check("1.2.3.4", now=t)
        self.assertFalse(ok)
        self.assertGreater(retry, 0.0)

    def test_buckets_are_per_ip(self):
        rl = IPRateLimiter(general_capacity=1, general_window_s=10)
        self.assertTrue(rl.check("a", now=5.0)[0])
        # Different IP has its own bucket.
        self.assertTrue(rl.check("b", now=5.0)[0])
        # First IP is now empty.
        self.assertFalse(rl.check("a", now=5.0)[0])

    def test_login_bucket_independent_of_general(self):
        rl = IPRateLimiter(general_capacity=10, general_window_s=10,
                           login_capacity=2, login_window_s=60)
        t = 0.0
        self.assertTrue(rl.check("ip", login=True, now=t)[0])
        self.assertTrue(rl.check("ip", login=True, now=t)[0])
        # Login bucket exhausted...
        self.assertFalse(rl.check("ip", login=True, now=t)[0])
        # ...but the general bucket still has room.
        self.assertTrue(rl.check("ip", login=False, now=t)[0])

    def test_refill_over_time(self):
        rl = IPRateLimiter(general_capacity=2, general_window_s=10)
        self.assertTrue(rl.check("ip", now=0.0)[0])
        self.assertTrue(rl.check("ip", now=0.0)[0])
        self.assertFalse(rl.check("ip", now=0.0)[0])
        # After a full window, tokens have refilled.
        self.assertTrue(rl.check("ip", now=10.0)[0])

    def test_credential_stuffing_across_usernames_is_blocked(self):
        # The login bucket is keyed by IP, so rotating usernames from one
        # IP cannot exceed the login allowance (defeats per-user-lockout
        # evasion).
        rl = IPRateLimiter(login_capacity=3, login_window_s=60)
        attempts = sum(1 for i in range(20)
                       if rl.check("attacker", login=True, now=0.0)[0])
        self.assertEqual(attempts, 3)


class TestClientIP(unittest.TestCase):
    def test_trusts_cf_header_when_proxied(self):
        headers = {"CF-Connecting-IP": "9.9.9.9",
                   "X-Forwarded-For": "8.8.8.8, 1.1.1.1"}
        self.assertEqual(client_ip(headers, "10.0.0.1", trust_proxy=True),
                         "9.9.9.9")

    def test_xff_first_hop_fallback(self):
        headers = {"X-Forwarded-For": "8.8.8.8, 1.1.1.1"}
        self.assertEqual(client_ip(headers, "10.0.0.1", trust_proxy=True),
                         "8.8.8.8")

    def test_ignores_proxy_headers_when_not_trusted(self):
        # Direct exposure: spoofable headers must NOT override the socket.
        headers = {"CF-Connecting-IP": "9.9.9.9"}
        self.assertEqual(client_ip(headers, "10.0.0.1", trust_proxy=False),
                         "10.0.0.1")


class TestSecurityHeaders(unittest.TestCase):
    def test_core_headers_present(self):
        h = security_headers()
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertIn("Strict-Transport-Security", h)
        self.assertIn("Content-Security-Policy", h)
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])


class TestTurnstile(unittest.TestCase):
    def test_disabled_when_no_secret_allows(self):
        # No secret configured → verification is a no-op (dev/local).
        self.assertTrue(verify_turnstile("anything", secret=""))

    def test_enabled_requires_token(self):
        # Secret set but empty client token → reject without a network call.
        self.assertFalse(verify_turnstile("", secret="sekret"))

    def test_enabled_verifies_via_cloudflare(self):
        calls = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"success": true}'

        def _fake_urlopen(req, timeout=4.0):
            calls["url"] = req.full_url
            return _Resp()

        orig = security.urllib.request.urlopen
        security.urllib.request.urlopen = _fake_urlopen
        try:
            self.assertTrue(verify_turnstile("tok", secret="sekret"))
            self.assertIn("turnstile", calls["url"])
        finally:
            security.urllib.request.urlopen = orig

    def test_fails_closed_on_verification_outage(self):
        def _boom(req, timeout=4.0):
            raise OSError("network down")

        orig = security.urllib.request.urlopen
        security.urllib.request.urlopen = _boom
        try:
            self.assertFalse(verify_turnstile("tok", secret="sekret"))
        finally:
            security.urllib.request.urlopen = orig


if __name__ == "__main__":
    unittest.main()
