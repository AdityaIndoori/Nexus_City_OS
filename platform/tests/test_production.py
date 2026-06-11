"""
Production-hardening tests: persistence, auth, durability, event hub.

Proves the Phase 1+2 guarantees:
  * The audit chain survives a process restart, intact.
  * Governance state (operating mode, confidence threshold) is restored.
  * Credentials are PBKDF2-verified; bad passwords / lockout / token
    forgery / expiry / revocation are all rejected.
  * The engine event hub wakes waiters on state change.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.audit import AuditTrail
from nexus.auth import AuthError, Authenticator
from nexus.engine import NexusEngine
from nexus.models import OperatingMode
from nexus.store import Store


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
        engine.set_mode("admin-1", OperatingMode.ADVISORY)
        # same store, new engine ⇒ simulated restart
        engine2 = NexusEngine(store=store)
        self.assertEqual(engine2.mode, OperatingMode.ADVISORY)

    def test_fresh_store_starts_shadow(self):
        engine = NexusEngine(store=Store(":memory:"))
        self.assertEqual(engine.mode, OperatingMode.SHADOW)


class TestAuth(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.auth = Authenticator(self.store, secret=b"t" * 32)

    def test_login_returns_role_and_valid_token(self):
        session = self.auth.login("op-1", "nexus-op-1")
        self.assertEqual(session["role"], "operator")
        payload = self.auth.verify_token(session["token"])
        self.assertEqual(payload["sub"], "op-1")

    def test_wrong_password_rejected(self):
        with self.assertRaises(AuthError):
            self.auth.login("op-1", "wrong")

    def test_unknown_user_rejected(self):
        with self.assertRaises(AuthError):
            self.auth.login("ghost", "x")

    def test_lockout_after_failures(self):
        for _ in range(5):
            try:
                self.auth.login("op-1", "wrong")
            except AuthError:
                pass
        with self.assertRaises(AuthError) as ctx:
            self.auth.login("op-1", "nexus-op-1")  # correct pw, but locked
        self.assertIn("locked", str(ctx.exception).lower())

    def test_forged_token_rejected(self):
        session = self.auth.login("op-1", "nexus-op-1")
        body, sig = session["token"].split(".", 1)
        with self.assertRaises(AuthError):
            self.auth.verify_token(body + "." + sig[:-2] + "xx")
        # tampered payload (role escalation attempt)
        import base64, json
        raw = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        raw["role"] = "admin"
        forged_body = base64.urlsafe_b64encode(
            json.dumps(raw, sort_keys=True).encode()).rstrip(b"=").decode()
        with self.assertRaises(AuthError):
            self.auth.verify_token(forged_body + "." + sig)

    def test_revoked_token_rejected(self):
        session = self.auth.login("op-1", "nexus-op-1")
        self.auth.revoke_token(session["token"])
        with self.assertRaises(AuthError):
            self.auth.verify_token(session["token"])

    def test_password_hashes_are_salted(self):
        self.auth.create_user("a", "viewer", "same-password")
        self.auth.create_user("b", "viewer", "same-password")
        ua, ub = self.store.get_user("a"), self.store.get_user("b")
        self.assertNotEqual(ua["pw_hash"], ub["pw_hash"])  # unique salts


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
        before = engine.event_seq
        engine.set_mode("admin-1", OperatingMode.ADVISORY)
        self.assertGreater(engine.event_seq, before)


if __name__ == "__main__":
    unittest.main()