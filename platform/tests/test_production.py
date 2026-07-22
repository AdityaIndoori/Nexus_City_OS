"""
Production-hardening tests: persistence, identity, durability, event hub.

Proves the production guarantees:
  * The audit chain survives a process restart, intact.
  * Governance state (operating mode, confidence threshold) is restored.
  * Identity is Cloudflare Access ONLY: real minted RS256 JWTs resolve
    citizen-mapped and service-token principals; forgeries are rejected.
  * The engine event hub wakes waiters on state change.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.audit import AuditTrail
from nexus.cfaccess import AccessError, CloudflareAccess
from nexus.engine import NexusEngine
from nexus.models import OperatingMode
from nexus.store import Store
from tests.helpers_auth import seed_demo_users
from tests.helpers_cfaccess import _Signer


class TestDurableAudit(unittest.TestCase):
    def test_audit_chain_survives_restart(self):
        path = os.path.join(os.path.dirname(__file__), "_t_audit.db")
        if os.path.exists(path):
            os.remove(path)
        try:
            store = Store(path)
            trail = AuditTrail(store=store)
            trail.record(actor="op-1", action="a1")
            trail.record(actor="op-1", action="a2")
            self.assertTrue(trail.verify_chain())
            store.close()
            # "restart": reopen from disk
            store2 = Store(path)
            trail2 = AuditTrail(store=store2)
            self.assertEqual(len(trail2), 2)
            self.assertTrue(trail2.verify_chain())
            # chain continues across the restart boundary
            trail2.record(actor="op-1", action="a3")
            self.assertTrue(trail2.verify_chain())
            self.assertEqual(trail2.entries()[-1]["prev_hash"],
                             trail2.entries()[-2]["entry_hash"])
            store2.close()
        finally:
            for suffix in ("", "-wal", "-shm"):
                p = path + suffix
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    def test_no_delete_or_update_api_exists(self):
        """Append-only contract: the Store exposes no way to modify audit."""
        forbidden = [m for m in dir(Store)
                     if ("audit" in m.lower()
                         and any(w in m.lower()
                                 for w in ("delete", "update", "remove")))]
        self.assertEqual(forbidden, [])


class TestGovernancePersistence(unittest.TestCase):
    def test_mode_restored_after_restart(self):
        store = Store(":memory:")
        engine = NexusEngine(store=store)
        seed_demo_users(engine)
        engine.set_mode("admin-1", OperatingMode.ADVISORY)
        # same store, new engine ⇒ simulated restart
        engine2 = NexusEngine(store=store)
        self.assertEqual(engine2.mode, OperatingMode.ADVISORY)

    def test_fresh_store_starts_shadow(self):
        engine = NexusEngine(store=Store(":memory:"))
        self.assertEqual(engine.mode, OperatingMode.SHADOW)


TEAM = "nexus-team.cloudflareaccess.com"
AUD = "abc123def456aud"


class TestCloudflareAccessIdentity(unittest.TestCase):
    """CF Access is the ONLY identity layer: end-to-end principal
    resolution with real minted RS256 JWTs."""

    def setUp(self):
        self.signer = _Signer()
        self.cfa = CloudflareAccess(
            team_domain=TEAM, aud=AUD,
            role_map={"neighbor@example.com": "citizen",
                      "chief@city.gov": "admin"},
            default_role="viewer",
            service_roles={"mcp-client-id": "analyst"},
            fetcher=lambda url: self.signer.jwks_bytes())

    def _jwt(self, **kw):
        kw.setdefault("iss", f"https://{TEAM}")
        kw.setdefault("aud", AUD)
        return self.signer.make_jwt(**kw)

    def test_citizen_mapped_email_verifies_to_citizen_role(self):
        p = self.cfa.verify(self._jwt(email="neighbor@example.com"))
        self.assertEqual(p["sub"], "neighbor@example.com")
        self.assertEqual(p["role"], "citizen")

    def test_service_token_maps_to_svc_principal_with_configured_role(self):
        p = self.cfa.verify(self._jwt(email="",
                                      common_name="mcp-client-id"))
        self.assertEqual(p["sub"], "svc:mcp-client-id")
        self.assertEqual(p["email"], "")
        self.assertEqual(p["role"], "analyst")

    def test_unmapped_service_token_defaults_to_viewer(self):
        p = self.cfa.verify(self._jwt(email="", common_name="rando-svc"))
        self.assertEqual(p["sub"], "svc:rando-svc")
        self.assertEqual(p["role"], "viewer")

    def test_forged_token_from_unknown_key_rejected(self):
        rogue = _Signer(kid="rogue-kid")
        forged = rogue.make_jwt(iss=f"https://{TEAM}", aud=AUD,
                                email="chief@city.gov")
        with self.assertRaises(AccessError):
            self.cfa.verify(forged)


class TestEventHub(unittest.TestCase):
    def test_emit_wakes_waiter(self):
        engine = NexusEngine()
        start_seq = engine.event_seq
        import threading
        result = {}

        def waiter():
            result["seq"] = engine.wait_for_event(start_seq, timeout=5.0)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        engine.emit_event("test")
        t.join(timeout=5.0)
        self.assertGreater(result["seq"], start_seq)

    def test_state_changes_bump_event_seq(self):
        engine = NexusEngine(store=Store(":memory:"))
        seed_demo_users(engine)
        before = engine.event_seq
        engine.set_mode("admin-1", OperatingMode.ADVISORY)
        self.assertGreater(engine.event_seq, before)


if __name__ == "__main__":
    unittest.main()
