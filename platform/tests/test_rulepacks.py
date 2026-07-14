"""
Rulepack refactor suite (TJ-N2, ADR-004).

Proves the ConstraintVerifier refactor into a domain-agnostic rule core +
declarative rulepacks: (a) verdict parity with the pre-refactor verifier at
post-E1 HEAD, (b) stateful R6/R7 still fire, (c) the MUTCD Chapter 6
work-zone rulepack proves generality, (d) run_rulepack() needs no CityGraph.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.adapters import default_timing_plan
from nexus.graph import CityGraph
from nexus.models import (
    Incident,
    IncidentStatusFlag,
    IncidentType,
    Intersection,
    Operation,
    SignalPhase,
    SignalTimingPlan,
)
from nexus.rulepacks import (
    RULEPACKS,
    run_rulepack,
    rulepack_version,
)
from nexus.safety import ConstraintVerifier
from tests.test_safety import make_plan


def make_parity_graph() -> CityGraph:
    g = CityGraph()
    g.add_intersection(Intersection(
        id="INT-0001", name="Std 30mph", lat=47.6, lon=-122.33,
        monitored=True, timing_plan=default_timing_plan("INT-0001")))
    g.add_intersection(Intersection(
        id="INT-0002", name="Fast 45mph", lat=47.601, lon=-122.33,
        monitored=True,
        timing_plan=default_timing_plan("INT-0002", approach_speed_mph=45.0)))
    long_greens = Intersection(
        id="INT-0003", name="Long greens", lat=47.602, lon=-122.33,
        monitored=True, timing_plan=default_timing_plan("INT-0003"))
    for p in long_greens.timing_plan.phases:
        p.green_seconds = 45.0
    g.add_intersection(long_greens)
    wide = Intersection(
        id="INT-0004", name="Wide crosswalk", lat=47.603, lon=-122.33,
        monitored=True, timing_plan=default_timing_plan("INT-0004"))
    wide.timing_plan.crosswalk_length_ft = 70.0
    g.add_intersection(wide)
    return g


def make_workzone_timing(green_s: float = 35.0, cycle_s: float = 90.0,
                         red_clearance_s: float = 2.0,
                         yellow_s: float = 4.0,
                         approach_mph: float = 30.0) -> SignalTimingPlan:
    return SignalTimingPlan(
        plan_id="STP-WZ", intersection_id="INT-WZ",
        cycle_seconds=cycle_s,
        phases=[
            SignalPhase(1, "through", green_s, yellow_s, red_clearance_s,
                        approach_mph, [2], 0.0),
            SignalPhase(2, "through", green_s, yellow_s, red_clearance_s,
                        approach_mph, [1], 0.0),
        ],
        pedestrian_walk_seconds=8.0, crosswalk_length_ft=60.0)


# Verdicts captured by running ConstraintVerifier.verify() at post-E1 HEAD
# (commit 4f98764 kinematics) against make_parity_graph() — the refactored
# verifier must reproduce them byte-for-byte.
PARITY_CASES = {
    "pass_valid_extend": (
        "INT-0001", [Operation("extend_green", "INT-0001", 1, 15.0)],
        True, []),
    "r1_fail_sub_minimum_green": (
        "INT-0001", [Operation("reduce_green", "INT-0001", 1, 30.0)],
        False,
        [("R1", "INT-0001 phase 1: green 5.0s < minimum 7s (through).")]),
    "r2_fail_ped_service_window": (
        "INT-0004", [Operation("reduce_green", "INT-0004", 1, 23.0),
                     Operation("reduce_green", "INT-0004", 2, 23.0)],
        False,
        [("R2", "INT-0004: pedestrian service needs 8.0s walk + 20.0s FDW "
                "(70ft at 3.5ft/s); max through window is 18.0s."),
         ("R5", "INT-0004: cycle 44.0s outside 60\u2013180s.")]),
    "r3_fail_45mph_yellow": (
        "INT-0002", [Operation("extend_green", "INT-0002", 1, 5.0)],
        False,
        [("R3", "INT-0002 phase 1: yellow 4.0s < kinematic requirement 4.3s "
                "(45 mph, +0.0% grade)."),
         ("R3", "INT-0002 phase 2: yellow 4.0s < kinematic requirement 4.3s "
                "(45 mph, +0.0% grade).")]),
    "r3_pass_30mph_yellow": (
        "INT-0001", [Operation("extend_green", "INT-0001", 1, 5.0)],
        True, []),
    "r4_fail_conflicting_greens": (
        "INT-0003", [Operation("extend_green", "INT-0003", 1, 10.0)],
        False,
        [("R4", "INT-0003: conflicting phases 1 and 2 need 112.0s combined "
                "service but the cycle is only 100s \u2014 greens would be "
                "forced to overlap.")]),
    "r5_fail_cycle_too_long": (
        "INT-0001", [Operation("extend_green", "INT-0001", 1, 95.0)],
        False,
        [("R5", "INT-0001 phase 1: duration 130.0s > 120s."),
         ("R5", "INT-0001: cycle 185.0s outside 60\u2013180s.")]),
    "r5_fail_cycle_too_short": (
        "INT-0001", [Operation("adjust_cycle", "INT-0001", 1, -35.0)],
        False,
        [("R4", "INT-0001: conflicting phases 1 and 2 need 82.0s combined "
                "service but the cycle is only 55s \u2014 greens would be "
                "forced to overlap."),
         ("R5", "INT-0001: cycle 55.0s outside 60\u2013180s.")]),
    "r5_fail_phase_below_floor": (
        "INT-0001", [Operation("reduce_green", "INT-0001", 1, 27.0)],
        False,
        [("R5", "INT-0001 phase 1: duration 8.0s < 10s.")]),
}


class TestVerdictParity(unittest.TestCase):
    """Same plans, identical verify() verdicts before/after the refactor."""

    def test_verdicts_identical_to_pre_refactor_baseline(self):
        for name, (target, ops, expect_passed,
                   expect_violations) in PARITY_CASES.items():
            graph = make_parity_graph()
            verifier = ConstraintVerifier(graph)
            result = verifier.verify(
                make_plan([target], ops, graph=graph))
            got = sorted((v.rule_id, v.message) for v in result.violations)
            self.assertEqual(result.passed, expect_passed, name)
            self.assertEqual(got, sorted(expect_violations), name)


class TestStatefulRulesSurviveRefactor(unittest.TestCase):
    """R6 (_active_changes) and R7 (EMS corridors) stay in the stateful core."""

    def test_r6_system_wide_limit_still_fires(self):
        graph = make_parity_graph()
        verifier = ConstraintVerifier(graph)
        for i in range(5):
            verifier.register_active_change(f"INT-X{i}", f"PLAN-{i}")
        result = verifier.verify(make_plan(
            ["INT-0001"], [Operation("extend_green", "INT-0001", 1, 10.0)],
            graph=graph))
        self.assertFalse(result.passed)
        self.assertTrue(any(v.rule_id == "R6" for v in result.violations))

    def test_r6_per_intersection_still_fires(self):
        graph = make_parity_graph()
        verifier = ConstraintVerifier(graph)
        verifier.register_active_change("INT-0001", "PLAN-OTHER")
        result = verifier.verify(make_plan(
            ["INT-0001"], [Operation("extend_green", "INT-0001", 1, 10.0)],
            graph=graph))
        self.assertTrue(any(v.rule_id == "R6" for v in result.violations))

    def test_r7_ems_corridor_still_fires(self):
        graph = make_parity_graph()
        graph.add_incident(Incident(
            id="INC-EMS", type=IncidentType.COLLISION,
            intersection_id="INT-0001", severity=0.9,
            status_flag=IncidentStatusFlag.EMS_RESPONDING))
        verifier = ConstraintVerifier(graph)
        result = verifier.verify(make_plan(
            ["INT-0001"], [Operation("reduce_green", "INT-0001", 1, 5.0)],
            graph=graph))
        self.assertTrue(any(v.rule_id == "R7" for v in result.violations))


class TestWorkZoneRulepack(unittest.TestCase):
    """MUTCD Chapter 6 temporary work-zone rulepack proves generality."""

    def test_valid_workzone_timing_passes_all_rules(self):
        verdicts = run_rulepack(make_workzone_timing(), "workzone")
        self.assertTrue(all(v["passed"] for v in verdicts))

    def test_short_green_fails_wz1(self):
        verdicts = run_rulepack(
            make_workzone_timing(green_s=8.0), "workzone")
        wz1 = next(v for v in verdicts if v["rule_id"] == "WZ1")
        self.assertFalse(wz1["passed"])
        self.assertTrue(wz1["violations"])

    def test_long_cycle_fails_wz2(self):
        verdicts = run_rulepack(
            make_workzone_timing(cycle_s=150.0), "workzone")
        wz2 = next(v for v in verdicts if v["rule_id"] == "WZ2")
        self.assertFalse(wz2["passed"])

    def test_short_all_red_fails_wz3(self):
        verdicts = run_rulepack(
            make_workzone_timing(red_clearance_s=1.0), "workzone")
        wz3 = next(v for v in verdicts if v["rule_id"] == "WZ3")
        self.assertFalse(wz3["passed"])

    def test_sub_kinematic_yellow_fails_wz4(self):
        verdicts = run_rulepack(
            make_workzone_timing(yellow_s=3.0, approach_mph=45.0), "workzone")
        wz4 = next(v for v in verdicts if v["rule_id"] == "WZ4")
        self.assertFalse(wz4["passed"])


class TestRunRulepackStateless(unittest.TestCase):
    """run_rulepack is a pure module-level entry point — no CityGraph."""

    def test_runs_without_graph_or_verifier(self):
        verdicts = run_rulepack(make_workzone_timing(), "mutcd")
        self.assertEqual(len(verdicts), len(RULEPACKS["mutcd"].rules))
        self.assertTrue(all(v["passed"] for v in verdicts))

    def test_default_rulepack_is_mutcd(self):
        timing = make_workzone_timing(green_s=5.0)
        default_verdicts = run_rulepack(timing)
        explicit_verdicts = run_rulepack(timing, "mutcd")
        self.assertEqual(default_verdicts, explicit_verdicts)
        r1 = next(v for v in default_verdicts if v["rule_id"] == "R1")
        self.assertFalse(r1["passed"])

    def test_verdict_dict_shape(self):
        for verdict in run_rulepack(make_workzone_timing(), "mutcd"):
            self.assertIsInstance(verdict["rule_id"], str)
            self.assertIsInstance(verdict["description"], str)
            self.assertIsInstance(verdict["passed"], bool)
            self.assertIsInstance(verdict["violations"], list)

    def test_unknown_rulepack_raises_keyerror(self):
        with self.assertRaises(KeyError):
            run_rulepack(make_workzone_timing(), "no-such-pack")


class TestRulepackVersion(unittest.TestCase):
    """Version string + content hash consumed by the certificate engine."""

    def test_version_is_deterministic_string(self):
        v1 = rulepack_version("mutcd")
        v2 = rulepack_version("mutcd")
        self.assertIsInstance(v1, str)
        self.assertEqual(v1, v2)

    def test_versions_differ_between_rulepacks(self):
        self.assertNotEqual(rulepack_version("mutcd"),
                            rulepack_version("workzone"))

    def test_version_embeds_name_and_content_hash(self):
        v = rulepack_version("mutcd")
        self.assertIn("mutcd", v)
        # trailing content hash: 12 hex chars of sha256 over rule params
        self.assertRegex(v, r"[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
