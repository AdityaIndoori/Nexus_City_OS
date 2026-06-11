"""
Safety test suite — the product's primary trust artifact (MASTER_PROMPT §4).

Covers every guardrail rule (R1–R7), the hallucination monitor (H1–H4),
provenance suppression, confidence abstention, and the governed threshold.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.adapters import SeattleAdapter, default_timing_plan
from nexus.graph import CityGraph
from nexus.models import (
    ActionPlan,
    ConfidenceBreakdown,
    Incident,
    IncidentState,
    IncidentStatusFlag,
    IncidentType,
    Intersection,
    MODEL_VERSION,
    Operation,
    PlanStatus,
    Provenance,
    new_id,
    now_ts,
)
from nexus.safety import (
    CONFIDENCE_THRESHOLD_MAX,
    CONFIDENCE_THRESHOLD_MIN,
    SafetyGate,
)


def make_graph(n: int = 4) -> CityGraph:
    """A tiny line graph INT-0001 — INT-0002 — ... for unit tests."""
    from nexus.models import RoadSegment
    g = CityGraph()
    for i in range(1, n + 1):
        iid = f"INT-{i:04d}"
        g.add_intersection(Intersection(
            id=iid, name=f"Test {i}", lat=47.6 + i * 0.001, lon=-122.33,
            monitored=True, timing_plan=default_timing_plan(iid)))
    for i in range(1, n):
        g.add_segment(RoadSegment(
            id=f"SEG-{i:04d}", from_intersection=f"INT-{i:04d}",
            to_intersection=f"INT-{i + 1:04d}", name=f"seg {i}",
            speed_limit_mph=25.0, current_speed_mph=20.0))
    return g


def make_plan(targets, operations, *, graph: CityGraph,
              confidence: float = 90.0,
              with_provenance: bool = True,
              provenance_entities=None,
              data_age_s: float = 5.0) -> ActionPlan:
    now = now_ts()
    prov = Provenance(
        entities=provenance_entities if provenance_entities is not None
        else list(targets),
        data_sources=[{"source": "camera", "timestamp": now - data_age_s}],
        weather={"condition": "clear", "temperature_f": 55.0,
                 "severe_alert": False},
        rationale="test rationale",
    ) if with_provenance else Provenance(
        entities=[], data_sources=[], weather=None, rationale="")
    return ActionPlan(
        plan_id=new_id("PLAN"),
        created_at=now,
        model_version=MODEL_VERSION,
        incident_id="INC-TEST",
        targets=list(targets),
        operations=operations,
        justification="test",
        provenance=prov,
        confidence=ConfidenceBreakdown(
            model_certainty=confidence, data_freshness=confidence,
            coverage_completeness=confidence,
            historical_accuracy=confidence),
    )


class TestMUTCDConstraints(unittest.TestCase):
    """Guardrail rules R1–R7 (PRD §4.4)."""

    def setUp(self) -> None:
        self.graph = make_graph()
        self.gate = SafetyGate(self.graph)

    def test_valid_plan_passes(self):
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0001", 1, 15.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.PENDING_APPROVAL)

    def test_r1_minimum_green_blocked(self):
        # default green is 35s; reducing by 30 leaves 5s < 7s minimum
        plan = make_plan(
            ["INT-0001"],
            [Operation("reduce_green", "INT-0001", 1, 30.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_CONSTRAINT)
        self.assertIn("R1", result.block_reason)

    def test_r2_pedestrian_clearance_blocked(self):
        # Long crosswalk: 60ft at 3.5ft/s needs 17.1s. Reducing greens to
        # 12s (still > 10s phase floor and > 7s min green) violates ped
        # clearance.
        inter = self.graph.get_intersection("INT-0001")
        inter.timing_plan.crosswalk_length_ft = 70.0
        plan = make_plan(
            ["INT-0001"],
            [Operation("reduce_green", "INT-0001", 1, 23.0),
             Operation("reduce_green", "INT-0001", 2, 23.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_CONSTRAINT)
        self.assertIn("R2", result.block_reason)

    def test_r5_cycle_too_long_blocked(self):
        # 90s cycle + 95s extension = 185s > 180s max
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0001", 1, 95.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_CONSTRAINT)
        self.assertIn("R5", result.block_reason)

    def test_r5_cycle_too_short_blocked(self):
        plan = make_plan(
            ["INT-0001"],
            [Operation("adjust_cycle", "INT-0001", 1, -35.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_CONSTRAINT)
        self.assertIn("R5", result.block_reason)

    def test_r6_per_intersection_concurrency_blocked(self):
        self.gate.verifier.register_active_change("INT-0001", "PLAN-OTHER")
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0001", 1, 10.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_CONSTRAINT)
        self.assertIn("R6", result.block_reason)

    def test_r6_system_wide_limit_blocked(self):
        for i in range(5):
            self.gate.verifier.register_active_change(
                f"INT-X{i}", f"PLAN-{i}")
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0001", 1, 10.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_CONSTRAINT)
        self.assertIn("R6", result.block_reason)

    def test_r7_ems_corridor_protected(self):
        inc = Incident(
            id="INC-EMS", type=IncidentType.COLLISION,
            intersection_id="INT-0002", severity=0.9,
            status_flag=IncidentStatusFlag.EMS_RESPONDING)
        self.graph.add_incident(inc)
        plan = make_plan(
            ["INT-0002"],
            [Operation("reduce_green", "INT-0002", 1, 5.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_CONSTRAINT)
        self.assertIn("R7", result.block_reason)

    def test_r7_extend_green_on_ems_corridor_allowed(self):
        inc = Incident(
            id="INC-EMS2", type=IncidentType.COLLISION,
            intersection_id="INT-0002", severity=0.9,
            status_flag=IncidentStatusFlag.EMS_RESPONDING)
        self.graph.add_incident(inc)
        plan = make_plan(
            ["INT-0002"],
            [Operation("extend_green", "INT-0002", 1, 10.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.PENDING_APPROVAL)


class TestHallucinationMonitor(unittest.TestCase):
    """H1–H4 (PRD §4.5)."""

    def setUp(self) -> None:
        self.graph = make_graph()
        self.gate = SafetyGate(self.graph)

    def test_h1_nonexistent_intersection_blocked(self):
        plan = make_plan(
            ["INT-9999"],
            [Operation("extend_green", "INT-9999", 1, 10.0)],
            graph=self.graph, provenance_entities=["INT-0001"])
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_HALLUCINATION)
        self.assertIn("H1", result.block_reason)

    def test_h2_unknown_provenance_entity_blocked(self):
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0001", 1, 10.0)],
            graph=self.graph, provenance_entities=["GHOST-0001"])
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_HALLUCINATION)
        self.assertIn("H2", result.block_reason)

    def test_h3_stale_data_window_blocked(self):
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0001", 1, 10.0)],
            graph=self.graph, data_age_s=45 * 60.0)  # 45 min > 30 min window
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_HALLUCINATION)
        self.assertIn("H3", result.block_reason)

    def test_h4_operation_outside_targets_blocked(self):
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0002", 1, 10.0)],
            graph=self.graph)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_HALLUCINATION)
        self.assertIn("H4", result.block_reason)


class TestProvenanceAndConfidence(unittest.TestCase):
    """PRD §4.2 suppression and §4.3 abstention."""

    def setUp(self) -> None:
        self.graph = make_graph()
        self.gate = SafetyGate(self.graph)

    def test_missing_provenance_suppressed(self):
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0001", 1, 10.0)],
            graph=self.graph, with_provenance=False)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.SUPPRESSED_PROVENANCE)

    def test_low_confidence_withheld(self):
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0001", 1, 10.0)],
            graph=self.graph, confidence=40.0)
        result = self.gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.WITHHELD_CONFIDENCE)
        self.assertIn("Insufficient data confidence", result.block_reason)

    def test_confidence_weights_per_prd(self):
        c = ConfidenceBreakdown(model_certainty=100, data_freshness=0,
                                coverage_completeness=0, historical_accuracy=0)
        self.assertAlmostEqual(c.composite, 40.0)
        c = ConfidenceBreakdown(model_certainty=0, data_freshness=100,
                                coverage_completeness=0, historical_accuracy=0)
        self.assertAlmostEqual(c.composite, 25.0)
        c = ConfidenceBreakdown(model_certainty=0, data_freshness=0,
                                coverage_completeness=100,
                                historical_accuracy=0)
        self.assertAlmostEqual(c.composite, 20.0)
        c = ConfidenceBreakdown(model_certainty=0, data_freshness=0,
                                coverage_completeness=0,
                                historical_accuracy=100)
        self.assertAlmostEqual(c.composite, 15.0)

    def test_governed_threshold_range_enforced(self):
        with self.assertRaises(ValueError):
            self.gate.set_confidence_threshold(
                CONFIDENCE_THRESHOLD_MIN - 1, actor_role="admin")
        with self.assertRaises(ValueError):
            self.gate.set_confidence_threshold(
                CONFIDENCE_THRESHOLD_MAX + 1, actor_role="admin")
        self.gate.set_confidence_threshold(80.0, actor_role="admin")
        self.assertEqual(self.gate.confidence_threshold, 80.0)

    def test_threshold_admin_only(self):
        with self.assertRaises(PermissionError):
            self.gate.set_confidence_threshold(80.0, actor_role="operator")

    def test_metrics_track_block_categories_separately(self):
        # one pass, one hallucination, one constraint
        self.gate.evaluate(make_plan(
            ["INT-0001"], [Operation("extend_green", "INT-0001", 1, 10.0)],
            graph=self.graph))
        self.gate.evaluate(make_plan(
            ["INT-9999"], [Operation("extend_green", "INT-9999", 1, 10.0)],
            graph=self.graph, provenance_entities=["INT-0001"]))
        self.gate.evaluate(make_plan(
            ["INT-0001"], [Operation("extend_green", "INT-0001", 1, 95.0)],
            graph=self.graph))
        m = self.gate.metrics.as_dict()
        self.assertEqual(m["generated"], 3)
        self.assertEqual(m["blocked_hallucination"], 1)
        self.assertEqual(m["blocked_constraint"], 1)
        self.assertAlmostEqual(m["combined_block_rate_pct"], 66.67, places=1)


if __name__ == "__main__":
    unittest.main()