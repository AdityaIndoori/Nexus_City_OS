"""Nexus City OS — GRANT sample-report generator (ADR-003, investor suite).

Builds an isolated in-memory Store seeded with PLAUSIBLE SYNTHETIC shadow-pilot
data (NOT live/real data — see the "SYNTHETIC SAMPLE DATA" banner below and in
every rendered page's existing methodology note), runs the read-only
EvidenceEngine over it, and writes the three existing ADR-003 HTML templates
to platform/scripts/sample_reports/ so investors/grant reviewers can see the
report shapes without needing 60 days of real pilot history first.

Deliberately NOT part of the test suite or CI (platform/scripts/ is manual
tooling only, per repo AGENTS.md). Does not touch platform/data/nexus.db or
any live process. Fixed random seed → deterministic output on every run.
"""
from __future__ import annotations

import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.audit import AuditTrail          # noqa: E402
from nexus.evidence import EvidenceEngine, MIN_WINDOW_SAMPLES, WINDOW_S  # noqa: E402
from nexus.store import Store               # noqa: E402

SEED = 20260714                    # fixed — deterministic sample data
NOW = time.time()
DAYS = 60.0
SYNTHETIC_TAG = "[SYNTHETIC SAMPLE DATA — not a real pilot]"
OPERATOR_EMAIL = "op.secret@seattle.gov"   # must never reach rendered reports

N_SCORED_PLANS = 46        # yield a nonzero, well-scored counterfactual set
N_THIN_PLANS = 3           # a few genuine scorecard abstentions (thin windows)
N_BLOCKED = 5              # SafetyGate refusals (REFUSED_STATUSES)
N_WITHHELD = 4             # confidence abstentions (ABSTAINED_STATUSES)

INTERSECTIONS = [f"INT-{i:03d}" for i in range(1, 16)]
REFUSED_STATUSES = ("blocked_constraint", "blocked_hallucination",
                    "suppressed_provenance")


def _seed_scored_plan(store: Store, rng: random.Random, plan_id: str,
                      at: float, thin: bool) -> None:
    """One shadow-logged plan + matched before/after congestion windows."""
    target = rng.choice(INTERSECTIONS)
    before = rng.uniform(0.35, 0.9)
    # ~85% of plans show improvement (plausible pilot mix, not a clean sweep)
    improves = rng.random() < 0.85
    after = (before - rng.uniform(0.05, 0.45) if improves
             else before + rng.uniform(0.02, 0.2))
    after = min(max(after, 0.02), 0.98)

    payload = {
        "plan_id": plan_id,
        "incident_id": f"INC-{plan_id}",
        "targets": [target],
        "operations": [{"type": "extend_green", "intersection_id": target,
                        "phase_id": 1, "delta_seconds": rng.uniform(3, 20)}],
        "created_at": at - 60.0,
        "approved_at": at,
        "approved_by": OPERATOR_EMAIL,
        "justification": f"{SYNTHETIC_TAG} synthetic note by {OPERATOR_EMAIL}",
        "status": "shadow_logged",
    }
    store.upsert_plan(plan_id, "shadow_logged", payload["incident_id"],
                      payload, at)

    n = (MIN_WINDOW_SAMPLES - 2) if thin else (MIN_WINDOW_SAMPLES + 3)
    spacing = min(60.0, WINDOW_S / (n + 1))
    rows = []
    for i in range(n):
        rows.append((target, before, at - spacing * (i + 1)))
        rows.append((target, after, at + spacing * (i + 1)))
    store.add_congestion_samples(rows)


def _seed_refused_and_withheld(store: Store, rng: random.Random) -> None:
    for i in range(N_BLOCKED):
        status = REFUSED_STATUSES[i % len(REFUSED_STATUSES)]
        at = NOW - rng.uniform(0, DAYS * 86400.0)
        pid = f"PLAN-BLOCKED-{i}"
        store.upsert_plan(pid, status, f"INC-{pid}",
                          {"plan_id": pid, "targets": [],
                           "justification": SYNTHETIC_TAG}, at)
    for i in range(N_WITHHELD):
        at = NOW - rng.uniform(0, DAYS * 86400.0)
        pid = f"PLAN-WITHHELD-{i}"
        store.upsert_plan(pid, "withheld_confidence", f"INC-{pid}",
                          {"plan_id": pid, "targets": [],
                           "justification": SYNTHETIC_TAG}, at)


def build_seeded_store() -> Store:
    rng = random.Random(SEED)
    store = Store(":memory:")
    for i in range(N_SCORED_PLANS):
        at = NOW - rng.uniform(3600.0, DAYS * 86400.0)
        _seed_scored_plan(store, rng, f"PLAN-{i:03d}", at, thin=False)
    for i in range(N_THIN_PLANS):
        at = NOW - rng.uniform(3600.0, DAYS * 86400.0)
        _seed_scored_plan(store, rng, f"PLAN-THIN-{i:03d}", at, thin=True)
    _seed_refused_and_withheld(store, rng)
    return store


def build_audit_trail() -> AuditTrail:
    audit = AuditTrail()
    for i in range(8):
        audit.record(actor="system", action="shadow_logged",
                    detail=f"{SYNTHETIC_TAG} sample entry {i}")
    return audit


def main() -> None:
    print(SYNTHETIC_TAG)
    print("Seeding isolated in-memory Store with synthetic shadow-pilot "
          f"data (seed={SEED}, window={int(DAYS)}d)...")
    store = build_seeded_store()
    audit = build_audit_trail()
    engine = EvidenceEngine(store, audit=audit)
    sc = engine.scorecard(now=NOW, days=DAYS)

    assert sc.plans_scored > 0, "sample generator must yield scored plans"
    assert sc.scorecard_abstentions > 0, "sample should show abstention too"
    assert sc.plans_refused == N_BLOCKED
    assert sc.plans_abstained == N_WITHHELD
    assert audit.verify_chain()

    out_dir = os.path.join(os.path.dirname(__file__), "sample_reports")
    os.makedirs(out_dir, exist_ok=True)
    pages = {
        "decision_audit.html": engine.render_decision_audit(sc),
        "grant_packet_ss4a.html": engine.render_grant_packet(sc),
        "kpi_benchmark.html": engine.render_kpi_benchmark(sc),
    }
    for name, html in pages.items():
        assert OPERATOR_EMAIL not in html, f"{name} leaked operator identity"
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"wrote {path}")

    print("\n--- headline KPIs (synthetic) ---")
    print(f"plans_logged:          {sc.plans_logged}")
    print(f"plans_refused:         {sc.plans_refused}")
    print(f"plans_abstained:       {sc.plans_abstained}")
    print(f"plans_scored:          {sc.plans_scored}")
    print(f"scorecard_abstentions: {sc.scorecard_abstentions}")
    print(f"avg_congestion_delta:  {sc.avg_congestion_delta}")
    print(f"days_in_shadow:        {sc.days_in_shadow}")
    print(f"audit_chain_verified:  {sc.audit_chain_verified}")
    print(f"dollar_anchor:         {sc.dollar_anchor}")
    store.close()


if __name__ == "__main__":
    main()
