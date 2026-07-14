"""
Certificate / Compliance Engine tests (ADR-002).

Covers: HMAC signature verification, single-byte tamper detection, audit-chain
participation (including across a Store restart), signing-key isolation from
the auth key, key rotation with retired-key verification, template rendering,
and redaction (no operator identities, no key material anywhere).
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus import bootstrap
from nexus.adapters import SeattleAdapter, default_timing_plan
from nexus.audit import AuditTrail
from nexus.auth import Authenticator
from nexus.certs import CERT_ACTION, CertificateEngine
from nexus.graph import CityGraph
from nexus.models import (
    ActionPlan,
    ConfidenceBreakdown,
    IncidentType,
    Intersection,
    MODEL_VERSION,
    Operation,
    PlanStatus,
    Provenance,
    new_id,
    now_ts,
)
from nexus.safety import SafetyGate
from nexus.store import Store


def make_graph(n: int = 4) -> CityGraph:
    g = CityGraph()
    for i in range(1, n + 1):
        iid = f"INT-{i:04d}"
        g.add_intersection(Intersection(
            id=iid, name=f"Test {i}", lat=47.6 + i * 0.001, lon=-122.33,
            monitored=True, timing_plan=default_timing_plan(iid)))
    return g


def make_plan(targets, operations, *, confidence: float = 90.0) -> ActionPlan:
    now = now_ts()
    return ActionPlan(
        plan_id=new_id("PLAN"),
        created_at=now,
        model_version=MODEL_VERSION,
        incident_id="INC-TEST",
        targets=list(targets),
        operations=operations,
        justification="test",
        provenance=Provenance(
            entities=list(targets),
            data_sources=[{"source": "camera", "timestamp": now - 5.0}],
            weather={"condition": "clear", "temperature_f": 55.0,
                     "severe_alert": False},
            rationale="test rationale"),
        confidence=ConfidenceBreakdown(
            model_certainty=confidence, data_freshness=confidence,
            coverage_completeness=confidence, historical_accuracy=confidence),
    )


class TestCertificateEngine(unittest.TestCase):

    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.audit = AuditTrail(store=self.store)
        self.graph = make_graph()
        self.gate = SafetyGate(self.graph)
        self.certs = CertificateEngine(self.store, self.audit)

    def _issue(self, **kw):
        plan = make_plan(
            ["INT-0001"], [Operation("extend_green", "INT-0001", 1, 15.0)],
            **kw)
        plan = self.gate.evaluate(plan)
        return plan, self.certs.issue(plan, self.gate)

    def test_hmac_verifies(self):
        plan, entry = self._issue()
        self.assertEqual(entry["action"], CERT_ACTION)
        cert = entry["after_state"]
        self.assertTrue(self.certs.verify_certificate(cert))
        # convenience: verify accepts the whole audit entry too
        self.assertTrue(self.certs.verify_certificate(entry))
        body = cert["certificate"]
        self.assertEqual(body["plan_id"], plan.plan_id)
        self.assertEqual(body["plan_hash"], plan.plan_hash())
        self.assertEqual(body["verdict"], PlanStatus.PENDING_APPROVAL.value)
        self.assertTrue(body["cert_id"].startswith("CERT-"))
        self.assertEqual(len(body["ruleset_version"]), 64)
        self.assertIn("key_id", body)
        rule_ids = {r["rule_id"] for r in body["rules_run"]}
        for rid in ("R1", "R2", "R3", "R4", "R5", "R6", "R7",
                    "H1", "H2", "H3", "H4"):
            self.assertIn(rid, rule_ids)
        self.assertTrue(all(r["passed"] for r in body["rules_run"]))
        self.assertIn("plan", body["input_snapshot_hashes"])
        self.assertIn("provenance", body["input_snapshot_hashes"])

    def test_blocked_plan_gets_certificate_with_failed_rules(self):
        plan = make_plan(
            ["INT-0001"], [Operation("reduce_green", "INT-0001", 1, 30.0)])
        plan = self.gate.evaluate(plan)
        self.assertEqual(plan.status, PlanStatus.BLOCKED_CONSTRAINT)
        entry = self.certs.issue(plan, self.gate)
        body = entry["after_state"]["certificate"]
        self.assertEqual(body["verdict"], PlanStatus.BLOCKED_CONSTRAINT.value)
        r1 = next(r for r in body["rules_run"] if r["rule_id"] == "R1")
        self.assertFalse(r1["passed"])
        self.assertIn("R1", r1["detail"])
        self.assertTrue(self.certs.verify_certificate(entry))

    def test_single_byte_tamper_fails(self):
        _, entry = self._issue()
        tampered = json.loads(json.dumps(entry["after_state"]))
        tampered["certificate"]["verdict"] = (
            tampered["certificate"]["verdict"][:-1] + "X")
        self.assertFalse(self.certs.verify_certificate(tampered))
        sig_flip = json.loads(json.dumps(entry["after_state"]))
        sig = sig_flip["signature"]
        sig_flip["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]
        self.assertFalse(self.certs.verify_certificate(sig_flip))
        self.assertFalse(self.certs.verify_certificate({}))
        self.assertFalse(self.certs.verify_certificate(
            {"certificate": None, "signature": "aa"}))

    def test_cert_entry_participates_in_audit_chain(self):
        _, entry = self._issue()
        self.assertTrue(self.audit.verify_chain())
        # HMAC is embedded in the entry body BEFORE the chain hash: forging
        # the cert body inside the stored entry breaks the chain itself.
        idx = entry["seq"]
        self.audit._entries[idx]["after_state"]["certificate"]["verdict"] = \
            "forged"
        self.assertFalse(self.audit.verify_chain())

    def test_chain_and_verification_survive_store_restart(self):
        path = "_t_certs_restart.db"
        try:
            store = Store(path)
            audit = AuditTrail(store=store)
            certs = CertificateEngine(store, audit)
            plan = self.gate.evaluate(make_plan(
                ["INT-0001"],
                [Operation("extend_green", "INT-0001", 1, 15.0)]))
            entry = certs.issue(plan, self.gate)
            store.close()

            store2 = Store(path)
            audit2 = AuditTrail(store=store2)
            self.assertTrue(audit2.verify_chain())
            certs2 = CertificateEngine(store2, audit2)
            reloaded = next(e for e in audit2.entries(500)
                            if e["action"] == CERT_ACTION)
            self.assertEqual(reloaded["after_state"]["signature"],
                             entry["after_state"]["signature"])
            self.assertTrue(certs2.verify_certificate(reloaded))
            store2.close()
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass

    def test_cert_key_distinct_from_auth_key(self):
        Authenticator(self.store)
        auth_key = self.store.get_kv("auth_signing_key")
        cert_key = self.store.get_kv("cert_signing_key")
        self.assertIsNotNone(auth_key)
        self.assertIsNotNone(cert_key)
        self.assertNotEqual(auth_key, cert_key)

    def test_env_override_key(self):
        os.environ["NEXUS_CERT_KEY"] = "ab" * 32
        try:
            store_a = Store(":memory:")
            certs_a = CertificateEngine(store_a, AuditTrail(store=store_a))
            plan = self.gate.evaluate(make_plan(
                ["INT-0001"],
                [Operation("extend_green", "INT-0001", 1, 15.0)]))
            entry = certs_a.issue(plan, self.gate)
            # a second engine with a DIFFERENT store but the same env key
            # verifies the certificate — proof the env key was used.
            store_b = Store(":memory:")
            certs_b = CertificateEngine(store_b, AuditTrail(store=store_b))
            self.assertTrue(certs_b.verify_certificate(entry))
        finally:
            del os.environ["NEXUS_CERT_KEY"]
        self.assertFalse(self.certs.verify_certificate(entry))

    def test_key_rotation_old_certs_still_verify(self):
        _, entry_old = self._issue()
        key_id_old = entry_old["after_state"]["certificate"]["key_id"]
        self.certs.rotate_key()
        self.assertTrue(self.certs.verify_certificate(entry_old))
        _, entry_new = self._issue()
        key_id_new = entry_new["after_state"]["certificate"]["key_id"]
        self.assertNotEqual(key_id_old, key_id_new)
        self.assertTrue(self.certs.verify_certificate(entry_new))
        # retired keys are verification-only: new certs sign with the new key
        retired = self.store.get_kv("cert_signing_key_retired")
        self.assertEqual(len(retired), 1)
        self.assertNotEqual(self.store.get_kv("cert_signing_key"), retired[0])
        # tamper still fails post-rotation
        tampered = json.loads(json.dumps(entry_old["after_state"]))
        tampered["certificate"]["plan_id"] = "PLAN-FORGED1"
        self.assertFalse(self.certs.verify_certificate(tampered))

    def test_all_three_templates_render(self):
        _, entry = self._issue()
        body = entry["after_state"]["certificate"]
        renders = [self.certs.render_after_action(entry),
                   self.certs.render_nist_conformity(entry),
                   self.certs.render_investor_case(entry)]
        for html in renders:
            self.assertTrue(html.strip())
            self.assertIn(body["verdict"], html)
            self.assertIn(body["ruleset_version"], html)
            self.assertIn("HMAC (symmetric)", html)
            self.assertIn("issuer-verifiable", html)
            self.assertIn("@media print", html)
        self.assertIn("NIST AI RMF", renders[1])
        for fn in ("Govern", "Map", "Measure", "Manage"):
            self.assertIn(fn, renders[1])
        self.assertIn("Annex IV", renders[1])   # roadmap flag line only

    def test_no_key_material_or_operator_identity_leaks(self):
        _, entry = self._issue()
        key_hex = self.store.get_kv("cert_signing_key")
        entry_json = json.dumps(entry, default=str)
        self.assertNotIn(key_hex, entry_json)
        body = entry["after_state"]["certificate"]
        self.assertNotIn("approved_by", body)
        self.assertNotIn("@", json.dumps(body, default=str))
        for html in (self.certs.render_after_action(entry),
                     self.certs.render_nist_conformity(entry),
                     self.certs.render_investor_case(entry)):
            self.assertNotIn(key_hex, html)


class TestEngineCertificateHookup(unittest.TestCase):

    def test_recommend_issues_certificate_when_store_present(self):
        engine, edge, _ = bootstrap(SeattleAdapter(seed=42),
                                    store=Store(":memory:"))
        iid = next(iter(engine.graph.cameras.values())).intersection_id
        edge.inject_scenario(iid, IncidentType.COLLISION)
        edge.tick()
        inc = next(i for i in engine.graph.incidents.values()
                   if i.intersection_id == iid)
        plan = engine.recommend(inc.id)
        cert_entries = [e for e in engine.audit.entries(500)
                        if e["action"] == CERT_ACTION]
        self.assertEqual(len(cert_entries), 1)
        body = cert_entries[0]["after_state"]["certificate"]
        self.assertEqual(body["plan_id"], plan.plan_id)
        self.assertEqual(body["verdict"], plan.status.value)
        self.assertTrue(engine.certs.verify_certificate(cert_entries[0]))
        self.assertTrue(engine.audit.verify_chain())

    def test_no_store_means_no_certificate_and_no_crash(self):
        engine, edge, _ = bootstrap(SeattleAdapter(seed=42))
        self.assertIsNone(engine.certs)
        iid = next(iter(engine.graph.cameras.values())).intersection_id
        edge.inject_scenario(iid, IncidentType.COLLISION)
        edge.tick()
        inc = next(i for i in engine.graph.incidents.values()
                   if i.intersection_id == iid)
        plan = engine.recommend(inc.id)   # must not raise
        self.assertEqual(plan.status, PlanStatus.PENDING_APPROVAL)

    def test_cert_failure_never_breaks_plan_flow(self):
        engine, edge, _ = bootstrap(SeattleAdapter(seed=42),
                                    store=Store(":memory:"))
        def boom(*a, **k):
            raise RuntimeError("cert backend down")
        engine.certs.issue = boom
        iid = next(iter(engine.graph.cameras.values())).intersection_id
        edge.inject_scenario(iid, IncidentType.COLLISION)
        edge.tick()
        inc = next(i for i in engine.graph.incidents.values()
                   if i.intersection_id == iid)
        plan = engine.recommend(inc.id)   # must not raise
        self.assertEqual(plan.status, PlanStatus.PENDING_APPROVAL)


if __name__ == "__main__":
    unittest.main()
