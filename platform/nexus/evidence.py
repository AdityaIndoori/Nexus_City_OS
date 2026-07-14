"""
Nexus City OS — Shadow Evidence (ADR-003, investor verification suite).

Counterfactual scorecard over the Store's history: scores logged shadow-mode
would-be plans post-hoc against actual bus-probe congestion trajectories
(matched before/after windows around each plan's would-be application time
at its target intersections — a directional signal, not ground truth), and
renders three print-optimized HTML artifacts (60-day Decision Audit,
SS4A/SMART grant packet, standardized KPI benchmark).

Strictly read-only: every query goes through the existing ``Store`` API
(never a second SQLite connection — SQLITE_BUSY risk vs the 60s sampling
writer); zero writes. Abstains (no score, explicit reason) when either
window is data-thin — abstention is a feature (PRD §4.3 ethos).

REDACTION (shareable tier): scorecards and reports carry aggregate counts
and role labels only — never operator emails/identities, never raw operator
notes, never 911 street addresses. The per-incident legal export elsewhere
stays full-detail.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import now_ts
from .store import Store

WINDOW_S = 1800.0          # matched before/after window: 30 min (ADR-003)
MIN_WINDOW_SAMPLES = 5     # fewer probe samples in either window → abstain
DEFAULT_DAYS = 60.0        # Decision Audit horizon (ADR-003)
VALUE_PER_DELAY_HOUR_USD = 19.86  # FHWA urban travel-time value, directional

# plan statuses → aggregate buckets (mirrors analytics.OUTCOME_BUCKETS split)
REFUSED_STATUSES = ("blocked_constraint", "blocked_hallucination",
                    "suppressed_provenance")
ABSTAINED_STATUSES = ("withheld_confidence",)

METHOD_NOTE = ("Methodology: matched before/after windows (30 min each) "
               "around each shadow plan's would-be application time at its "
               "target intersections, scored on bus-probe congestion — a "
               "directional signal, not ground truth.")


@dataclass
class PlanScore:
    plan_id: str
    applied_at: float
    targets: List[str]
    before_mean: Optional[float] = None
    after_mean: Optional[float] = None
    delta: Optional[float] = None            # after - before (negative = better)
    before_samples: int = 0
    after_samples: int = 0
    abstained: bool = False
    abstain_reason: str = ""


@dataclass
class EvidenceScorecard:
    """Named KPI schema (ADR-003) — aggregate counts + method notes only."""
    generated_at: float
    window_days: float
    capture_start: Optional[float]           # honest about when capture began
    days_in_shadow: float
    plans_logged: int                        # shadow_logged
    plans_refused: int                       # SafetyGate blocks (explainable refusal)
    plans_abstained: int                     # confidence abstentions
    plans_scored: int
    scorecard_abstentions: int               # thin-data abstentions (this engine)
    avg_congestion_delta: Optional[float]    # mean delta across scored plans
    method_note: str
    incident_count: int
    audit_chain_verified: Optional[bool]     # None = no audit trail supplied
    dollar_anchor: str                       # rendered text, honest/directional
    plan_scores: List[PlanScore] = field(default_factory=list)


class EvidenceEngine:
    """Read-only counterfactual scorecard + report renderer over a Store."""

    def __init__(self, store: Store, audit: Optional[Any] = None) -> None:
        self.store = store
        self.audit = audit   # AuditTrail with verify_chain(); optional

    # -- scoring -----------------------------------------------------------

    def scorecard(self, now: Optional[float] = None,
                  days: float = DEFAULT_DAYS) -> EvidenceScorecard:
        now = now if now is not None else now_ts()
        since = now - days * 86400.0

        shadow = self.store.plan_snapshots(since, status="shadow_logged")
        statuses = self.store.plan_outcomes()
        refused = sum(statuses.get(s, 0) for s in REFUSED_STATUSES)
        abstained = sum(statuses.get(s, 0) for s in ABSTAINED_STATUSES)

        # one congestion fetch covering every plan's windows, indexed in memory
        earliest = min((p["updated_at"] for p in shadow), default=now)
        samples = self.store.congestion_history(earliest - WINDOW_S)
        by_inter: Dict[str, List[Any]] = {}
        for s in samples:
            by_inter.setdefault(s["intersection_id"], []).append(
                (s["at"], s["congestion"]))

        scores = [self._score_plan(p, by_inter) for p in shadow]
        deltas = [s.delta for s in scores if not s.abstained]
        avg_delta = (round(statistics.fmean(deltas), 4)
                     if deltas else None)

        first_sample = min((s["at"] for s in samples), default=None)
        first_plan = min((p["updated_at"] for p in shadow), default=None)
        capture_start = min((t for t in (first_sample, first_plan)
                             if t is not None), default=None)
        days_in_shadow = (round((now - capture_start) / 86400.0, 1)
                          if capture_start is not None else 0.0)

        return EvidenceScorecard(
            generated_at=now,
            window_days=days,
            capture_start=capture_start,
            days_in_shadow=days_in_shadow,
            plans_logged=len(shadow),
            plans_refused=refused,
            plans_abstained=abstained,
            plans_scored=len(deltas),
            scorecard_abstentions=len(scores) - len(deltas),
            avg_congestion_delta=avg_delta,
            method_note=METHOD_NOTE,
            incident_count=len(self.store.incident_history(since)),
            audit_chain_verified=(self.audit.verify_chain()
                                  if self.audit is not None else None),
            dollar_anchor=self._dollar_anchor(avg_delta, len(deltas)),
            plan_scores=scores,
        )

    def _score_plan(self, plan: Dict[str, Any],
                    by_inter: Dict[str, List[Any]]) -> PlanScore:
        # would-be application time: approval time when present, else logging
        at = plan.get("approved_at") or plan["updated_at"]
        targets = [str(t) for t in plan.get("targets", [])]
        score = PlanScore(plan_id=str(plan.get("plan_id", "?")),
                          applied_at=float(at), targets=targets)
        before: List[float] = []
        after: List[float] = []
        for target in targets:
            for ts, cong in by_inter.get(target, []):
                if at - WINDOW_S <= ts < at:
                    before.append(cong)
                elif at < ts <= at + WINDOW_S:
                    after.append(cong)
        score.before_samples = len(before)
        score.after_samples = len(after)
        if min(len(before), len(after)) < MIN_WINDOW_SAMPLES:
            score.abstained = True
            score.abstain_reason = (
                f"insufficient probe samples in matched windows "
                f"(before={len(before)}, after={len(after)}, "
                f"need >= {MIN_WINDOW_SAMPLES} each) — abstaining "
                f"rather than reporting a low-confidence number")
            return score
        score.before_mean = round(statistics.fmean(before), 4)
        score.after_mean = round(statistics.fmean(after), 4)
        score.delta = round(score.after_mean - score.before_mean, 4)
        return score

    def _dollar_anchor(self, avg_delta: Optional[float],
                       scored: int) -> str:
        if avg_delta is None or avg_delta >= 0 or scored == 0:
            return ("Estimated delay-hours saved: not claimed — "
                    "insufficient scored evidence for a dollar figure "
                    "(directional metric withheld, not rounded up).")
        # congestion delta × window × scored plans → intersection delay-hours;
        # deliberately conservative and labeled directional.
        hours = abs(avg_delta) * (WINDOW_S / 3600.0) * scored
        return (f"Estimated delay-hours saved (directional): "
                f"~{hours:.1f} intersection-hours across {scored} scored "
                f"plan windows (~${hours * VALUE_PER_DELAY_HOUR_USD:.0f} "
                f"at the FHWA ${VALUE_PER_DELAY_HOUR_USD}/veh-hr value of "
                f"time) — an anchor, not a measured saving.")

    # -- HTML rendering (index.html-style string substitution, no build) ----

    def render_decision_audit(self, sc: EvidenceScorecard) -> str:
        return self._render(
            sc, title=f"{int(sc.window_days)}-Day Decision Audit",
            subtitle="Shadow-mode counterfactual evidence report",
            body=_AUDIT_BODY)

    def render_grant_packet(self, sc: EvidenceScorecard) -> str:
        return self._render(
            sc, title="SS4A / SMART Grant Evidence Packet",
            subtitle="Safe Streets and Roads for All — supporting data",
            body=_GRANT_BODY)

    def render_kpi_benchmark(self, sc: EvidenceScorecard) -> str:
        return self._render(
            sc, title="Standardized KPI Benchmark",
            subtitle="Comparable decision-intelligence KPIs",
            body=_BENCH_BODY)

    def _render(self, sc: EvidenceScorecard, title: str,
                subtitle: str, body: str) -> str:
        delta = ("abstained (thin data)" if sc.avg_congestion_delta is None
                 else f"{sc.avg_congestion_delta:+.3f}")
        chain = ("yes" if sc.audit_chain_verified
                 else "no" if sc.audit_chain_verified is not None else "n/a")
        capture = (time.strftime("%Y-%m-%d",
                                 time.localtime(sc.capture_start))
                   if sc.capture_start is not None else "no data captured yet")
        tokens = {
            "__TITLE__": title,
            "__SUBTITLE__": subtitle,
            "__GENERATED__": time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(sc.generated_at)),
            "__WINDOW_DAYS__": f"{sc.window_days:.0f}",
            "__CAPTURE_START__": capture,
            "__DAYS_IN_SHADOW__": f"{sc.days_in_shadow:.1f}",
            "__PLANS_LOGGED__": str(sc.plans_logged),
            "__PLANS_REFUSED__": str(sc.plans_refused),
            "__PLANS_ABSTAINED__": str(sc.plans_abstained),
            "__PLANS_SCORED__": str(sc.plans_scored),
            "__SCORE_ABSTENTIONS__": str(sc.scorecard_abstentions),
            "__DELTA__": delta,
            "__INCIDENTS__": str(sc.incident_count),
            "__CHAIN__": chain,
            "__DOLLAR__": sc.dollar_anchor,
            "__METHOD__": sc.method_note,
        }
        html = _PAGE.replace("__BODY__", body)
        for key, value in tokens.items():
            html = html.replace(key, value)
        return html


# ---------------------------------------------------------------------------
# Templates — shareable tier: aggregate counts + role labels ONLY (no operator
# identities, no raw notes, no 911 addresses). Print CSS included.
# ---------------------------------------------------------------------------

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__ — Nexus City OS</title>
<style>
  body { font: 14px/1.5 Georgia, 'Times New Roman', serif; color: #1a1a2e;
         max-width: 800px; margin: 40px auto; padding: 0 24px; }
  h1 { font-size: 26px; margin-bottom: 2px; }
  .sub { color: #555; margin-top: 0; }
  table { border-collapse: collapse; width: 100%; margin: 18px 0; }
  th, td { border: 1px solid #bbb; padding: 8px 12px; text-align: left; }
  th { background: #f0f0f5; }
  .kpi { font-size: 22px; font-weight: bold; }
  .note { background: #f7f7fa; border-left: 4px solid #4a4a8a;
          padding: 10px 14px; margin: 16px 0; font-size: 13px; }
  .proof { font-family: monospace; font-size: 13px; }
  footer { margin-top: 32px; font-size: 12px; color: #777;
           border-top: 1px solid #ccc; padding-top: 10px; }
  @media print {
    body { margin: 0; max-width: none; font-size: 12px; }
    .note { border-left-width: 3px; }
    a { color: inherit; text-decoration: none; }
    h1 { page-break-after: avoid; }
    table { page-break-inside: avoid; }
  }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<p class="sub">__SUBTITLE__ · Nexus City OS · generated __GENERATED__</p>
__BODY__
<div class="note">__METHOD__</div>
<div class="note">__DOLLAR__</div>
<p class="proof">Audit chain verified: __CHAIN__ (tamper-evident
hash-chained trail, PRD &sect;11.3)</p>
<footer>Shareable evidence tier: aggregate counts and role labels only —
operator identities, raw operator notes, and 911 street addresses are
redacted by design. Data capture began __CAPTURE_START__.</footer>
</body>
</html>
"""

