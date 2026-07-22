"""
Nexus City OS — Cloudflare Access (Zero Trust) identity layer.

When enabled, Cloudflare Access becomes the **only** way to sign in: visitors
authenticate at Cloudflare's edge (Google / GitHub / Microsoft / SAML-OIDC SSO
/ email one-time-PIN) BEFORE any request reaches the origin. Cloudflare then
forwards a signed identity assertion on every request:

  * ``Cf-Access-Jwt-Assertion`` header (and a ``CF_Authorization`` cookie)
    — an RS256-signed JWT whose claims include the verified ``email``, the
    issuer (the team domain) and the audience (the Access *Application AUD*).

This module verifies that JWT — fully, with **zero external dependencies** —
so the origin trusts identity only when the signature, issuer, audience and
expiry all check out. (The bare ``Cf-Access-Authenticated-User-Email`` header
is intentionally NOT trusted on its own: it is forgeable if the origin is ever
reachable without going through Cloudflare. The signed JWT is not.)

Pure-stdlib RS256:
  RSA PKCS#1 v1.5 verification is modular exponentiation plus a constant-time
  compare of the EMSA-PKCS1-v1_5 encoding of SHA-256(signing_input). No crypto
  library is required — only ``int.from_bytes`` / ``pow`` / ``hashlib``.

Configuration (all via env; presence of the first two ENABLES Access-only mode):
  * ``NEXUS_CF_ACCESS_TEAM_DOMAIN``  e.g. ``myteam.cloudflareaccess.com``
  * ``NEXUS_CF_ACCESS_AUD``          Access Application Audience tag(s) —
                                     comma-separated when the hostname carries
                                     several path-scoped Access apps (console
                                     root + /community)
  * ``NEXUS_CF_ACCESS_ADMINS``       comma-separated admin emails        (optional)
  * ``NEXUS_CF_ACCESS_OPERATORS``    comma-separated operator emails      (optional)
  * ``NEXUS_CF_ACCESS_ANALYSTS``     comma-separated analyst emails       (optional)
  * ``NEXUS_CF_ACCESS_VIEWERS``      comma-separated viewer emails        (optional)
  * ``NEXUS_CF_ACCESS_CITIZENS``     comma-separated civilian emails      (optional)
  * ``NEXUS_CF_ACCESS_DEFAULT_ROLE`` role for an authenticated, unmapped
                                     email (default ``viewer``)
  * ``NEXUS_CF_ACCESS_SERVICE_ROLES`` service-token map ``<client-id>:<role>``
                                     comma-separated; machine principals show
                                     as ``svc:<client-id>``, default viewer,
                                     never citizen

The role map — not the Access application AUD — is the authorization
boundary between the operator console and the civilian Community Watch.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

JWKS_TTL_S = 3600.0
CLOCK_SKEW_S = 60.0
VALID_ROLES = ("admin", "operator", "analyst", "viewer", "citizen")

# ASN.1 DigestInfo prefix for SHA-256 (RFC 8017 §9.2). The PKCS#1 v1.5
# signature payload is: 0x00 0x01 [0xFF padding] 0x00 <prefix> <32-byte hash>.
_SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")


class AccessError(Exception):
    pass


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _int_from_b64url(text: str) -> int:
    return int.from_bytes(_b64url_decode(text), "big")


def _rs256_verify(signing_input: bytes, signature: bytes,
                  n: int, e: int) -> bool:
    """Verify an RS256 (RSASSA-PKCS1-v1_5 / SHA-256) signature with nothing
    but big-int math. Returns True iff the signature is valid for (n, e)."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    sig_int = int.from_bytes(signature, "big")
    if sig_int >= n:
        return False
    # RSAVP1: m = s^e mod n, then encode back to k bytes.
    em = pow(sig_int, e, n).to_bytes(k, "big")
    # Rebuild the expected EMSA-PKCS1-v1_5 encoding and constant-time compare.
    digest = hashlib.sha256(signing_input).digest()
    expected = (b"\x00\x01"
                + b"\xff" * (k - len(_SHA256_DIGESTINFO) - len(digest) - 3)
                + b"\x00" + _SHA256_DIGESTINFO + digest)
    return hmac.compare_digest(em, expected)


def _default_fetcher(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "NexusCityOS"})
    with urllib.request.urlopen(req, timeout=8) as resp:   # noqa: S310
        return resp.read()


