"""
Nexus City OS — Verification Rulepacks (TJ-N2, ADR-004).

Domain-agnostic rule core: each rulepack is a declarative list of Rule
dataclasses (NO DSL) evaluated against a SignalTimingPlan. Pure — no graph,
no state, no locks. Rulepack #1 "mutcd" carries the MUTCD Chapter 4D/4E
guardrails R1–R5 (with ADR-001 kinematics); rulepack #2 "workzone" carries
MUTCD Chapter 6 temporary-traffic-control rules, proving the core is
regulation-agnostic. Stateful rules R6/R7 stay in safety.ConstraintVerifier.
Each pack exposes a version string + content hash for the certificate
engine (ADR-002) via rulepack_version().
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Set, Tuple

from .models import SignalTimingPlan

# ---------------------------------------------------------------------------
# MUTCD constants (PRD §4.4) — parameterize rulepack #1
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

# Work-zone constants (MUTCD Chapter 6) — parameterize rulepack #2
WZ_MIN_GREEN_S = 10.0              # MUTCD 6F.84 temporary signal min green
WZ_MAX_CYCLE_S = 120.0             # MUTCD 6C.11 queue/delay cap, lane closure
WZ_MIN_ALL_RED_S = 2.0             # MUTCD 6F.84 opposing-platoon clearance


@dataclass
class Violation:
    rule_id: str
    message: str


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


# ---------------------------------------------------------------------------
# Rule core — declarative dataclasses, pure check functions
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    rule_id: str
    description: str
    check: Callable[[SignalTimingPlan], List[Violation]]
    # Numeric parameters the rule is bound to — hashed into the rulepack
    # content hash so a parameter change changes the certificate identity.
    params: Dict[str, float] = field(default_factory=dict)


@dataclass
class Rulepack:
    name: str
    version: str
    rules: List[Rule]


# -- rulepack #1: MUTCD 4D/4E (R1–R5) ---------------------------------------

def _check_r1_min_green(plan: SignalTimingPlan) -> List[Violation]:
    v: List[Violation] = []
    for phase in plan.phases:
        min_green = (MIN_GREEN_LEFT_S if phase.movement == "left_turn"
                     else MIN_GREEN_THROUGH_S)
        if phase.green_seconds < min_green:
            v.append(Violation(
                "R1", f"{plan.intersection_id} phase {phase.phase_id}: green "
                      f"{phase.green_seconds:.1f}s < minimum "
                      f"{min_green:.0f}s ({phase.movement})."))
    return v


def _check_r2_pedestrian(plan: SignalTimingPlan) -> List[Violation]:
    v: List[Violation] = []
    iid = plan.intersection_id
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
    return v


def _check_r3_yellow(plan: SignalTimingPlan) -> List[Violation]:
    # R3: yellow change interval — ITE kinematic requirement per phase
    # approach speed + grade (ADR-001), within the MUTCD band.
    v: List[Violation] = []
    for phase in plan.phases:
        min_yellow = required_yellow_s(phase.approach_speed_mph,
                                       phase.grade_pct)
        if phase.yellow_seconds < min_yellow:
            v.append(Violation(
                "R3", f"{plan.intersection_id} phase {phase.phase_id}: yellow "
                      f"{phase.yellow_seconds:.1f}s < kinematic "
                      f"requirement {min_yellow:.1f}s "
                      f"({phase.approach_speed_mph:.0f} mph, "
                      f"{phase.grade_pct:+.1f}% grade)."))
        elif phase.yellow_seconds > MAX_YELLOW_S:
            v.append(Violation(
                "R3", f"{plan.intersection_id} phase {phase.phase_id}: yellow "
                      f"{phase.yellow_seconds:.1f}s > "
                      f"{MAX_YELLOW_S:.1f}s maximum."))
    return v


def _check_r4_ring_barrier(plan: SignalTimingPlan) -> List[Violation]:
    # R4: ring-and-barrier conflict model — phases in each other's
    # conflicts_with (treated symmetrically) must be sequenceable: their
    # combined service times (green + yellow + red clearance) must fit
    # the cycle, otherwise the controller would be forced to run
    # conflicting greens simultaneously.
    v: List[Violation] = []
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
                "R4", f"{plan.intersection_id}: conflicting phases {a} and "
                      f"{b} need "
                      f"{service_by_phase[a] + service_by_phase[b]:.1f}s "
                      f"combined service but the cycle is only "
                      f"{plan.cycle_seconds:.0f}s — greens would be "
                      f"forced to overlap."))
    return v


def _check_r5_cycle_and_phase_bounds(plan: SignalTimingPlan) -> List[Violation]:
    v: List[Violation] = []
    iid = plan.intersection_id
    if not (MIN_CYCLE_S <= plan.cycle_seconds <= MAX_CYCLE_S):
        v.append(Violation(
            "R5", f"{iid}: cycle {plan.cycle_seconds:.1f}s outside "
                  f"{MIN_CYCLE_S:.0f}–{MAX_CYCLE_S:.0f}s."))
    for phase in plan.phases:
        min_green = (MIN_GREEN_LEFT_S if phase.movement == "left_turn"
                     else MIN_GREEN_THROUGH_S)
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
    return v


# -- rulepack #2: MUTCD Chapter 6 temporary work zones ----------------------

def _check_wz1_min_green(plan: SignalTimingPlan) -> List[Violation]:
    v: List[Violation] = []
    for phase in plan.phases:
        if phase.green_seconds < WZ_MIN_GREEN_S:
            v.append(Violation(
                "WZ1", f"{plan.intersection_id} phase {phase.phase_id}: "
                       f"work-zone green {phase.green_seconds:.1f}s < "
                       f"{WZ_MIN_GREEN_S:.0f}s (MUTCD 6F.84)."))
    return v


def _check_wz2_max_cycle(plan: SignalTimingPlan) -> List[Violation]:
    if plan.cycle_seconds > WZ_MAX_CYCLE_S:
        return [Violation(
            "WZ2", f"{plan.intersection_id}: cycle {plan.cycle_seconds:.1f}s "
                   f"> {WZ_MAX_CYCLE_S:.0f}s work-zone cap — queue spillback "
                   f"past the advance warning area (MUTCD 6C.11).")]
    return []


def _check_wz3_all_red_clearance(plan: SignalTimingPlan) -> List[Violation]:
    v: List[Violation] = []
    for phase in plan.phases:
        if phase.red_clearance_seconds < WZ_MIN_ALL_RED_S:
            v.append(Violation(
                "WZ3", f"{plan.intersection_id} phase {phase.phase_id}: "
                       f"all-red {phase.red_clearance_seconds:.1f}s < "
                       f"{WZ_MIN_ALL_RED_S:.0f}s opposing-platoon clearance "
                       f"(MUTCD 6F.84)."))
    return v


def _check_wz4_kinematic_yellow(plan: SignalTimingPlan) -> List[Violation]:
    # Advance-warning consistency: temporary signals still owe drivers the
    # full ITE kinematic yellow (MUTCD 6F.84 defers to 4D.26 intervals).
    v: List[Violation] = []
    for phase in plan.phases:
        min_yellow = required_yellow_s(phase.approach_speed_mph,
                                       phase.grade_pct)
        if phase.yellow_seconds < min_yellow:
            v.append(Violation(
                "WZ4", f"{plan.intersection_id} phase {phase.phase_id}: "
                       f"work-zone yellow {phase.yellow_seconds:.1f}s < "
                       f"kinematic requirement {min_yellow:.1f}s "
                       f"(MUTCD 6F.84 / 4D.26)."))
    return v


MUTCD_RULEPACK = Rulepack(
    name="mutcd", version="1.0", rules=[
        Rule("R1", "Minimum green per movement (MUTCD 4D.26)",
             _check_r1_min_green,
             {"min_green_through_s": MIN_GREEN_THROUGH_S,
              "min_green_left_s": MIN_GREEN_LEFT_S}),
        Rule("R2", "Pedestrian walk + FDW service window (MUTCD 4E.06)",
             _check_r2_pedestrian,
             {"min_ped_walk_s": MIN_PED_WALK_S,
              "walk_speed_ft_s": PED_WALK_SPEED_FT_S,
              "walk_speed_slow_ft_s": PED_WALK_SPEED_SLOW_FT_S}),
        Rule("R3", "ITE kinematic yellow-change interval (MUTCD 4D.26)",
             _check_r3_yellow,
             {"min_yellow_s": MIN_YELLOW_S, "max_yellow_s": MAX_YELLOW_S,
              "perception_reaction_s": PERCEPTION_REACTION_TIME_S,
              "deceleration_m_s2": DECELERATION_RATE_M_S2}),
        Rule("R4", "Ring-and-barrier conflicting-phase sequenceability",
             _check_r4_ring_barrier, {}),
        Rule("R5", "Cycle and phase duration bounds",
             _check_r5_cycle_and_phase_bounds,
             {"min_cycle_s": MIN_CYCLE_S, "max_cycle_s": MAX_CYCLE_S,
              "min_phase_s": MIN_PHASE_S, "max_phase_s": MAX_PHASE_S}),
    ])

WORKZONE_RULEPACK = Rulepack(
    name="workzone", version="1.0", rules=[
        Rule("WZ1", "Temporary-signal minimum green (MUTCD 6F.84)",
             _check_wz1_min_green, {"wz_min_green_s": WZ_MIN_GREEN_S}),
        Rule("WZ2", "Lane-closure maximum cycle (MUTCD 6C.11)",
             _check_wz2_max_cycle, {"wz_max_cycle_s": WZ_MAX_CYCLE_S}),
        Rule("WZ3", "Opposing-platoon all-red clearance (MUTCD 6F.84)",
             _check_wz3_all_red_clearance,
             {"wz_min_all_red_s": WZ_MIN_ALL_RED_S}),
        Rule("WZ4", "Kinematic yellow in work zones (MUTCD 6F.84/4D.26)",
             _check_wz4_kinematic_yellow,
             {"perception_reaction_s": PERCEPTION_REACTION_TIME_S,
              "deceleration_m_s2": DECELERATION_RATE_M_S2}),
    ])

RULEPACKS: Dict[str, Rulepack] = {
    MUTCD_RULEPACK.name: MUTCD_RULEPACK,
    WORKZONE_RULEPACK.name: WORKZONE_RULEPACK,
}


def run_rules(plan: SignalTimingPlan,
              rulepack_name: str = "mutcd") -> List[Violation]:
    violations: List[Violation] = []
    for rule in RULEPACKS[rulepack_name].rules:
        violations.extend(rule.check(plan))
    return violations


def run_rulepack(plan: SignalTimingPlan,
                 rulepack_name: str = "mutcd") -> List[Dict[str, object]]:
    verdicts: List[Dict[str, object]] = []
    for rule in RULEPACKS[rulepack_name].rules:
        violations = rule.check(plan)
        verdicts.append({
            "rule_id": rule.rule_id,
            "description": rule.description,
            "passed": not violations,
            "violations": [{"rule_id": v.rule_id, "message": v.message}
                           for v in violations],
        })
    return verdicts


def rulepack_version(name: str) -> str:
    pack = RULEPACKS[name]
    # Content hash over rule identities + parameters: a parameter change
    # changes the certificate identity (ADR-002 consumes this).
    content = json.dumps(
        [[r.rule_id, r.description, sorted(r.params.items())]
         for r in pack.rules],
        sort_keys=True)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"{pack.name}-{pack.version}-{digest}"
