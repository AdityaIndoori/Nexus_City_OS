"""
Tests for the Cloudflare Access (Zero Trust) identity layer (nexus.cfaccess).

Zero external dependencies: helpers_cfaccess generates a real RSA keypair in
pure Python (Miller-Rabin prime search), publishes its public half as a JWKS
document through an injected fetcher, and signs Access-style JWTs with the
private exponent (`pow(m, d, n)`); we assert the verifier accepts valid
tokens and rejects tampered / wrong-audience / wrong-issuer / expired /
unknown-kid ones.
"""
import base64
import json
import time
import unittest

from nexus.cfaccess import AccessError, CloudflareAccess
from tests.helpers_cfaccess import _Signer


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
