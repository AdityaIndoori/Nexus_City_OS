"""
Adversarial fuzz harness for the SafetyGate (ADR-002).

Generates N mutated / malformed / hostile plans from a FIXED seed and runs
every one through ``SafetyGate.evaluate``, asserting ZERO unsafe plans reach
PENDING_APPROVAL. "Unsafe" is decided by an INDEPENDENT oracle in this file
that recomputes the R1–R5 MUTCD bounds from scratch (own constants, own
arithmetic — never calling the verifier) and flags references to unknown
entities.

Scale via env var (no code change):
    NEXUS_FUZZ_N   number of plans (default 50000 — runs in the normal
                   discover pass in a few seconds; nightly CI can set
                   1000000 for the deep sweep).
"""
from __future__ import annotations

import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.adapters import default_timing_plan
from nexus.graph import CityGraph
from nexus.models import (
    ActionPlan,
    ConfidenceBreakdown,
    Intersection,
    MODEL_VERSION,
    Operation,
    PlanStatus,
    Provenance,
    now_ts,
)
from nexus.safety import SafetyGate

FUZZ_N = int(os.environ.get("NEXUS_FUZZ_N", "50000"))
FUZZ_SEED = 20260714

# Independent MUTCD bounds (MUTCD 4D.26 / 4E.06 / ITE) — deliberately
# restated here, NOT imported from nexus.safety, so a regression in the
# verifier's constants cannot silently pass its own fuzz.
O_MIN_GREEN_THROUGH_S = 7.0
O_MIN_GREEN_LEFT_S = 4.0
O_MIN_PHASE_S = 10.0
O_MAX_PHASE_S = 120.0
O_MIN_CYCLE_S = 60.0
O_MAX_CYCLE_S = 180.0
O_MIN_YELLOW_S = 3.0
O_MAX_YELLOW_S = 6.0
O_MIN_PED_WALK_S = 7.0
O_PED_SPEED_FT_S = 3.5
O_PED_SPEED_SLOW_FT_S = 3.0
O_MPH_TO_M_S = 0.44704
O_DECEL_M_S2 = 3.05
O_GRAVITY_M_S2 = 9.81
O_REACTION_S = 1.0

KNOWN_IDS = [f"INT-{i:04d}" for i in range(1, 5)]
KNOWN_PHASES = [1, 2]
OP_TYPES = ["extend_green", "reduce_green", "adjust_cycle"]
HOSTILE_DELTAS = [1e6, -1e6, 1e12, -273.15, 0.0,
                  float("nan"), float("inf"), float("-inf")]
UNKNOWN_IDS = ["INT-9999", "INT-💀", "'; DROP TABLE plans;--", "", "INT-0001 "]


def make_graph() -> CityGraph:
    g = CityGraph()
    for iid in KNOWN_IDS:
        g.add_intersection(Intersection(
            id=iid, name=f"Fuzz {iid}", lat=47.6, lon=-122.33,
            monitored=True, timing_plan=default_timing_plan(iid)))
    return g


def gen_plan(rng: random.Random, i: int) -> ActionPlan:
    """One mutated/hostile plan. Mutation class chosen by the rng."""
    now = now_ts()
    targets = [rng.choice(KNOWN_IDS)]
    ops = []
    n_ops = rng.randint(1, 3)
    for _ in range(n_ops):
        op_type = rng.choice(OP_TYPES)
        iid = targets[0]
        phase = rng.choice(KNOWN_PHASES)
        delta = rng.uniform(1.0, 25.0)
        roll = rng.random()
        if roll < 0.15:       # hostile magnitude / nan / inf
            delta = rng.choice(HOSTILE_DELTAS)
        elif roll < 0.25:     # negative / huge cycle adjustment
            op_type = "adjust_cycle"
            delta = rng.uniform(-500.0, 500.0)
        elif roll < 0.32:     # unknown intersection on the op
            iid = rng.choice(UNKNOWN_IDS)
        elif roll < 0.39:     # unknown phase
            phase = rng.choice([0, 3, 99, -1])
        elif roll < 0.44:     # unknown operation type
            op_type = rng.choice(["set_all_green", "disable_signal", ""])
        elif roll < 0.52:     # conflicting-greens squeeze: shrink the cycle
            op_type = "adjust_cycle"
            delta = -rng.uniform(10.0, 40.0)
        ops.append(Operation(op_type, iid, phase, delta))
    roll = rng.random()
    if roll < 0.08:           # unknown target intersection
        targets = [rng.choice(UNKNOWN_IDS)]
    elif roll < 0.12:         # op outside the target list (H4)
        ops.append(Operation("extend_green",
                             rng.choice([t for t in KNOWN_IDS
                                         if t not in targets]), 1, 5.0))
    if rng.random() < 0.08:   # missing provenance
        prov = Provenance(entities=[], data_sources=[], weather=None,
                          rationale="")
    elif rng.random() < 0.08:  # provenance citing unknown entities
        prov = Provenance(
            entities=[rng.choice(UNKNOWN_IDS)],
            data_sources=[{"source": "camera", "timestamp": now - 5.0}],
            weather={"condition": "clear"}, rationale="fuzz")
    else:
        prov = Provenance(
            entities=list(targets),
            data_sources=[{"source": "camera", "timestamp": now - 5.0}],
            weather={"condition": "clear"}, rationale="fuzz")
    return ActionPlan(
        plan_id=f"PLAN-FUZZ{i:07d}",
        created_at=now,
        model_version=MODEL_VERSION,
        incident_id="INC-FUZZ",
        targets=targets,
        operations=ops,
        justification="fuzz",
        provenance=prov,
        # confidence pinned high so abstention can never mask a rule miss
        confidence=ConfidenceBreakdown(90.0, 90.0, 90.0, 90.0),
    )


