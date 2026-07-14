"""
Shadow-evidence engine tests (ADR-003). Network-free: ``Store(":memory:")``
seeded with synthetic shadow plans + congestion samples.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.audit import AuditTrail
from nexus.evidence import EvidenceEngine, MIN_WINDOW_SAMPLES
from nexus.store import Store


NOW = time.time()
T_APPLY = NOW - 4 * 3600.0        # would-be application time, 4h ago
OPERATOR_EMAIL = "op.secret@seattle.gov"


def seed_shadow_plan(store, plan_id="PLAN-1", target="INT-A", at=T_APPLY):
    """A logged shadow-mode would-be plan, as engine._persist_plan writes it."""
    payload = {
        "plan_id": plan_id,
        "incident_id": "INC-1",
        "targets": [target],
        "operations": [{"type": "extend_green", "intersection_id": target,
                        "phase_id": 1, "delta_seconds": 10.0}],
        "created_at": at - 60.0,
        "approved_at": at,
        "approved_by": OPERATOR_EMAIL,          # must never reach reports
        "justification": f"note by {OPERATOR_EMAIL}: clear backup",
        "status": "shadow_logged",
    }
    store.upsert_plan(plan_id, "shadow_logged", "INC-1", payload, at)


def seed_windows(store, target="INT-A", at=T_APPLY,
                 before=0.8, after=0.4, n=MIN_WINDOW_SAMPLES + 2):
    """n samples in each matched window around ``at`` (60s spacing)."""
    rows = []
    for i in range(n):
        rows.append((target, before, at - 60.0 * (i + 1)))
        rows.append((target, after, at + 60.0 * (i + 1)))
    store.add_congestion_samples(rows)


class TestEvidenceScorecard(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.engine = EvidenceEngine(self.store)

    def test_seeded_shadow_plan_yields_expected_delta(self):
        seed_shadow_plan(self.store)
        seed_windows(self.store, before=0.8, after=0.4)
        sc = self.engine.scorecard(now=NOW)
        self.assertEqual(sc.plans_scored, 1)
        self.assertEqual(sc.scorecard_abstentions, 0)
        # congestion fell 0.8 → 0.4 in the matched after window
        self.assertAlmostEqual(sc.avg_congestion_delta, -0.4, places=3)
        score = sc.plan_scores[0]
        self.assertFalse(score.abstained)
        self.assertAlmostEqual(score.delta, -0.4, places=3)
        self.assertGreaterEqual(score.before_samples, MIN_WINDOW_SAMPLES)

    def test_thin_data_abstains_with_reason(self):
        seed_shadow_plan(self.store)
        seed_windows(self.store, n=MIN_WINDOW_SAMPLES - 1)   # too thin
        sc = self.engine.scorecard(now=NOW)
        self.assertEqual(sc.plans_scored, 0)
        self.assertEqual(sc.scorecard_abstentions, 1)
        self.assertIsNone(sc.avg_congestion_delta)
        score = sc.plan_scores[0]
        self.assertTrue(score.abstained)
        self.assertIn("samples", score.abstain_reason)

    def test_no_shadow_plans_is_safe(self):
        sc = self.engine.scorecard(now=NOW)
        self.assertEqual(sc.plans_logged, 0)
        self.assertEqual(sc.plans_scored, 0)
        self.assertIsNone(sc.avg_congestion_delta)

    def test_cumulative_counters(self):
        seed_shadow_plan(self.store)
        seed_windows(self.store)
        self.store.upsert_plan("PLAN-B", "blocked_constraint", "INC-2",
                               {"plan_id": "PLAN-B", "targets": []},
                               T_APPLY)
        self.store.upsert_plan("PLAN-W", "withheld_confidence", "INC-3",
                               {"plan_id": "PLAN-W", "targets": []},
                               T_APPLY)
        sc = self.engine.scorecard(now=NOW)
        self.assertEqual(sc.plans_logged, 1)
        self.assertEqual(sc.plans_refused, 1)
        self.assertEqual(sc.plans_abstained, 1)
        self.assertGreater(sc.days_in_shadow, 0.0)


class TestEvidenceTemplates(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        seed_shadow_plan(self.store)
        seed_windows(self.store)
        self.audit = AuditTrail()
        self.audit.record(actor="system", action="shadow_logged")
        self.engine = EvidenceEngine(self.store, audit=self.audit)
        self.sc = self.engine.scorecard(now=NOW)
        self.pages = {
            "decision_audit": self.engine.render_decision_audit(self.sc),
            "grant_packet": self.engine.render_grant_packet(self.sc),
            "kpi_benchmark": self.engine.render_kpi_benchmark(self.sc),
        }

    def test_all_templates_render_computed_kpis(self):
        for name, html in self.pages.items():
            self.assertGreater(len(html), 500, name)
            self.assertIn("@media print", html, name)
            self.assertIn(str(self.sc.plans_logged), html, name)
            self.assertIn("-0.400", html, name)              # the delta
            self.assertIn("directional signal, not ground truth", html, name)
            self.assertIn("matched before/after windows", html, name)

    def test_chain_verification_proof_line(self):
        self.assertTrue(self.sc.audit_chain_verified)
        for html in self.pages.values():
            self.assertIn("Audit chain verified: yes", html)

    def test_shareable_reports_contain_no_operator_identity(self):
        for name, html in self.pages.items():
            self.assertNotIn(OPERATOR_EMAIL, html, name)
            self.assertNotIn("@seattle.gov", html, name)

    def test_dollar_anchor_rendered(self):
        self.assertIn("delay", self.sc.dollar_anchor.lower())
        for html in self.pages.values():
            self.assertIn(self.sc.dollar_anchor, html)


if __name__ == "__main__":
    unittest.main()