class CloudflareAccess:
    """Verifier for Cloudflare Access JWTs + email→role mapping."""

    def __init__(self, team_domain: str = "", aud: str = "",
                 role_map: Optional[Dict[str, str]] = None,
                 default_role: str = "viewer",
                 service_roles: Optional[Dict[str, str]] = None,
                 fetcher: Optional[Callable[[str], bytes]] = None) -> None:
        self.team_domain = (team_domain or "").strip().rstrip("/")
        # Comma-separated AUDs: one origin may sit behind several path-scoped
        # Access applications (console root + /community), each with its own
        # audience tag. Role mapping — not the AUD — is the authz boundary.
        self.auds = {a.strip() for a in (aud or "").split(",") if a.strip()}
        self.default_role = default_role if default_role in VALID_ROLES \
            else "viewer"
        # email (lowercased) -> role
        self.role_map = {k.lower(): v for k, v in (role_map or {}).items()
                         if v in VALID_ROLES}
        # service-token Client ID (common_name) -> role; never citizen.
        self.service_roles = {
            k: v for k, v in (service_roles or {}).items()
            if v in VALID_ROLES and v != "citizen"}
        self._fetcher = fetcher or _default_fetcher
        self._lock = threading.RLock()
        self._jwks: Dict[str, Any] = {}     # kid -> {"n": int, "e": int}
        self._jwks_at = 0.0

    # ---- configuration ----------------------------------------------------

    @classmethod
    def from_env(cls, fetcher: Optional[Callable[[str], bytes]] = None
                 ) -> "CloudflareAccess":
        def _emails(var: str) -> List[str]:
            return [e.strip().lower() for e in
                    os.environ.get(var, "").split(",") if e.strip()]
        role_map: Dict[str, str] = {}
        for role, var in (("citizen", "NEXUS_CF_ACCESS_CITIZENS"),
                          ("viewer", "NEXUS_CF_ACCESS_VIEWERS"),
                          ("analyst", "NEXUS_CF_ACCESS_ANALYSTS"),
                          ("operator", "NEXUS_CF_ACCESS_OPERATORS"),
                          ("admin", "NEXUS_CF_ACCESS_ADMINS")):
            for email in _emails(var):
                role_map[email] = role   # later (higher-priv) wins
        service_roles: Dict[str, str] = {}
        for pair in os.environ.get(
                "NEXUS_CF_ACCESS_SERVICE_ROLES", "").split(","):
            if ":" in pair:
                cn, _, role = pair.strip().partition(":")
                if cn and role:
                    service_roles[cn] = role
        return cls(
            team_domain=os.environ.get("NEXUS_CF_ACCESS_TEAM_DOMAIN", ""),
            aud=os.environ.get("NEXUS_CF_ACCESS_AUD", ""),
            role_map=role_map,
            default_role=os.environ.get(
                "NEXUS_CF_ACCESS_DEFAULT_ROLE", "viewer"),
            service_roles=service_roles,
            fetcher=fetcher)

    @property
    def enabled(self) -> bool:
        """Access-only mode is on only when both the team domain and the
        application audience are configured (so we can validate ``aud``)."""
        return bool(self.team_domain and self.auds)

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    @property
    def certs_url(self) -> str:
        return f"https://{self.team_domain}/cdn-cgi/access/certs"

    @property
    def logout_url(self) -> str:
        return "/cdn-cgi/access/logout"

    def role_for(self, email: str) -> str:
        return self.role_map.get((email or "").lower(), self.default_role)

    # ---- JWKS -------------------------------------------------------------

    def _load_jwks(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            fresh = (time.time() - self._jwks_at) < JWKS_TTL_S
            if self._jwks and fresh and not force:
                return self._jwks
            try:
                raw = self._fetcher(self.certs_url)
                doc = json.loads(raw)
                keys = {}
                for k in doc.get("keys", []):
                    if k.get("kty") == "RSA" and "n" in k and "e" in k:
                        keys[k.get("kid", "")] = {
                            "n": _int_from_b64url(k["n"]),
                            "e": _int_from_b64url(k["e"])}
                if keys:
                    self._jwks = keys
                    self._jwks_at = time.time()
            except Exception as exc:  # noqa: BLE001
                # Keep any previously cached keys; only error if we have none.
                if not self._jwks:
                    raise AccessError(
                        f"Cannot fetch Access certs: {exc}") from None
            return self._jwks

    # ---- verification -----------------------------------------------------

    def verify(self, token: str) -> Dict[str, Any]:
        """Verify a Cloudflare Access JWT. Returns a principal dict
        ``{sub, email, role, exp}``; raises AccessError on any failure."""
        if not self.enabled:
            raise AccessError("Cloudflare Access is not configured.")
        if not token:
            raise AccessError("Missing Access assertion.")
        parts = token.split(".")
        if len(parts) != 3:
            raise AccessError("Malformed Access JWT.")
        header_b64, payload_b64, sig_b64 = parts
        try:
            header = json.loads(_b64url_decode(header_b64))
            payload = json.loads(_b64url_decode(payload_b64))
            signature = _b64url_decode(sig_b64)
        except Exception:  # noqa: BLE001
            raise AccessError("Undecodable Access JWT.") from None
        if header.get("alg") != "RS256":
            raise AccessError(f"Unexpected JWT alg {header.get('alg')!r}.")

        kid = header.get("kid", "")
        jwks = self._load_jwks()
        key = jwks.get(kid)
        if key is None:
            jwks = self._load_jwks(force=True)   # key rotation — refresh once
            key = jwks.get(kid)
        if key is None:
            raise AccessError("Unknown signing key (kid).")

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        if not _rs256_verify(signing_input, signature, key["n"], key["e"]):
            raise AccessError("Invalid Access JWT signature.")

        now = time.time()
        if payload.get("exp", 0) < now - CLOCK_SKEW_S:
            raise AccessError("Access assertion expired.")
        if payload.get("nbf", 0) > now + CLOCK_SKEW_S:
            raise AccessError("Access assertion not yet valid.")
        if payload.get("iss") != self.issuer:
            raise AccessError("Access assertion issuer mismatch.")
        aud = payload.get("aud", [])
        aud_list = aud if isinstance(aud, list) else [aud]
        if not (self.auds & set(aud_list)):
            raise AccessError("Access assertion audience mismatch.")

        email = (payload.get("email")
                 or payload.get("identity_nonce")
                 or payload.get("sub", "")).lower()
        common_name = str(payload.get("common_name", "")).strip()
        if not email and common_name:
            # Service-token assertion (machine client): sub is empty and
            # common_name carries the CF-Access-Client-Id. Never a citizen.
            return {"sub": f"svc:{common_name}", "email": "",
                    "role": self.service_roles.get(common_name, "viewer"),
                    "exp": float(payload.get("exp", now))}
        if not email:
            raise AccessError("Access assertion has no identity.")
        return {"sub": email, "email": email,
                "role": self.role_for(email),
                "exp": float(payload.get("exp", now))}
