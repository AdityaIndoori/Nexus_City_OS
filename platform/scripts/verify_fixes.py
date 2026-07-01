"""Smoke verification of the bug-fix batch (run: python platform/scripts/verify_fixes.py)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexus.engine import (  # noqa: E402
    NexusEngine, AUTO_REVERT_WINDOW_S,
)
from nexus.models import (  # noqa: E402
    ActionPlan, ConfidenceBreakdown, IncidentType, OperatingMode,
    PlanStatus, Provenance, now_ts,
)
from nexus import bootstrap  # noqa: E402
from nexus.adapters import SeattleAdapter  # noqa: E402

ok = 0


def check(name, cond):
    global ok
    assert cond, f"FAILED: {name}"
    ok += 1
    print(f"  ok  {name}")


engine, edge, _ = bootstrap(SeattleAdapter(), use_llm=False)

# --- new APIs exist ---
check("engine.set_confidence_threshold exists",
      hasattr(engine, "set_confidence_threshold"))
check("engine.expire_advisories exists", hasattr(engine, "expire_advisories"))
check("audit.verify_chain_cached works",
      engine.audit.verify_chain_cached() is True)

# --- governed threshold via engine (admin-only, audited) ---
engine.set_confidence_threshold("admin-1", 80.0)
check("threshold set to 80", engine.safety.confidence_threshold == 80.0)
try:
    engine.set_confidence_threshold("op-1", 75.0)
    raise AssertionError("operator must not set threshold")
except Exception:
    check("threshold blocked for operator", True)
engine.set_confidence_threshold("admin-1", 70.0)

# --- R6 settle: executed change frees its registration after the window ---
engine.set_mode("admin-1", OperatingMode.LIVE)
cams = list(engine.graph.cameras.values())
iid_a, iid_b = cams[0].intersection_id, cams[1].intersection_id
edge.inject_scenario(iid_a, IncidentType.COLLISION)
edge.tick()
inc = next(i for i in engine.graph.incidents.values()
           if i.state.value not in ("resolved", "closed"))
plan = engine.recommend(inc.id)
assert plan.status == PlanStatus.PENDING_APPROVAL, plan.block_reason
plan = engine.approve("op-1", plan.plan_id)
check("plan executed in Live mode", plan.status == PlanStatus.EXECUTED)
check("active change registered",
      engine.safety.verifier.active_change_count() == len(plan.targets))
# age past the monitoring window and run the monitor pass
engine._monitoring[plan.plan_id]["executed_at"] = \
    now_ts() - AUTO_REVERT_WINDOW_S - 1
engine.check_rollback_monitors()
check("settled change frees R6 budget",
      engine.safety.verifier.active_change_count() == 0)
check("monitor pruned", plan.plan_id not in engine._monitoring)
check("plan stays EXECUTED after settle",
      plan.status == PlanStatus.EXECUTED)

# --- reject guard: cannot reject an executed plan ---
try:
    engine.reject("op-1", plan.plan_id)
    raise AssertionError("must not reject an executed plan")
except ValueError:
    check("reject blocked for executed plan", True)

# --- advisory expiry ---
engine.set_mode("admin-1", OperatingMode.ADVISORY)
edge.inject_scenario(iid_b, IncidentType.STOPPED_VEHICLE)
edge.tick()
inc2 = next(i for i in engine.graph.incidents.values()
            if i.intersection_id == iid_b
            and i.state.value not in ("resolved", "closed"))
plan2 = engine.recommend(inc2.id)
plan2 = engine.approve("op-1", plan2.plan_id)
check("advisory issued", plan2.status == PlanStatus.ADVISORY_ISSUED)
plan2.expires_at = now_ts() - 1
n = engine.expire_advisories()
check("advisory expired by tick", n == 1
      and plan2.status == PlanStatus.EXPIRED)
inst = engine.advisory_instruction(plan2.plan_id)
check("expired instruction still viewable", inst["expired"] is True)

# --- cached chain verification latches on tamper ---
check("chain intact before tamper", engine.audit.verify_chain_cached())
engine.audit._entries[1]["actor"] = "attacker"
engine.audit._verified_upto = 0        # force full re-scan
check("tamper detected", engine.audit.verify_chain_cached() is False)
check("latched False", engine.audit.verify_chain_cached() is False)

print(f"\nAll {ok} checks passed.")