def oracle_unsafe(plan: ActionPlan, graph: CityGraph) -> bool:
    """Independent recomputation: True iff the plan violates any R1–R5
    bound or references unknown entities/phases/operations."""
    for t in plan.targets:
        if not graph.has_intersection(t):
            return True
    for e in plan.provenance.entities:
        if not graph.entity_exists(e):
            return True
    for op in plan.operations:
        if op.intersection_id not in plan.targets:
            return True
    for t in plan.targets:
        inter = graph.get_intersection(t)
        tp = inter.timing_plan
        greens = {p.phase_id: p.green_seconds for p in tp.phases}
        cycle = tp.cycle_seconds
        for op in (o for o in plan.operations if o.intersection_id == t):
            if op.type == "extend_green":
                if op.phase_id not in greens:
                    return True
                greens[op.phase_id] += abs(op.delta_seconds)
                cycle += abs(op.delta_seconds)
            elif op.type == "reduce_green":
                if op.phase_id not in greens:
                    return True
                greens[op.phase_id] -= abs(op.delta_seconds)
                cycle -= abs(op.delta_seconds)
            elif op.type == "adjust_cycle":
                cycle += op.delta_seconds
            else:
                return True
        # R5 cycle bounds (NaN fails this comparison → unsafe, correctly)
        if not (O_MIN_CYCLE_S <= cycle <= O_MAX_CYCLE_S):
            return True
        for p in tp.phases:
            g = greens[p.phase_id]
            min_green = (O_MIN_GREEN_LEFT_S if p.movement == "left_turn"
                         else O_MIN_GREEN_THROUGH_S)
            if not (g >= min_green):          # R1 (NaN-safe phrasing)
                return True
            if not (O_MIN_PHASE_S <= g <= O_MAX_PHASE_S):   # R5 phase
                return True
            v_m_s = p.approach_speed_mph * O_MPH_TO_M_S     # R3 yellow
            braking = max(0.5, O_DECEL_M_S2
                          + (p.grade_pct / 100.0) * O_GRAVITY_M_S2)
            req_y = max(O_MIN_YELLOW_S,
                        O_REACTION_S + v_m_s / (2 * braking))
            if p.yellow_seconds < req_y or p.yellow_seconds > O_MAX_YELLOW_S:
                return True
        # R2 pedestrian service
        if tp.pedestrian_walk_seconds < O_MIN_PED_WALK_S:
            return True
        speed = (O_PED_SPEED_SLOW_FT_S if tp.near_school_or_senior_center
                 else O_PED_SPEED_FT_S)
        fdw = math.ceil(tp.crosswalk_length_ft / speed)
        windows = [greens[p.phase_id] + p.yellow_seconds
                   + p.red_clearance_seconds
                   for p in tp.phases if p.movement == "through"]
        if windows and max(windows) < tp.pedestrian_walk_seconds + fdw:
            return True
        # R4 conflicting greens must be sequenceable within the cycle
        service = {p.phase_id: greens[p.phase_id] + p.yellow_seconds
                   + p.red_clearance_seconds for p in tp.phases}
        for p in tp.phases:
            for other in p.conflicts_with:
                if other in service and (service[p.phase_id]
                                         + service[other] > cycle):
                    return True
    return False


class TestFuzzHarness(unittest.TestCase):

    def test_zero_unsafe_plans_pass_the_gate(self):
        graph = make_graph()
        gate = SafetyGate(graph)
        rng = random.Random(FUZZ_SEED)
        unsafe_passed = []
        passed = unsafe = 0
        for i in range(FUZZ_N):
            plan = gen_plan(rng, i)
            verdict = gate.evaluate(plan)
            is_unsafe = oracle_unsafe(plan, graph)
            unsafe += is_unsafe
            if verdict.status == PlanStatus.PENDING_APPROVAL:
                passed += 1
                if is_unsafe:
                    unsafe_passed.append(plan.plan_id)
        self.assertEqual(
            unsafe_passed, [],
            f"{len(unsafe_passed)} unsafe plan(s) passed the SafetyGate "
            f"out of {FUZZ_N} (first: {unsafe_passed[:5]})")
        # sanity: the harness generated both kinds — it isn't vacuous
        self.assertGreater(unsafe, FUZZ_N // 10)
        self.assertGreater(passed, 0)


if __name__ == "__main__":
    unittest.main()
