"""
Nexus City OS — Investor Demo Walkthrough (Wave 4 DEMO, plan §Wave 4).

Offline, deterministic, self-contained six-beat tour of the safety moat:
INCIDENT -> PLAN -> CERTIFICATE -> REFUSAL -> ABSTENTION -> APPROVE.

Builds its OWN runtime (SeattleAdapter(seed=42) + Store(":memory:")) — never
touches platform/data/nexus.db or any live deployment. No network, no LLM
calls (deterministic copilot fallback). Run:

    python platform/scripts/demo_walkthrough.py

Exits non-zero with a clear message if any beat fails to produce the
expected outcome (this script doubles as a manual smoke check).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexus import bootstrap  # noqa: E402
from nexus.adapters import SeattleAdapter  # noqa: E402
from nexus.certs import CERT_ACTION  # noqa: E402
from nexus.mcp import handle_mcp  # noqa: E402
from nexus.models import (  # noqa: E402
    FRESHNESS_THRESHOLDS,
    IncidentType,
    PlanStatus,
    Role,
    WeatherCondition,
    now_ts,
)
from nexus.store import Store  # noqa: E402

ONE_LINER = ("Nexus City OS - the only traffic AI that knows when to stay "
             "quiet. Every decision verified, certified, and refusable.")

# Stale enough to tank the freshness score (all thresholds are well under
# 900s) but under the H3 hallucination window (30 min) so the plan reaches
# the confidence check instead of being blocked as hallucinated first.
STALE_FEED_AGE_S = 20 * 60.0


def _fail(beat: str, message: str) -> None:
    print(f"\n[FAILED] Beat {beat}: {message}")
    sys.exit(1)


def _header(n: int, name: str) -> None:
    print(f"\n{'=' * 70}\nBEAT {n} — {name}\n{'=' * 70}")


def beat_1_incident(engine, edge):
    _header(1, "INCIDENT")
    iid = next(iter(engine.graph.cameras.values())).intersection_id
    edge.inject_scenario(iid, IncidentType.COLLISION)
    edge.tick()
    incidents = [i for i in engine.graph.incidents.values()
                 if i.intersection_id == iid]
    if not incidents:
        _fail("1 INCIDENT", "edge simulator did not detect the injected collision")
    inc = incidents[0]
    print(f"Injected a collision at {iid}; edge CV detected {inc.id} "
          f"(severity={inc.severity:.0%}, source={inc.detection_source})")
    engine.acknowledge_incident("op-1", inc.id)
    print(f"Operator op-1 acknowledged {inc.id}")
    return inc


def beat_2_plan(engine, inc):
    _header(2, "PLAN")
    plan = engine.recommend(inc.id)
    if plan.status != PlanStatus.PENDING_APPROVAL:
        _fail("2 PLAN", f"expected pending_approval, got {plan.status.value}: "
                        f"{plan.block_reason}")
    print(f"engine.recommend() produced plan {plan.plan_id}")
    print(f"  status={plan.status.value}  "
          f"confidence={plan.confidence.composite}%  targets={plan.targets}")
    print(f"  justification: {plan.justification}")
    return plan


def beat_3_certificate(engine, plan):
    _header(3, "CERTIFICATE")
    if engine.certs is None:
        _fail("3 CERTIFICATE", "engine.certs is None (no Store attached)")
    entries = [e for e in engine.audit.entries(500)
               if e["action"] == CERT_ACTION
               and e["after_state"]["certificate"]["plan_id"] == plan.plan_id]
    if not entries:
        _fail("3 CERTIFICATE", f"no safety_certificate audit entry for {plan.plan_id}")
    entry = entries[0]
    cert = entry["after_state"]["certificate"]
    if not engine.certs.verify_certificate(entry):
        _fail("3 CERTIFICATE", "HMAC signature failed to verify")
    print(f"Certificate {cert['cert_id']} issued for plan {cert['plan_id']}")
    print(f"  key_id={cert['key_id']}")
    print(f"  verdict={cert['verdict']}")
    print(f"  ruleset_version={cert['ruleset_version']}")
    print("  HMAC signature verified against the hash-chained audit entry.")
    return cert


def beat_4_refusal(engine, inc):
    _header(4, "REFUSAL")
    iid = inc.intersection_id
    timing = engine.graph.get_intersection(iid).timing_plan
    unsafe_op = {
        "type": "reduce_green",
        "intersection_id": iid,
        "phase_id": timing.phases[0].phase_id,
        "delta_seconds": 60.0,  # collapses the through phase far below MUTCD minimums
    }
    principal = {"sub": "op-1", "role": Role.OPERATOR.value}
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "attempt_action", "arguments": {
            "incident_id": inc.id,
            "targets": [iid],
            "operations": [unsafe_op],
            "justification": "investor demo: attempt an unsafe green cut",
        }},
    }
    resp = handle_mcp(engine, principal, payload)
    result = resp.get("result")
    if result is None or result.get("passed") is not False:
        _fail("4 REFUSAL", f"expected a refused attempt_action result, got {resp}")
    print("MCP tools/call attempt_action (-60s green) via nexus.mcp.handle_mcp:")
    print(f"  status={result['status']}")
    print(f"  block_reason={result['block_reason']}")
    for v in result["violations"]:
        print(f"  violation: [{v['rule_id']}] {v['message']}")
    print(f"  {result['note']}")
    return result


def beat_5_abstention(engine, edge, exclude_intersection_id):
    _header(5, "ABSTENTION")
    # Honest degraded-confidence scenario (safety.py §4.3): icy weather plus
    # every tracked feed sitting well outside its freshness window.
    engine.graph.set_weather(WeatherCondition(
        condition="ice", temperature_f=18.0, severe_alert=True))
    stale_at = now_ts() - STALE_FEED_AGE_S
    for source in FRESHNESS_THRESHOLDS:
        engine.feed_last_update[source] = stale_at

    cams = [c for c in engine.graph.cameras.values()
            if c.intersection_id != exclude_intersection_id]
    if not cams:
        _fail("5 ABSTENTION", "no second monitored intersection available")
    iid2 = cams[0].intersection_id
    edge.inject_scenario(iid2, IncidentType.STOPPED_VEHICLE)
    edge.tick()
    incidents = [i for i in engine.graph.incidents.values()
                 if i.intersection_id == iid2]
    if not incidents:
        _fail("5 ABSTENTION", "edge simulator did not detect the second scenario")
    inc2 = incidents[0]
    engine.acknowledge_incident("op-1", inc2.id)
    plan2 = engine.recommend(inc2.id)
    if plan2.status != PlanStatus.WITHHELD_CONFIDENCE:
        _fail("5 ABSTENTION",
             f"expected withheld_confidence, got {plan2.status.value} "
             f"(confidence={plan2.confidence.composite}%)")
    print(f"Degraded conditions: weather=ice, all feeds ~{STALE_FEED_AGE_S / 60:.0f} "
          f"min stale (thresholds: {dict(FRESHNESS_THRESHOLDS)})")
    print(f"Plan {plan2.plan_id} composite confidence = "
          f"{plan2.confidence.composite}% (threshold="
          f"{engine.safety.confidence_threshold}%)")
    print(f"  status={plan2.status.value}")
    print(f"  block_reason={plan2.block_reason}")
    print(f"  recorded: safety.metrics.withheld_confidence="
          f"{engine.safety.metrics.withheld_confidence}")
    return plan2


def beat_6_approve(engine, plan):
    _header(6, "APPROVE")
    approved = engine.approve("op-1", plan.plan_id)
    if approved.status not in (PlanStatus.SHADOW_LOGGED, PlanStatus.APPROVED,
                              PlanStatus.ADVISORY_ISSUED, PlanStatus.EXECUTED):
        _fail("6 APPROVE", f"unexpected post-approval status: {approved.status.value}")
    print(f"Operator op-1 approved plan {plan.plan_id} -> "
          f"status={approved.status.value} (mode={engine.mode.value})")
    intact = engine.audit.verify_chain()
    if not intact:
        _fail("6 APPROVE", "audit chain failed verification after approval")
    print(f"engine.audit.verify_chain() -> {intact}  "
          f"({len(engine.audit)} hash-chained entries)")
    return approved


def main() -> None:
    print("NEXUS CITY OS — INVESTOR DEMO WALKTHROUGH")
    print("Offline, deterministic, self-contained (seed=42, in-memory store).")

    engine, edge, _ = bootstrap(SeattleAdapter(seed=42), store=Store(":memory:"))

    inc = beat_1_incident(engine, edge)
    plan = beat_2_plan(engine, inc)
    beat_3_certificate(engine, plan)
    beat_4_refusal(engine, inc)
    beat_5_abstention(engine, edge, inc.intersection_id)
    beat_6_approve(engine, plan)

    print(f"\n{'=' * 70}")
    print(ONE_LINER)
    print("=" * 70)


if __name__ == "__main__":
    main()
