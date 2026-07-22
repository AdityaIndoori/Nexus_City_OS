"""Shared CF-Access test helpers: pure-Python RSA keygen (Miller-Rabin),
RS256 signing, and a JWT-minting _Signer — extracted from test_cfaccess so
identity tests across the suite can mint REAL Access-style JWTs offline."""
import base64
import hashlib
import json
import random
import time

from nexus.cfaccess import _SHA256_DIGESTINFO

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

    def make_jwt(self, *, iss, aud, email, exp=None, kid=None, alg="RS256",
                 common_name=None):
        now = time.time()
        header = {"alg": alg, "kid": kid or self.kid, "typ": "JWT"}
        payload = {"iss": iss, "aud": aud, "email": email,
                   "iat": now, "exp": exp if exp is not None else now + 600}
        if common_name is not None:
            payload["common_name"] = common_name
        h = _b64u(json.dumps(header).encode())
        p = _b64u(json.dumps(payload).encode())
        sig = _sign_rs256(f"{h}.{p}".encode("ascii"), self.n, self.d)
        return f"{h}.{p}.{_b64u(sig)}"
