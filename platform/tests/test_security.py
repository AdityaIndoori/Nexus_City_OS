"""Tests for the public-edge security layer (nexus.security).

Network-free: rate limiting, client-IP resolution, and security headers.
"""
import unittest

from nexus.security import (
    IPRateLimiter,
    client_ip,
    security_headers,
)


class TestRateLimiter(unittest.TestCase):
    def test_general_bucket_allows_then_blocks(self):
        rl = IPRateLimiter(general_capacity=3, general_window_s=10)
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

    def test_refill_over_time(self):
        rl = IPRateLimiter(general_capacity=2, general_window_s=10)
        self.assertTrue(rl.check("ip", now=0.0)[0])
        self.assertTrue(rl.check("ip", now=0.0)[0])
        self.assertFalse(rl.check("ip", now=0.0)[0])
        # After a full window, tokens have refilled.
        self.assertTrue(rl.check("ip", now=10.0)[0])


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


if __name__ == "__main__":
    unittest.main()
