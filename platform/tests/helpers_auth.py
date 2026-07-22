"""Shared identity helpers for the CF-Access-only auth model.

Engine RBAC is populated per-request by the server from the verified
Cloudflare Access principal; tests that call engine methods directly seed
the same well-known identities via seed_demo_users(). Handler tests patch
runtime.cfaccess with StubAccess (canned token -> principal map) so no
real JWT round-trip is needed at the HTTP layer."""
from typing import Any, Dict

from nexus.cfaccess import AccessError
from nexus.models import Role


def seed_demo_users(engine) -> None:
    with engine._lock:
        engine.users.update({
            "op-1": Role.OPERATOR,
            "analyst-1": Role.ANALYST,
            "admin-1": Role.ADMIN,
            "viewer-1": Role.VIEWER,
        })


class StubAccess:
    """cfaccess stand-in: enabled, verify() resolves canned principals."""

    enabled = True
    logout_url = "/cdn-cgi/access/logout"

    def __init__(self) -> None:
        self._principals: Dict[str, Dict[str, Any]] = {}

    def add(self, token: str, sub: str, role: str,
            email: str = None) -> str:
        self._principals[token] = {
            "sub": sub, "email": sub if email is None else email,
            "role": role, "exp": 4102444800.0}
        return token

    def verify(self, token: str) -> Dict[str, Any]:
        principal = self._principals.get(token)
        if principal is None:
            raise AccessError("Missing or invalid Access assertion.")
        return dict(principal)
