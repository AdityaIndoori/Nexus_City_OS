"""
Kinematic MUTCD correctness suite (E1, ADR-001).

Covers the ITE kinematic yellow-change interval (replacing the fixed 3–6s
band), the true flashing-don't-walk pedestrian change interval (MUTCD 4E),
the ring-and-barrier R4 conflict model, and grade_pct survival through
SignalTimingPlan.copy().
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.graph import CityGraph
from nexus.models import Intersection, Operation, PlanStatus, SignalPhase, SignalTimingPlan
from nexus.safety import (
    ConstraintVerifier,
    PED_WALK_SPEED_FT_S,
    SafetyGate,
    required_fdw_s,
    required_yellow_s,
)
from nexus.adapters import default_timing_plan
from tests.test_safety import make_plan


def make_timing(yellow_s: float = 4.0, approach_mph: float = 30.0,
                grade_pct: float = 0.0, green_s: float = 35.0,
                cycle_s: float = 90.0,
                phases=None) -> SignalTimingPlan:
    if phases is None:
        phases = [
            SignalPhase(1, "through", green_s, yellow_s, 2.0,
                        approach_mph, [2], grade_pct),
            SignalPhase(2, "through", green_s, yellow_s, 2.0,
                        approach_mph, [1], grade_pct),
        ]
    return SignalTimingPlan(
        plan_id="STP-TEST", intersection_id="INT-TEST",
        cycle_seconds=cycle_s, phases=phases,
        pedestrian_walk_seconds=8.0, crosswalk_length_ft=60.0)


def verify_timing(plan: SignalTimingPlan):
    return ConstraintVerifier(CityGraph())._verify_timing(plan)


class TestKinematicYellow(unittest.TestCase):
    """ITE yellow-change formula y = t + v / (2(a + G·g)) (ADR-001)."""

    def test_faster_uphill_needs_more_yellow_than_slow_flat(self):
        self.assertGreaterEqual(required_yellow_s(45.0, 3.0),
                                required_yellow_s(25.0, 0.0))

    def test_45mph_kinematic_value_matches_formula(self):
        # 45 mph = 20.1168 m/s; y = 1.0 + 20.1168 / (2 * 3.05) ≈ 4.30s
        self.assertAlmostEqual(required_yellow_s(45.0, 0.0),
                               1.0 + 45.0 * 0.44704 / (2 * 3.05), places=3)

    def test_4s_yellow_at_45mph_fails_r3(self):
        violations = verify_timing(make_timing(yellow_s=4.0,
                                               approach_mph=45.0))
        self.assertTrue(any(v.rule_id == "R3" for v in violations))

    def test_4s_yellow_at_30mph_flat_passes(self):
        # Guards the 163-green constraint: adapter defaults (4.0s @ 30 mph)
        # must remain compliant (kinematic requirement ≈ 3.2s).
        violations = verify_timing(make_timing(yellow_s=4.0,
                                               approach_mph=30.0))
        self.assertFalse(any(v.rule_id == "R3" for v in violations))

    def test_gate_verdict_names_r3_for_45mph_plan(self):
        g = CityGraph()
        g.add_intersection(Intersection(
            id="INT-0001", name="Fast Approach", lat=47.6, lon=-122.33,
            monitored=True,
            timing_plan=default_timing_plan("INT-0001",
                                            approach_speed_mph=45.0)))
        gate = SafetyGate(g)
        plan = make_plan(
            ["INT-0001"],
            [Operation("extend_green", "INT-0001", 1, 5.0)],
            graph=g)
        result = gate.evaluate(plan)
        self.assertEqual(result.status, PlanStatus.BLOCKED_CONSTRAINT)
        self.assertIn("R3", result.block_reason)


class TestFlashingDontWalk(unittest.TestCase):
    """True FDW pedestrian change interval (MUTCD 4E.06)."""

    def test_fdw_60ft_at_least_ceil_of_crossing_time(self):
        self.assertGreaterEqual(required_fdw_s(60.0, PED_WALK_SPEED_FT_S),
                                math.ceil(60.0 / 3.5))

    def test_walk_plus_fdw_exceeding_service_window_fails_r2(self):
        # Greens cut to 12s: window 12+4+2=18s < walk 8s + FDW 20s (70ft).
        plan = make_timing(green_s=12.0, cycle_s=60.0)
        plan.crosswalk_length_ft = 70.0
        violations = verify_timing(plan)
        self.assertTrue(any(v.rule_id == "R2" for v in violations))

    def test_default_plan_ped_interval_passes(self):
        violations = verify_timing(make_timing())
        self.assertFalse(any(v.rule_id == "R2" for v in violations))


class TestRingAndBarrier(unittest.TestCase):
    """R4: mutually conflicting phases must be sequenceable in the cycle."""

    def test_conflicting_phases_that_cannot_fit_rejected(self):
        # Two conflicting phases at 50+4+2=56s each: 112s > 90s cycle —
        # they would be forced green simultaneously.
        violations = verify_timing(make_timing(green_s=50.0))
        self.assertTrue(any(v.rule_id == "R4" for v in violations))

    def test_valid_barrier_crossing_passes(self):
        # NEMA-style dual ring: ring 1 = {1, 2}, ring 2 = {3, 4}.
        # 1‖3 and 2‖4 are concurrent (non-conflicting) across the barrier;
        # all other pairs conflict. Total per-ring time 82s fits the 90s cycle.
        phases = [
            SignalPhase(1, "through", 35.0, 4.0, 2.0, 30.0, [2, 4], 0.0),
            SignalPhase(2, "through", 35.0, 4.0, 2.0, 30.0, [1, 3], 0.0),
            SignalPhase(3, "through", 35.0, 4.0, 2.0, 30.0, [2, 4], 0.0),
            SignalPhase(4, "through", 35.0, 4.0, 2.0, 30.0, [1, 3], 0.0),
        ]
        violations = verify_timing(make_timing(phases=phases))
        self.assertFalse(any(v.rule_id == "R4" for v in violations))

    def test_one_way_conflict_listing_treated_as_symmetric(self):
        # Phase 2 forgets to list phase 1; the conflict must still bind.
        phases = [
            SignalPhase(1, "through", 50.0, 4.0, 2.0, 30.0, [2], 0.0),
            SignalPhase(2, "through", 50.0, 4.0, 2.0, 30.0, [], 0.0),
        ]
        violations = verify_timing(make_timing(phases=phases))
        self.assertTrue(any(v.rule_id == "R4" for v in violations))


class TestGradePctField(unittest.TestCase):
    """grade_pct must survive positional reconstruction in copy()."""

    def test_copy_roundtrip_preserves_grade_pct(self):
        plan = make_timing(grade_pct=3.5)
        dup = plan.copy()
        for original, copied in zip(plan.phases, dup.phases):
            self.assertEqual(copied.grade_pct, original.grade_pct)
            self.assertEqual(copied.approach_speed_mph,
                             original.approach_speed_mph)
            self.assertEqual(copied.conflicts_with, original.conflicts_with)

    def test_grade_pct_defaults_to_zero(self):
        phase = SignalPhase(1, "through", 35.0, 4.0, 2.0, 30.0)
        self.assertEqual(phase.grade_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
