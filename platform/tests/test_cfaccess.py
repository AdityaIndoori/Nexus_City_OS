"""
Tests for the Cloudflare Access (Zero Trust) identity layer (nexus.cfaccess).

Zero external dependencies: we generate a real RSA keypair in pure Python
(Miller-Rabin prime search), publish its public half as a JWKS document
through an injected fetcher, sign Access-style JWTs with the private exponent
(`pow(m, d, n)`), and assert the verifier accepts valid tokens and rejects
tampered / wrong-audience / wrong-issuer / expired / unknown-kid ones.
"""
import base64
import hashlib
import json
import random
import time
import unittest

from nexus.cfaccess import (
    AccessError,
    CloudflareAccess,
    _SHA256_DIGESTINFO,
)

# ---- pure-Python RSA keygen (test-only; small & deterministic-seeded) -----

_rng = random.Random(20240615)


def _is_probable_prime(n: int, rounds: int = 20) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = _rng.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int) -> int:
    while True:
        cand = _rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(cand):
            return cand


def _gen_rsa(bits: int = 1024):
    """Generate (n, e, d). 1024-bit keeps the test fast; the verifier is
    bit-length agnostic so this exercises the same code path as CF's 2048."""
    e = 65537
    while True:
        p = _gen_prime(bits // 2)
        q = _gen_prime(bits // 2)
        if p == q:
            continue
        n = p * q
        phi = (p - 1) * (q - 1)
        if phi % e == 0:
            continue
        d = pow(e, -1, phi)
        return n, e, d


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _int_b64u(i: int) -> str:
    return _b64u(i.to_bytes((i.bit_length() + 7) // 8, "big"))


def _sign_rs256(signing_input: bytes, n: int, d: int) -> bytes:
    k = (n.bit_length() + 7) // 8
    digest = hashlib.sha256(signing_input).digest()
    em = (b"\x00\x01"
          + b"\xff" * (k - len(_SHA256_DIGESTINFO) - len(digest) - 3)
          + b"\x00" + _SHA256_DIGESTINFO + digest)
    m = int.from_bytes(em, "big")
    sig = pow(m, d, n)
    return sig.to_bytes(k, "big")


class _Signer:
    """Mints CF-Access-style JWTs for a generated key + serves its JWKS."""

    def __init__(self, kid="kid-test"):
        self.n, self.e, self.d = _gen_rsa(1024)
        self.kid = kid

    def jwks_bytes(self) -> bytes:
        return json.dumps({"keys": [{
            "kty": "RSA", "kid": self.kid, "alg": "RS256", "use": "sig",
            "n": _int_b64u(self.n), "e": _int_b64u(self.e)}]}).encode()

    def make_jwt(self, *, iss, aud, email, exp=None, kid=None, alg="RS256"):
        now = time.time()
        header = {"alg": alg, "kid": kid or self.kid, "typ": "JWT"}
        payload = {"iss": iss, "aud": aud, "email": email,
                   "iat": now, "exp": exp if exp is not None else now + 600}
        h = _b64u(json.dumps(header).encode())
        p = _b64u(json.dumps(payload).encode())
        sig = _sign_rs256(f"{h}.{p}".encode("ascii"), self.n, self.d)
        return f"{h}.{p}.{_b64u(sig)}"


TEAM = "nexus-team.cloudflareaccess.com"
AUD = "abc123def456aud"


class CloudflareAccessTests(unittest.TestCase):
    def setUp(self):
        self.signer = _Signer()
        self.cfa = CloudflareAccess(
            team_domain=TEAM, aud=AUD,
            role_map={"chief@city.gov": "admin",
                      "op@city.gov": "operator"},
            default_role="viewer",
            fetcher=lambda url: self.signer.jwks_bytes())

    def _jwt(self, **kw):
        kw.setdefault("iss", f"https://{TEAM}")
        kw.setdefault("aud", AUD)
        kw.setdefault("email", "op@city.gov")
        return self.signer.make_jwt(**kw)

    def test_enabled_requires_domain_and_aud(self):
        self.assertTrue(self.cfa.enabled)
        self.assertFalse(CloudflareAccess().enabled)
        self.assertFalse(CloudflareAccess(team_domain=TEAM).enabled)
        self.assertFalse(CloudflareAccess(aud=AUD).enabled)

    def test_valid_token_resolves_identity_and_role(self):
        p = self.cfa.verify(self._jwt(email="op@city.gov"))
        self.assertEqual(p["sub"], "op@city.gov")
        self.assertEqual(p["email"], "op@city.gov")
        self.assertEqual(p["role"], "operator")

    def test_role_mapping_admin_and_default(self):
        self.assertEqual(
            self.cfa.verify(self._jwt(email="chief@city.gov"))["role"],
            "admin")
        # unmapped email → default role
        self.assertEqual(
            self.cfa.verify(self._jwt(email="rando@city.gov"))["role"],
            "viewer")

    def test_email_case_insensitive(self):
        self.assertEqual(
            self.cfa.verify(self._jwt(email="OP@City.Gov"))["role"],
            "operator")

    def test_tampered_signature_rejected(self):
        tok = self._jwt()
        head, payload, sig = tok.split(".")
        # flip the payload (different email) but keep the old signature
        bad_payload = base64.urlsafe_b64encode(
            json.dumps({"iss": f"https://{TEAM}", "aud": AUD,
                        "email": "attacker@evil.com",
                        "exp": time.time() + 600}).encode()
            ).rstrip(b"=").decode()
        with self.assertRaises(AccessError):
            self.cfa.verify(f"{head}.{bad_payload}.{sig}")

    def test_wrong_audience_rejected(self):
        with self.assertRaises(AccessError):
            self.cfa.verify(self._jwt(aud="some-other-app"))

    def test_wrong_issuer_rejected(self):
        with self.assertRaises(AccessError):
            self.cfa.verify(self._jwt(iss="https://evil.cloudflareaccess.com"))

    def test_expired_token_rejected(self):
        with self.assertRaises(AccessError):
            self.cfa.verify(self._jwt(exp=time.time() - 3600))

    def test_unknown_kid_rejected(self):
        with self.assertRaises(AccessError):
            self.cfa.verify(self._jwt(kid="rotated-away"))

    def test_non_rs256_alg_rejected(self):
        with self.assertRaises(AccessError):
            self.cfa.verify(self._jwt(alg="HS256"))

    def test_malformed_token_rejected(self):
        for bad in ("", "not-a-jwt", "a.b", "a.b.c.d"):
            with self.assertRaises(AccessError):
                self.cfa.verify(bad)

    def test_disabled_instance_refuses(self):
        with self.assertRaises(AccessError):
            CloudflareAccess().verify(self._jwt())

    def test_from_env(self):
        import os
        keys = ["NEXUS_CF_ACCESS_TEAM_DOMAIN", "NEXUS_CF_ACCESS_AUD",
                "NEXUS_CF_ACCESS_ADMINS", "NEXUS_CF_ACCESS_DEFAULT_ROLE"]
        saved = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["NEXUS_CF_ACCESS_TEAM_DOMAIN"] = TEAM
            os.environ["NEXUS_CF_ACCESS_AUD"] = AUD
            os.environ["NEXUS_CF_ACCESS_ADMINS"] = "boss@city.gov"
            os.environ["NEXUS_CF_ACCESS_DEFAULT_ROLE"] = "analyst"
            cfa = CloudflareAccess.from_env()
            self.assertTrue(cfa.enabled)
            self.assertEqual(cfa.role_for("boss@city.gov"), "admin")
            self.assertEqual(cfa.role_for("nobody@city.gov"), "analyst")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
