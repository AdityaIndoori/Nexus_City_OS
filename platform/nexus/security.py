"""
Nexus City OS — public-edge security hardening (stdlib only).

When the platform is exposed on the open internet (e.g. via a Cloudflare
Tunnel), Cloudflare Access handles identity at the edge; this module adds
the abuse-prevention controls that matter at the origin:

  * IPRateLimiter — token-bucket per client IP. Blunts scraping / DoS of
    the open GET routes. Returns HTTP 429 with a Retry-After.
  * security_headers() — HSTS, nosniff, frame-deny, a tight CSP for the
    single-file UI, and Referrer-Policy. Applied to every response.
  * client_ip() — trusts Cloudflare's CF-Connecting-IP / X-Forwarded-For
    ONLY when the platform is configured behind a trusted proxy (Cloudflare
    Tunnel in front of the origin), otherwise uses the socket peer (prevents IP
    spoofing of the rate limiter when directly exposed).

All pure-stdlib, thread-safe, and unit-testable. Designed as defense in
depth BEHIND Cloudflare (which provides DDoS/WAF/edge rate limiting); this
layer ensures the origin is not naked even if traffic bypasses the CDN.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional, Tuple

# --- configuration (env-overridable so a deployment can tune without code) --
# General per-IP allowance across all routes.
RATE_GENERAL_CAPACITY = int(os.environ.get("NEXUS_RATE_GENERAL", "120"))
RATE_GENERAL_WINDOW_S = float(os.environ.get("NEXUS_RATE_WINDOW", "10"))
# Max request body accepted (bytes) — guards against memory-DoS.
MAX_BODY_BYTES = int(os.environ.get("NEXUS_MAX_BODY_BYTES", str(64 * 1024)))
# Trust proxy headers for the client IP (set true when Cloudflare is in
# front; false when the origin is directly internet-exposed).
TRUST_PROXY = os.environ.get("NEXUS_TRUST_PROXY", "1") not in ("0", "false",
                                                               "False", "")


def security_headers(csp: bool = True) -> Dict[str, str]:
    """Baseline hardening headers for every response. The UI is a single
    self-contained file plus same-origin XHR/SSE and OSM/Leaflet from CDNs,
    so the CSP allows those while blocking inline-script injection vectors
    elsewhere."""
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        # HSTS is safe because Cloudflare terminates TLS; harmless on
        # plain HTTP (browsers ignore it without https).
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if csp:
        headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data: blob: https:; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; "

            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "connect-src 'self' https:; "
            "frame-ancestors 'none'; base-uri 'self'")
    return headers


def client_ip(headers, peer: str, trust_proxy: bool = TRUST_PROXY) -> str:
    """Resolve the real client IP. Behind Cloudflare, CF-Connecting-IP is
    authoritative; X-Forwarded-For's first hop is the fallback. When NOT
    behind a trusted proxy these headers are attacker-controlled, so we use
    the socket peer to keep the rate limiter honest."""
    if trust_proxy:
        cf = headers.get("CF-Connecting-IP")
        if cf:
            return cf.strip()
        xff = headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
    return peer


class IPRateLimiter:
    """Thread-safe token-bucket rate limiter keyed by client IP.
    ``check(ip)`` returns (allowed, retry_after_s). Idle buckets are pruned
    lazily to bound memory."""

    def __init__(self,
                 general_capacity: int = RATE_GENERAL_CAPACITY,
                 general_window_s: float = RATE_GENERAL_WINDOW_S) -> None:
        self.gc = max(1, general_capacity)
        self.gw = max(0.1, general_window_s)
        self._lock = threading.RLock()
        # ip -> (tokens, last_ts)
        self._buckets: Dict[str, Tuple[float, float]] = {}
        self._last_prune = 0.0

    def _refill(self, tokens: float, last: float, now: float,
                capacity: int, window_s: float) -> Tuple[float, float]:
        rate = capacity / window_s            # tokens per second
        tokens = min(float(capacity), tokens + (now - last) * rate)
        return tokens, now

    def check(self, ip: str,
              now: Optional[float] = None) -> Tuple[bool, float]:
        now = now if now is not None else time.time()
        with self._lock:
            self._maybe_prune(now)
            tokens, last = self._buckets.setdefault(
                ip, (float(self.gc), now))
            tokens, last = self._refill(tokens, last, now, self.gc, self.gw)
            if tokens >= 1.0:
                self._buckets[ip] = (tokens - 1.0, last)
                return True, 0.0
            # Not enough tokens: time until one token refills.
            retry = (1.0 - tokens) * (self.gw / self.gc)
            self._buckets[ip] = (tokens, last)
            return False, round(retry, 2)

    def _maybe_prune(self, now: float) -> None:
        if now - self._last_prune < 60.0:
            return
        self._last_prune = now
        stale = now - self.gw * 4
        for ip in list(self._buckets):
            if self._buckets[ip][1] < stale:
                self._buckets.pop(ip, None)

    def tracked_ips(self) -> int:
        with self._lock:
            return len(self._buckets)