_KPI_TABLE = """<table>
<tr><th>KPI</th><th>Value</th></tr>
<tr><td>Days in shadow (capture began __CAPTURE_START__)</td>
    <td class="kpi">__DAYS_IN_SHADOW__</td></tr>
<tr><td>Would-be plans logged (shadow mode)</td>
    <td class="kpi">__PLANS_LOGGED__</td></tr>
<tr><td>Plans refused by SafetyGate (explainable refusal)</td>
    <td class="kpi">__PLANS_REFUSED__</td></tr>
<tr><td>Plans withheld on low confidence (abstention)</td>
    <td class="kpi">__PLANS_ABSTAINED__</td></tr>
<tr><td>Plans scored against matched congestion windows</td>
    <td class="kpi">__PLANS_SCORED__</td></tr>
<tr><td>Scorecard abstentions (thin data — no number reported)</td>
    <td class="kpi">__SCORE_ABSTENTIONS__</td></tr>
<tr><td>Avg congestion delta, after vs before (negative = improvement)</td>
    <td class="kpi">__DELTA__</td></tr>
<tr><td>Incidents handled in window</td>
    <td class="kpi">__INCIDENTS__</td></tr>
</table>
"""

_AUDIT_BODY = ("""<p>This __WINDOW_DAYS__-day Decision Audit scores every
would-be action the platform logged in <strong>Shadow Mode</strong> — where
no physical change is ever executed — against the congestion trajectory the
city actually experienced.</p>
""" + _KPI_TABLE + """<p>Every count above is reproducible from the
tamper-evident audit trail; refusals and abstentions are reported as
first-class outcomes, not hidden.</p>
""")

_GRANT_BODY = ("""<p>Supporting evidence for a Safe Streets and Roads for
All (SS4A) or SMART grant application. All figures derive from
<strong>__WINDOW_DAYS__ days</strong> of shadow-mode operation on existing
city infrastructure — no hardware installed, no signal timing modified.</p>
""" + _KPI_TABLE + """<p>The platform's safety envelope (MUTCD R1&ndash;R7
verification, hallucination monitoring, confidence abstention, human-in-the-
loop approval) blocked __PLANS_REFUSED__ plans and withheld
__PLANS_ABSTAINED__ on low confidence during the period — demonstrable,
auditable restraint suitable for federal safety funding narratives.</p>
""")

_BENCH_BODY = ("""<p>Standardized decision-intelligence KPIs over a
__WINDOW_DAYS__-day window, computed identically for any deployment of the
platform (methodology below) so periods and sites are comparable.</p>
""" + _KPI_TABLE)
