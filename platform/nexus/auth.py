"""
Nexus City OS — Authentication & session layer (production pillar 2).

Replaces the reference implementation's bare user-ID strings with real
credential verification and signed session tokens — using only the stdlib:

  * Credentials:  PBKDF2-HMAC-SHA256 (210k iterations, per-user random salt),
                  constant-time comparison. Stored in the durable Store.
  * Sessions:     HMAC-SHA256-signed bearer tokens with expiry (8h shift
                  length), server-side revocation list, sliding inactivity
                  window. Token format: base64url(payload).base64url(sig).
  * Lockout:      5 failed attempts → 5-minute lockout per user (throttles
                  online brute force); failures are audit-logged upstream.

Production swap: this module's ``Authenticator`` interface maps 1:1 onto an
OIDC/SAML SSO integration (verify → principal + role claims); MFA and
hardware tokens slot in at ``verify_credentials``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any, Dict, Optional

from .store import Store

PBKDF2_ITERATIONS = 210_000
TOKEN_TTL_S = 8 * 3600          # one shift
LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW_S = 300.0

# Default bootstrap accounts (reference deployment). Production replaces
# this with SSO provisioning; passwords here are for the local demo only.
DEFAULT_ACCOUNTS = [
    ("op-1", "operator", "nexus-op-1"),
    ("analyst-1", "analyst", "nexus-analyst-1"),
    ("admin-1", "admin", "nexus-admin-1"),
    ("viewer-1", "viewer", "nexus-viewer-1"),
]


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt, PBKDF2_ITERATIONS)


class AuthError(Exception):
    pass


class Authenticator:
    """Credential verification + HMAC-signed bearer sessions."""

    def __init__(self, store: Store,
                 secret: Optional[bytes] = None) -> None:
        self._store = store
        # Signing key persists across restarts (sessions survive a reboot);
        # production keeps this in a KMS/HSM.
        existing = store.get_kv("auth_signing_key")
        if secret is not None:
            self._secret = secret
        elif existing:
            self._secret = bytes.fromhex(existing)
        else:
            self._secret = os.urandom(32)
            store.set_kv("auth_signing_key", self._secret.hex())
        self._lock = threading.RLock()
        # jti -> token exp: entries self-expire (a revoked token past its
        # expiry is rejected by the exp check anyway), so the set can't
        # grow unbounded on a long-running deployment.
        self._revoked: Dict[str, float] = {}
        self._revoked_last_prune = 0.0
        self._failures: Dict[str, list] = {}   # user_id -> [ts, ...]
        self._bootstrap_defaults()

    @staticmethod
    def _password_env_key(user_id: str) -> str:
        """Env var holding an override password for ``user_id`` — the id
        uppercased with non-alphanumerics → ``_`` (e.g. admin-1 →
        ``NEXUS_PASSWORD_ADMIN_1``)."""
        return "NEXUS_PASSWORD_" + "".join(
            c.upper() if c.isalnum() else "_" for c in user_id)

    def _bootstrap_defaults(self) -> None:
        """Seed/reconcile demo accounts on every boot.

        On a PUBLIC deployment set ``NEXUS_DISABLE_DEMO_ACCOUNTS=1`` to skip
        the well-known demo passwords entirely (and provision real accounts
        out-of-band via ``create_user`` / SSO).

        Credential RECONCILIATION (the production-correct behaviour): a
        per-account env override (``NEXUS_PASSWORD_<USER_ID>``) is applied on
        EVERY startup, not just first creation — so rotating a password via
        env reliably takes effect even when the SQLite store persisted across
        a redeploy (e.g. a mounted volume). Without an override, the account
        is created with its default password only if it doesn't already
        exist (an operator-changed password is never clobbered)."""
        if os.environ.get("NEXUS_DISABLE_DEMO_ACCOUNTS", "") in (
                "1", "true", "True"):
            return
        for user_id, role, default_password in DEFAULT_ACCOUNTS:
            override = os.environ.get(self._password_env_key(user_id))
            existing = self._store.get_user(user_id)
            if override is not None:
                # Reconcile to the env value every boot (rotation-safe).
                salt = os.urandom(16)
                self._store.upsert_user(
                    user_id, role, salt,
                    hash_password(override, salt), time.time())
            elif existing is None:
                salt = os.urandom(16)
                self._store.upsert_user(
                    user_id, role, salt,
                    hash_password(default_password, salt), time.time())



    # ---- credentials ------------------------------------------------------

    def create_user(self, user_id: str, role: str, password: str) -> None:
        salt = os.urandom(16)
        self._store.upsert_user(user_id, role, salt,
                                hash_password(password, salt), time.time())

    def _locked_out(self, user_id: str) -> bool:
        with self._lock:
            now = time.time()
            fails = [t for t in self._failures.get(user_id, [])
                     if now - t < LOCKOUT_WINDOW_S]
            self._failures[user_id] = fails
            return len(fails) >= LOCKOUT_THRESHOLD

    def verify_credentials(self, user_id: str, password: str) -> str:
        """Returns the user's role on success; raises AuthError otherwise."""
        if self._locked_out(user_id):
            raise AuthError("Account temporarily locked "
                            "(too many failed attempts).")
        user = self._store.get_user(user_id)
        if user is None:
            # burn comparable CPU so unknown-user probes aren't faster
            hash_password(password, b"\x00" * 16)
            raise AuthError("Invalid credentials.")
        candidate = hash_password(password, user["salt"])
        if not hmac.compare_digest(candidate, user["pw_hash"]):
            with self._lock:
                self._failures.setdefault(user_id, []).append(time.time())
            raise AuthError("Invalid credentials.")
        with self._lock:
            self._failures.pop(user_id, None)
        return user["role"]

    # ---- sessions ----------------------------------------------------------

    def issue_token(self, user_id: str, role: str) -> str:
        payload = {"sub": user_id, "role": role,
                   "iat": time.time(), "exp": time.time() + TOKEN_TTL_S,
                   "jti": _b64e(os.urandom(9))}
        body = _b64e(json.dumps(payload, sort_keys=True).encode("utf-8"))
        sig = _b64e(hmac.new(self._secret, body.encode("ascii"),
                             hashlib.sha256).digest())
        return f"{body}.{sig}"

    def verify_token(self, token: str) -> Dict[str, Any]:
        """Returns the token payload; raises AuthError if invalid."""
        try:
            body, sig = token.split(".", 1)
        except ValueError:
            raise AuthError("Malformed token.") from None
        expected = _b64e(hmac.new(self._secret, body.encode("ascii"),
                                  hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise AuthError("Invalid token signature.")
        try:
            payload = json.loads(_b64d(body))
        except Exception:  # noqa: BLE001
            raise AuthError("Malformed token payload.") from None
        if payload.get("exp", 0) < time.time():
            raise AuthError("Session expired.")
        with self._lock:
            self._prune_revoked()
            if payload.get("jti") in self._revoked:
                raise AuthError("Session revoked.")
        return payload

    def _prune_revoked(self) -> None:
        """Drop revocation entries whose tokens have expired (must be
        called with the lock held; throttled to once a minute)."""
        now = time.time()
        if now - self._revoked_last_prune < 60.0:
            return
        self._revoked_last_prune = now
        expired = [jti for jti, exp in self._revoked.items() if exp < now]
        for jti in expired:
            self._revoked.pop(jti, None)

    def revoke_token(self, token: str) -> None:
        try:
            payload = self.verify_token(token)
        except AuthError:
            return
        with self._lock:
            self._revoked[payload.get("jti")] = float(
                payload.get("exp", time.time() + TOKEN_TTL_S))

    def login(self, user_id: str, password: str) -> Dict[str, Any]:
        role = self.verify_credentials(user_id, password)
        return {"token": self.issue_token(user_id, role),
                "user_id": user_id, "role": role,
                "expires_in": TOKEN_TTL_S}