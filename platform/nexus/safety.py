"""
Nexus City OS — AI Grounding & Safety Architecture (PRD §4).

This module is the independent secondary validation layer. It runs AFTER
plan generation and BEFORE any plan reaches an operator. It enforces:

  * MUTCD Chapter 4D / 4E physical constraints (PRD §4.4) — guardrail rules
    R1–R7 from MASTER_PROMPT Pipeline C.
  * Hallucination monitoring (PRD §4.5).
  * Mandatory provenance suppression (PRD §4.2).
  * Confidence threshold abstention with governed range (PRD §4.3).

Every block is recorded with the violated rule ID so the safety metrics
(hallucination block rate, constraint block rate) can be tracked separately.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .graph import CityGraph
from .models import (
    ActionPlan,
    PlanStatus,
    SignalTimingPlan,
    now_ts,
)

# ---------------------------------------------------------------------------
# MUTCD constants (PRD §4.4)
# ---------------------------------------------------------------------------
MIN_GREEN_THROUGH_S = 7.0          # MUTCD 4D.26
MIN_GREEN_LEFT_S = 4.0             # MUTCD 4D.26
MIN_PED_WALK_S = 7.0               # MUTCD 4E.06
PED_WALK_SPEED_FT_S = 3.5          # MUTCD 4E.06
PED_WALK_SPEED_SLOW_FT_S = 3.0     # near schools / senior centers
MIN_YELLOW_S = 3.0                 # MUTCD 4D.26 absolute band floor
MAX_YELLOW_S = 6.0                 # MUTCD 4D.26 absolute band ceiling
PERCEPTION_REACTION_TIME_S = 1.0   # ITE kinematic yellow-change (ADR-001)
DECELERATION_RATE_M_S2 = 3.05      # ITE comfortable deceleration rate
GRAVITY_M_S2 = 9.81
MPH_TO_M_S = 0.44704
MIN_CYCLE_S = 60.0
MAX_CYCLE_S = 180.0
MIN_PHASE_S = 10.0
MAX_PHASE_S = 120.0

DEFAULT_CONFIDENCE_THRESHOLD = 70.0
CONFIDENCE_THRESHOLD_MIN = 50.0    # governed range (PRD §4.3)
CONFIDENCE_THRESHOLD_MAX = 95.0

ABSTENTION_MESSAGE = ("Insufficient data confidence to recommend an action. "
                      "Manual assessment recommended.")


@dataclass
class Violation:
    rule_id: str
    message: str


@dataclass
class VerificationResult:
    passed: bool
    violations: List[Violation] = field(default_factory=list)

    def reason(self) -> str:
        return "; ".join(f"[{v.rule_id}] {v.message}" for v in self.violations)


def required_yellow_s(approach_speed_mph: float, grade_pct: float) -> float:
    # ITE kinematic yellow change: y = t + v / (2*(a + G*g)), floored at the
    # MUTCD 4D.26 3.0s band minimum (ADR-001). G = grade as a decimal.
    v_m_s = approach_speed_mph * MPH_TO_M_S
    # Steep downgrades could zero the denominator; 0.5 m/s² floor guards it.
    braking = max(0.5, DECELERATION_RATE_M_S2
                  + (grade_pct / 100.0) * GRAVITY_M_S2)
    return max(MIN_YELLOW_S, PERCEPTION_REACTION_TIME_S + v_m_s / (2 * braking))


def required_fdw_s(crosswalk_length_ft: float, walk_speed_ft_s: float) -> float:
    # Flashing-don't-walk pedestrian change interval (MUTCD 4E.06): full
    # crosswalk length at the design walking speed, rounded UP to a whole
    # second (controllers time in integer seconds; never round down safety).
    return float(math.ceil(crosswalk_length_ft / walk_speed_ft_s))


def apply_operations_to_plan(timing: SignalTimingPlan,
                             operations, ) -> SignalTimingPlan:
    """Return a copy of ``timing`` with the plan's operations applied.
    Raises KeyError for unknown phases (caught upstream as hallucination)."""
    new_plan = timing.copy()
    phase_index = {p.phase_id: p for p in new_plan.phases}
    for op in operations:
        if op.phase_id not in phase_index:
            raise KeyError(f"Unknown phase {op.phase_id} "
                           f"on {timing.intersection_id}")
        phase = phase_index[op.phase_id]
        if op.type == "extend_green":
            phase.green_seconds += abs(op.delta_seconds)
            new_plan.cycle_seconds += abs(op.delta_seconds)
        elif op.type == "reduce_green":
            phase.green_seconds -= abs(op.delta_seconds)
            new_plan.cycle_seconds -= abs(op.delta_seconds)
        elif op.type == "adjust_cycle":
            new_plan.cycle_seconds += op.delta_seconds
        else:
            raise ValueError(f"Unknown operation type: {op.type}")
    return new_plan


class ConstraintVerifier:
    """Independent MUTCD physical-constraint verifier (PRD §4.4).

    Tracks system-wide active changes (R6) under a lock.
    """

    def __init__(self, graph: CityGraph, max_concurrent_changes: int = 5) -> None:
        self._graph = graph
        self._lock = threading.RLock()
        self.max_concurrent_changes = max_concurrent_changes
        # intersection_id -> plan_id with an active (executed, unreverted) change
        self._active_changes: Dict[str, str] = {}

    # -- active change bookkeeping (R6) --------------------------------

    def register_active_change(self, intersection_id: str, plan_id: str) -> None:
        with self._lock:
            self._active_changes[intersection_id] = plan_id

    def clear_active_change(self, intersection_id: str) -> None:
        with self._lock:
            self._active_changes.pop(intersection_id, None)

    def active_change_count(self) -> int:
        with self._lock:
            return len(self._active_changes)

    def active_changes(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._active_changes)

    # -- verification ----------------------------------------------------

    def verify(self, plan: ActionPlan) -> VerificationResult:
        violations: List[Violation] = []

        with self._lock:
            active = dict(self._active_changes)

        # R6a: system-wide concurrent change limit
        new_targets = [t for t in plan.targets if t not in active]
        if len(active) + len(new_targets) > self.max_concurrent_changes:
            violations.append(Violation(
                "R6", f"System-wide concurrent change limit "
                      f"({self.max_concurrent_changes}) would be exceeded "
                      f"({len(active)} active)."))

        # R6b: one concurrent change per intersection
        for target in plan.targets:
            if target in active and active[target] != plan.plan_id:
                violations.append(Violation(
                    "R6", f"{target} already has an active timing change "
                          f"({active[target]})."))

        ems_protected = self._graph.active_ems_intersections()

        for target in plan.targets:
            try:
                inter = self._graph.get_intersection(target)
            except KeyError:
                # Existence is the hallucination monitor's job; skip here.
                continue

            ops = [o for o in plan.operations if o.intersection_id == target]

            # R7: EMS corridor green-priority protection
            if target in ems_protected:
                for op in ops:
                    if op.type == "reduce_green" or (
                            op.type == "adjust_cycle" and op.delta_seconds < 0):
                        violations.append(Violation(
                            "R7", f"Cannot reduce green priority at {target}: "
                                  f"active EMS_RESPONDING corridor."))
                        break

            try:
                proposed = apply_operations_to_plan(inter.timing_plan, ops)
            except (KeyError, ValueError) as exc:
                violations.append(Violation("R0", f"Invalid operation: {exc}"))
                continue

            violations.extend(self._verify_timing(proposed))

        return VerificationResult(passed=not violations, violations=violations)

    def _verify_timing(self, plan: SignalTimingPlan) -> List[Violation]:
        v: List[Violation] = []
        iid = plan.intersection_id

        # R5: cycle length bounds
        if not (MIN_CYCLE_S <= plan.cycle_seconds <= MAX_CYCLE_S):
            v.append(Violation(
                "R5", f"{iid}: cycle {plan.cycle_seconds:.1f}s outside "
                      f"{MIN_CYCLE_S:.0f}–{MAX_CYCLE_S:.0f}s."))

        for phase in plan.phases:
            # R1: minimum green
            min_green = (MIN_GREEN_LEFT_S if phase.movement == "left_turn"
                         else MIN_GREEN_THROUGH_S)
            if phase.green_seconds < min_green:
                v.append(Violation(
                    "R1", f"{iid} phase {phase.phase_id}: green "
                          f"{phase.green_seconds:.1f}s < minimum "
                          f"{min_green:.0f}s ({phase.movement})."))

            # R5: phase duration bounds
            if not (MIN_PHASE_S <= phase.green_seconds <= MAX_PHASE_S):
                # Only flag the upper bound here (lower bound handled by R1
                # for sub-minimum greens; phases between min-green and 10s
                # still violate the 10s phase floor).
                if phase.green_seconds > MAX_PHASE_S:
                    v.append(Violation(
                        "R5", f"{iid} phase {phase.phase_id}: duration "
                              f"{phase.green_seconds:.1f}s > {MAX_PHASE_S:.0f}s."))
                elif phase.green_seconds >= min_green:
                    v.append(Violation(
                        "R5", f"{iid} phase {phase.phase_id}: duration "
                              f"{phase.green_seconds:.1f}s < {MIN_PHASE_S:.0f}s."))

            # R3: yellow change interval — ITE kinematic requirement per
            # phase approach speed + grade (ADR-001), within the MUTCD band.
            min_yellow = required_yellow_s(phase.approach_speed_mph,
                                           phase.grade_pct)
            if phase.yellow_seconds < min_yellow:
                v.append(Violation(
                    "R3", f"{iid} phase {phase.phase_id}: yellow "
                          f"{phase.yellow_seconds:.1f}s < kinematic "
                          f"requirement {min_yellow:.1f}s "
                          f"({phase.approach_speed_mph:.0f} mph, "
                          f"{phase.grade_pct:+.1f}% grade)."))
            elif phase.yellow_seconds > MAX_YELLOW_S:
                v.append(Violation(
                    "R3", f"{iid} phase {phase.phase_id}: yellow "
                          f"{phase.yellow_seconds:.1f}s > "
                          f"{MAX_YELLOW_S:.1f}s maximum."))

        # R2: pedestrian intervals
        if plan.pedestrian_walk_seconds < MIN_PED_WALK_S:
            v.append(Violation(
                "R2", f"{iid}: pedestrian walk "
                      f"{plan.pedestrian_walk_seconds:.1f}s < "
                      f"{MIN_PED_WALK_S:.0f}s."))
        walk_speed = (PED_WALK_SPEED_SLOW_FT_S
                      if plan.near_school_or_senior_center
                      else PED_WALK_SPEED_FT_S)
        fdw = required_fdw_s(plan.crosswalk_length_ft, walk_speed)
        # WALK + flashing-don't-walk must fit within the parallel through
        # phase's service window (green + yellow + red clearance, MUTCD 4E.06).
        through_windows = [p.green_seconds + p.yellow_seconds
                           + p.red_clearance_seconds
                           for p in plan.phases if p.movement == "through"]
        if through_windows and (max(through_windows)
                                < plan.pedestrian_walk_seconds + fdw):
            v.append(Violation(
                "R2", f"{iid}: pedestrian service needs "
                      f"{plan.pedestrian_walk_seconds:.1f}s walk + "
                      f"{fdw:.1f}s FDW "
                      f"({plan.crosswalk_length_ft:.0f}ft at "
                      f"{walk_speed:.1f}ft/s); max through window is "
                      f"{max(through_windows):.1f}s."))

        # R4: ring-and-barrier conflict model — phases in each other's
        # conflicts_with (treated symmetrically) must be sequenceable: their
        # combined service times (green + yellow + red clearance) must fit
        # the cycle, otherwise the controller would be forced to run
        # conflicting greens simultaneously.
        service_by_phase = {p.phase_id: p.green_seconds + p.yellow_seconds
                            + p.red_clearance_seconds for p in plan.phases}
        conflict_pairs: Set[Tuple[int, int]] = set()
        for phase in plan.phases:
            for other_id in phase.conflicts_with:
                if other_id in service_by_phase:
                    conflict_pairs.add(
                        (min(phase.phase_id, other_id),
                         max(phase.phase_id, other_id)))
        for a, b in sorted(conflict_pairs):
            if service_by_phase[a] + service_by_phase[b] > plan.cycle_seconds:
                v.append(Violation(
                    "R4", f"{iid}: conflicting phases {a} and {b} need "
                          f"{service_by_phase[a] + service_by_phase[b]:.1f}s "
                          f"combined service but the cycle is only "
                          f"{plan.cycle_seconds:.0f}s — greens would be "
                          f"forced to overlap."))
        return v


class HallucinationMonitor:
    """Blocks plans referencing entities absent from the city model or data
    outside the valid time window (PRD §4.5)."""

    MAX_DATA_AGE_S = 30 * 60.0  # data older than 30 min is outside the window

    def __init__(self, graph: CityGraph) -> None:
        self._graph = graph

    def check(self, plan: ActionPlan) -> VerificationResult:
        violations: List[Violation] = []
        for target in plan.targets:
            if not self._graph.has_intersection(target):
                violations.append(Violation(
                    "H1", f"Plan references non-existent intersection {target}."))
        for entity_id in plan.provenance.entities:
            if not self._graph.entity_exists(entity_id):
                violations.append(Violation(
                    "H2", f"Provenance cites unknown entity {entity_id}."))
        now = now_ts()
        for src in plan.provenance.data_sources:
            ts = src.get("timestamp")
            if ts is None or not isinstance(ts, (int, float)):
                violations.append(Violation(
                    "H3", f"Data source {src.get('source')} lacks a timestamp."))
            elif now - float(ts) > self.MAX_DATA_AGE_S:
                violations.append(Violation(
                    "H3", f"Data source {src.get('source')} timestamp is "
                          f"outside the valid time window."))
        for op in plan.operations:
            if op.intersection_id not in plan.targets:
                violations.append(Violation(
                    "H4", f"Operation targets {op.intersection_id} which is "
                          f"not in the plan's target list."))
        return VerificationResult(passed=not violations, violations=violations)


@dataclass
class SafetyMetrics:
    """Block-rate metrics tracked separately (PRD §4.5)."""
    generated: int = 0
    blocked_hallucination: int = 0
    blocked_constraint: int = 0
    suppressed_provenance: int = 0
    withheld_confidence: int = 0

    def as_dict(self) -> Dict[str, float]:
        total = max(1, self.generated)
        return {
            "generated": self.generated,
            "blocked_hallucination": self.blocked_hallucination,
            "blocked_constraint": self.blocked_constraint,
            "suppressed_provenance": self.suppressed_provenance,
            "withheld_confidence": self.withheld_confidence,
            "hallucination_block_rate_pct": round(
                100.0 * self.blocked_hallucination / total, 2),
            "constraint_block_rate_pct": round(
                100.0 * self.blocked_constraint / total, 2),
            "combined_block_rate_pct": round(
                100.0 * (self.blocked_hallucination + self.blocked_constraint)
                / total, 2),
        }


class SafetyGate:
    """The full pre-operator safety pipeline (PRD §4.2–4.5), in order:

      1. Provenance completeness  → SUPPRESSED_PROVENANCE
      2. Hallucination check      → BLOCKED_HALLUCINATION
      3. MUTCD constraint check   → BLOCKED_CONSTRAINT
      4. Confidence threshold     → WITHHELD_CONFIDENCE
      5. Otherwise                → PENDING_APPROVAL
    """

    def __init__(self, graph: CityGraph,
                 max_concurrent_changes: int = 5) -> None:
        self._lock = threading.RLock()
        self.verifier = ConstraintVerifier(graph, max_concurrent_changes)
        self.monitor = HallucinationMonitor(graph)
        self.metrics = SafetyMetrics()
        self._confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD

    # -- governed confidence threshold (PRD §4.3) -----------------------

    @property
    def confidence_threshold(self) -> float:
        with self._lock:
            return self._confidence_threshold

    def set_confidence_threshold(self, value: float, actor_role: str) -> None:
        if actor_role != "admin":
            raise PermissionError(
                "Only the Admin role can adjust the confidence threshold.")
        if not (CONFIDENCE_THRESHOLD_MIN <= value <= CONFIDENCE_THRESHOLD_MAX):
            raise ValueError(
                f"Threshold must be within the governed range "
                f"{CONFIDENCE_THRESHOLD_MIN:.0f}–{CONFIDENCE_THRESHOLD_MAX:.0f}.")
        with self._lock:
            self._confidence_threshold = float(value)

    # -- the gate --------------------------------------------------------

    def evaluate(self, plan: ActionPlan) -> ActionPlan:
        with self._lock:
            self.metrics.generated += 1

        # 1. Provenance (PRD §4.2) — suppressed, never shown to operators.
        if not plan.provenance.is_complete():
            plan.status = PlanStatus.SUPPRESSED_PROVENANCE
            plan.block_reason = "Incomplete provenance (PRD §4.2)."
            with self._lock:
                self.metrics.suppressed_provenance += 1
            return plan

        # 2. Hallucination (PRD §4.5)
        h = self.monitor.check(plan)
        if not h.passed:
            plan.status = PlanStatus.BLOCKED_HALLUCINATION
            plan.block_reason = h.reason()
            with self._lock:
                self.metrics.blocked_hallucination += 1
            return plan

        # 3. MUTCD constraints (PRD §4.4)
        c = self.verifier.verify(plan)
        if not c.passed:
            plan.status = PlanStatus.BLOCKED_CONSTRAINT
            plan.block_reason = c.reason()
            with self._lock:
                self.metrics.blocked_constraint += 1
            return plan

        # 4. Confidence abstention (PRD §4.3)
        if plan.confidence.composite < self.confidence_threshold:
            plan.status = PlanStatus.WITHHELD_CONFIDENCE
            plan.block_reason = ABSTENTION_MESSAGE
            with self._lock:
                self.metrics.withheld_confidence += 1
            return plan

        plan.status = PlanStatus.PENDING_APPROVAL
        return plan