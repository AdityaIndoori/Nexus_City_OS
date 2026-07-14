"""
Stateless Verification API tests (ADR-005, C1).

Covers: POST /api/v1/verify PASS with per-rule detail; kinematic R3 FAIL with
explanation; workzone rulepack selection; statelessness (nonexistent
intersection still gets full R1-R5 results); malformed JSON vs empty plan
400 discrimination; unauthenticated 401; POST /api/v1/certs/verify signature
+ chain-membership verification (valid / tampered / unknown cert_id).

Handlers are exercised fully in-process (no sockets, no network): the
make_handler() class is driven with raw HTTP bytes over BytesIO.
"""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus import bootstrap
from nexus.adapters import SeattleAdapter
from nexus.auth import Authenticator
from nexus.cfaccess import CloudflareAccess
from nexus.models import (
    ActionPlan,
    ConfidenceBreakdown,
    MODEL_VERSION,
    Operation,
    Provenance,
    new_id,
    now_ts,
)
from nexus.security import IPRateLimiter
from nexus.server import make_handler
from nexus.store import Store


class FakeRuntime:
    """Duck-typed PlatformRuntime carrying only what the handler touches."""

    def __init__(self):
        self.store = Store(":memory:")
        self.engine, self.edge, self.adapter = bootstrap(
            SeattleAdapter(seed=42), self.store)
        self.auth = Authenticator(self.store)
        self.cfaccess = CloudflareAccess.from_env()
        self.ratelimit = IPRateLimiter()


def http_post(handler_cls, path, raw_body, token=None):
    lines = [f"POST {path} HTTP/1.1", "Host: test"]
    if token:
        lines.append(f"Authorization: Bearer {token}")
    lines.append("Content-Type: application/json")
    lines.append(f"Content-Length: {len(raw_body)}")
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + raw_body

    class _Driver(handler_cls):
        def __init__(self):
            self.rfile = io.BytesIO(request)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 40000)
            self.handle_one_request()

    driver = _Driver()
    response = driver.wfile.getvalue()
    head, _, payload = response.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, (json.loads(payload) if payload else {})


def good_plan(intersection_id="INT-0001", yellow=4.0, speed_mph=30.0):
    return {
        "intersection_id": intersection_id,
        "cycle_seconds": 90.0,
        "pedestrian_walk_seconds": 7.0,
        "crosswalk_length_ft": 60.0,
        "phases": [
            {"phase_id": 1, "movement": "through", "green_seconds": 30.0,
             "yellow_seconds": yellow, "red_clearance_seconds": 1.0,
             "approach_speed_mph": speed_mph, "conflicts_with": [2],
             "grade_pct": 0.0},
            {"phase_id": 2, "movement": "through", "green_seconds": 30.0,
             "yellow_seconds": yellow, "red_clearance_seconds": 1.0,
             "approach_speed_mph": speed_mph, "conflicts_with": [1],
             "grade_pct": 0.0},
        ],
    }


class VerifyApiBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.runtime = FakeRuntime()
        cls.handler_cls = make_handler(cls.runtime)
        cls.token = cls.runtime.auth.login("op-1", "nexus-op-1")["token"]

    def post(self, path, body, token="use-default"):
        tok = self.token if token == "use-default" else token
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return http_post(self.handler_cls, path, raw, token=tok)


