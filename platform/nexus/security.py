"""
Nexus City OS — public-edge security hardening (stdlib only).

When the platform is exposed on the open internet (e.g. Render), the
app-logic auth (``nexus.auth``) is necessary but not sufficient. This module
adds the abuse-prevention controls that matter at the network edge:

  * IPRateLimiter — token-bucket per client IP, with a stricter bucket for
    sensitive routes (login). Stops credential-stuffing that rotates
    usernames to dodge the per-user lockout, and blunts scraping / DoS of
    the open GET routes. Returns HTTP 429 with a Retry-After.
  * security_headers() — HSTS, nosniff, frame-deny, a tight CSP for the
    single-file UI, and Referrer-Policy. Applied to every response.
  * client_ip() — trusts Cloudflare's CF-Connecting-IP / X-Forwarded-For
    ONLY when the platform is configured behind a trusted proxy (Cloudflare
    in front of Render), otherwise uses the socket peer (prevents IP
    spoofing of the rate limiter when directly exposed).
  * verify_turnstile() — server-side Cloudflare Turnstile (CAPTCHA)
    verification for the login path on public deployments.

All pure-stdlib, thread-safe, and unit-testable. Designed as defense in
depth BEHIND Cloudflare (which provides DDoS/WAF/edge rate limiting); this
layer ensures the origin is not naked even if traffic bypasses the CDN.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional, Tuple

# --- configuration (env-overridable so a deployment can tune without code) --
# General per-IP allowance across all routes.
RATE_GENERAL_CAPACITY = int(os.environ.get("NEXUS_RATE_GENERAL", "120"))
RATE_GENERAL_WINDOW_S = float(os.environ.get("NEXUS_RATE_WINDOW", "10"))
# Stricter bucket for auth/login (credential-stuffing defence).
RATE_LOGIN_CAPACITY = int(os.environ.get("NEXUS_RATE_LOGIN", "8"))
RATE_LOGIN_WINDOW_S = float(os.environ.get("NEXUS_RATE_LOGIN_WINDOW", "60"))
# Max request body accepted (bytes) — guards against memory-DoS.
MAX_BODY_BYTES = int(os.environ.get("NEXUS_MAX_BODY_BYTES", str(64 * 1024)))
# Trust proxy headers for the client IP (set true when Cloudflare/Render is
# in front; false when the origin is directly internet-exposed).
TRUST_PROXY = os.environ.get("NEXUS_TRUST_PROXY", "1") not in ("0", "false",
                                                               "False", "")
# Cloudflare Turnstile secret (server-side verification); empty disables it.
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET", "")
TURNSTILE_VERIFY_URL = ("https://challenges.cloudflare.com/turnstile/v0/"
                        "siteverify")


def security_headers(csp: bool = True) -> Dict[str, str]:
    """Baseline hardening headers for every response. The UI is a single
    self-contained file plus same-origin XHR/SSE and OSM/Leaflet from CDNs,
    so the CSP allows those while blocking inline-script injection vectors
    elsewhere."""
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        # HSTS is safe because Cloudflare/Render terminate TLS; harmless on
        # plain HTTP (browsers ignore it without https).
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if csp:
        headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data: https:; "
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

    Two independent buckets per IP: a general bucket and a stricter login
    bucket. ``check(ip, login=...)`` returns (allowed, retry_after_s).
    Idle buckets are pruned lazily to bound memory.
    """

    def __init__(self,
                 general_capacity: int = RATE_GENERAL_CAPACITY,
                 general_window_s: float = RATE_GENERAL_WINDOW_S,
                 login_capacity: int = RATE_LOGIN_CAPACITY,
                 login_window_s: float = RATE_LOGIN_WINDOW_S) -> None:
        self.gc = max(1, general_capacity)
        self.gw = max(0.1, general_window_s)
        self.lc = max(1, login_capacity)
        self.lw = max(0.1, login_window_s)
        self._lock = threading.RLock()
        # ip -> {"g": (tokens, last_ts), "l": (tokens, last_ts)}
        self._buckets: Dict[str, Dict[str, Tuple[float, float]]] = {}
        self._last_prune = 0.0

    def _refill(self, tokens: float, last: float, now: float,
                capacity: int, window_s: float) -> Tuple[float, float]:
        rate = capacity / window_s            # tokens per second
        tokens = min(float(capacity), tokens + (now - last) * rate)
        return tokens, now

    def check(self, ip: str, login: bool = False,
              now: Optional[float] = None) -> Tuple[bool, float]:
        now = now if now is not None else time.time()
        cap, win, key = ((self.lc, self.lw, "l") if login
                         else (self.gc, self.gw, "g"))
        with self._lock:
            self._maybe_prune(now)
            bucket = self._buckets.setdefault(
                ip, {"g": (float(self.gc), now), "l": (float(self.lc), now)})
            tokens, last = bucket[key]
            tokens, last = self._refill(tokens, last, now, cap, win)
            if tokens >= 1.0:
                bucket[key] = (tokens - 1.0, last)
                return True, 0.0
            # Not enough tokens: time until one token refills.
            retry = (1.0 - tokens) * (win / cap)
            bucket[key] = (tokens, last)
            return False, round(retry, 2)

    def _maybe_prune(self, now: float) -> None:
        if now - self._last_prune < 60.0:
            return
        self._last_prune = now
        stale = now - max(self.gw, self.lw) * 4
        for ip in list(self._buckets):
            b = self._buckets[ip]
            if b["g"][1] < stale and b["l"][1] < stale:
                self._buckets.pop(ip, None)

    def tracked_ips(self) -> int:
        with self._lock:
            return len(self._buckets)


def verify_turnstile(token: str, remote_ip: str = "",
                     secret: str = TURNSTILE_SECRET,
                     timeout: float = 4.0) -> bool:
    """Server-side Cloudflare Turnstile verification. Returns True when
    Turnstile is disabled (no secret configured) so local/dev runs are
    unaffected; otherwise validates the client token with Cloudflare."""
    if not secret:
        return True                      # disabled → allow (dev/local)
    if not token:
        return False
    data = urllib.parse.urlencode({
        "secret": secret, "response": token,
        **({"remoteip": remote_ip} if remote_ip else {}),
    }).encode("ascii")
    try:
        req = urllib.request.Request(TURNSTILE_VERIFY_URL, data=data,
                                     method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return bool(payload.get("success"))
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        # Fail CLOSED on a verification outage for the login path: a CAPTCHA
        # we can't verify must not be treated as solved.
        return False
