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

import threading
from dataclasses import dataclass, field
from typing import Dict, List

from .graph import CityGraph
from .models import (
    ActionPlan,
    PlanStatus,
    SignalTimingPlan,
    now_ts,
)
# MUTCD 4D/4E constants + pure timing rules R1–R5 now live in the
# domain-agnostic rulepack core (TJ-N2, ADR-004); re-exported here so the
# public safety surface is unchanged.
from .rulepacks import (  # noqa: F401 — re-exported public names
    DECELERATION_RATE_M_S2,
    GRAVITY_M_S2,
    MAX_CYCLE_S,
    MAX_PHASE_S,
    MAX_YELLOW_S,
    MIN_CYCLE_S,
    MIN_GREEN_LEFT_S,
    MIN_GREEN_THROUGH_S,
    MIN_PED_WALK_S,
    MIN_PHASE_S,
    MIN_YELLOW_S,
    MPH_TO_M_S,
    PED_WALK_SPEED_FT_S,
    PED_WALK_SPEED_SLOW_FT_S,
    PERCEPTION_REACTION_TIME_S,
    Violation,
    required_fdw_s,
    required_yellow_s,
    run_rules,
)

DEFAULT_CONFIDENCE_THRESHOLD = 70.0
CONFIDENCE_THRESHOLD_MIN = 50.0    # governed range (PRD §4.3)
CONFIDENCE_THRESHOLD_MAX = 95.0

ABSTENTION_MESSAGE = ("Insufficient data confidence to recommend an action. "
                      "Manual assessment recommended.")


@dataclass
class VerificationResult:
    passed: bool
    violations: List[Violation] = field(default_factory=list)

    def reason(self) -> str:
        return "; ".join(f"[{v.rule_id}] {v.message}" for v in self.violations)


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
        # Pure timing rules R1–R5 = the "mutcd" rulepack (TJ-N2, ADR-004).
        return run_rules(plan, "mutcd")


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