class TestVerifyEndpoint(VerifyApiBase):

    def test_valid_mutcd_plan_passes_with_rules_evaluated(self):
        status, resp = self.post("/api/v1/verify", {"plan": good_plan()})
        self.assertEqual(status, 200)
        self.assertEqual(resp["verdict"], "PASS")
        self.assertEqual(resp["rulepack"], "mutcd")
        self.assertTrue(resp["rulepack_version"].startswith("mutcd-"))
        self.assertEqual(resp["violations"], [])
        self.assertEqual(resp["rules_evaluated"],
                         ["R1", "R2", "R3", "R4", "R5"])

    def test_short_yellow_at_45mph_fails_r3_with_explanation(self):
        status, resp = self.post(
            "/api/v1/verify",
            {"plan": good_plan(yellow=4.0, speed_mph=45.0)})
        self.assertEqual(status, 200)
        self.assertEqual(resp["verdict"], "FAIL")
        r3 = next(r for r in resp["violations"] if r["rule_id"] == "R3")
        self.assertFalse(r3["passed"])
        self.assertIn("kinematic", r3["violations"][0]["message"])
        self.assertIn("45 mph", r3["violations"][0]["message"])

    def test_workzone_rulepack_selectable(self):
        plan = good_plan()
        plan["phases"][0]["green_seconds"] = 8.0     # < WZ1 10s minimum
        status, resp = self.post(
            "/api/v1/verify", {"plan": plan, "rulepack": "workzone"})
        self.assertEqual(status, 200)
        self.assertEqual(resp["rulepack"], "workzone")
        self.assertEqual(resp["verdict"], "FAIL")
        self.assertIn("WZ1", [r["rule_id"] for r in resp["violations"]])
        self.assertEqual(resp["rules_evaluated"],
                         ["WZ1", "WZ2", "WZ3", "WZ4"])

    def test_stateless_nonexistent_intersection_gets_full_results(self):
        status, resp = self.post(
            "/api/v1/verify",
            {"plan": good_plan(intersection_id="INT-DOES-NOT-EXIST")})
        self.assertEqual(status, 200)
        self.assertEqual(resp["verdict"], "PASS")
        self.assertEqual(resp["rules_evaluated"],
                         ["R1", "R2", "R3", "R4", "R5"])

    def test_malformed_json_returns_400_fixed_message(self):
        status, resp = self.post("/api/v1/verify", b"{not valid json")
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"], "invalid JSON body")

    def test_empty_body_returns_400_missing_plan(self):
        status, resp = self.post("/api/v1/verify", b"")
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"], "missing plan")

    def test_garbage_plan_returns_400_fixed_message(self):
        status, resp = self.post(
            "/api/v1/verify",
            {"plan": {"cycle_seconds": "not a number", "phases": [{}]}})
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"], "invalid plan")
        self.assertNotIn("Traceback", json.dumps(resp))

    def test_unknown_rulepack_returns_400(self):
        status, resp = self.post(
            "/api/v1/verify", {"plan": good_plan(), "rulepack": "nope"})
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"], "unknown rulepack")

    def test_unauthenticated_returns_401(self):
        status, _ = self.post("/api/v1/verify", {"plan": good_plan()},
                              token=None)
        self.assertEqual(status, 401)


def make_action_plan(targets):
    now = now_ts()
    return ActionPlan(
        plan_id=new_id("PLAN"),
        created_at=now,
        model_version=MODEL_VERSION,
        incident_id="INC-TEST",
        targets=list(targets),
        operations=[Operation("extend_green", targets[0], 1, 15.0)],
        justification="test",
        provenance=Provenance(
            entities=list(targets),
            data_sources=[{"source": "camera", "timestamp": now - 5.0}],
            weather={"condition": "clear", "temperature_f": 55.0,
                     "severe_alert": False},
            rationale="test rationale"),
        confidence=ConfidenceBreakdown(
            model_certainty=90.0, data_freshness=90.0,
            coverage_completeness=90.0, historical_accuracy=90.0),
    )


class TestCertsVerifyEndpoint(VerifyApiBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        engine = cls.runtime.engine
        iid = next(iter(engine.graph.intersections))
        plan = engine.safety.evaluate(make_action_plan([iid]))
        cls.cert_entry = engine.certs.issue(plan, engine.safety)

    def test_valid_certificate_verifies_signature_and_chain(self):
        status, resp = self.post("/api/v1/certs/verify", self.cert_entry)
        self.assertEqual(status, 200)
        self.assertTrue(resp["signature_valid"])
        self.assertTrue(resp["chain_member"])
        self.assertEqual(resp["verdict"], "VALID")

    def test_tampered_certificate_fails_signature(self):
        tampered = copy.deepcopy(self.cert_entry)
        tampered["after_state"]["certificate"]["verdict"] = "forged"
        status, resp = self.post("/api/v1/certs/verify", tampered)
        self.assertEqual(status, 200)
        self.assertFalse(resp["signature_valid"])
        self.assertEqual(resp["verdict"], "INVALID")

    def test_unknown_cert_id_not_chain_member(self):
        forged = copy.deepcopy(self.cert_entry)
        forged["after_state"]["certificate"]["cert_id"] = "CERT-DEADBEEF"
        status, resp = self.post("/api/v1/certs/verify", forged)
        self.assertEqual(status, 200)
        self.assertFalse(resp["chain_member"])
        self.assertEqual(resp["verdict"], "INVALID")

    def test_empty_body_returns_400(self):
        status, resp = self.post("/api/v1/certs/verify", b"")
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"], "missing certificate")

    def test_malformed_json_returns_400(self):
        status, resp = self.post("/api/v1/certs/verify", b"][")
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"], "invalid JSON body")

    def test_unauthenticated_returns_401(self):
        status, _ = self.post("/api/v1/certs/verify", self.cert_entry,
                              token=None)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
