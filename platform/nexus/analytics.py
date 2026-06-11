"""
Nexus City OS — Historical analytics (Phase 3).

Pure aggregation over the Store's history tables for the Analyst dashboard:
  * hourly congestion buckets (avg/max/sample count),
  * top congestion hotspots by intersection,
  * incident counts by type,
  * plan outcome counts (approved/rejected/blocked/reverted/...).

Stateless and network-free: everything reads from SQLite via ``Store``.
"""
from __future__ import annotations

import statistics
import time
from typing import Any, Callable, Dict, List, Optional

from .store import Store

# plan statuses grouped into outcome buckets for the dashboard
OUTCOME_BUCKETS = {
    "approved": ("approved", "executed", "shadow_logged", "advisory_issued"),
    "rejected": ("rejected",),
    "blocked": ("blocked_constraint", "blocked_hallucination",
                "suppressed_provenance", "withheld_confidence"),
    "reverted": ("reverted",),
    "pending": ("pending_approval", "generated"),
}


class Analytics:
    """Aggregates Store history into the /api/analytics response shape."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def summary(self, hours: float = 24.0,
                name_lookup: Optional[Callable[[str], str]] = None,
                now: Optional[float] = None) -> Dict[str, Any]:
        now = now if now is not None else time.time()
        hours = max(0.5, min(168.0, float(hours)))
        since = now - hours * 3600.0

        rows = self.store.congestion_history(since)

        # -- hourly buckets ------------------------------------------------
        by_hour: Dict[str, List[float]] = {}
        by_inter: Dict[str, List[float]] = {}
        for r in rows:
            hour_key = time.strftime("%Y-%m-%dT%H:00",
                                     time.localtime(r["at"]))
            by_hour.setdefault(hour_key, []).append(r["congestion"])
            by_inter.setdefault(r["intersection_id"],
                                []).append(r["congestion"])
        congestion_by_hour = [{
            "hour": hour,
            "avg": round(statistics.fmean(vals), 3),
            "max": round(max(vals), 3),
            "samples": len(vals),
        } for hour, vals in sorted(by_hour.items())]

        # -- hotspots --------------------------------------------------------
        hotspots = sorted(({
            "intersection_id": iid,
            "name": name_lookup(iid) if name_lookup else iid,
            "avg": round(statistics.fmean(vals), 3),
            "max": round(max(vals), 3),
            "samples": len(vals),
        } for iid, vals in by_inter.items()),
            key=lambda h: h["avg"], reverse=True)[:10]

        # -- incidents -------------------------------------------------------
        incident_counts: Dict[str, int] = {}
        vision_confirmed = 0
        for inc in self.store.incident_history(since):
            itype = str(inc.get("type", "unknown"))
            incident_counts[itype] = incident_counts.get(itype, 0) + 1
            if inc.get("detection_source") == "ai_vision":
                vision_confirmed += 1

        # -- plan outcomes ------------------------------------------------------
        plan_outcomes = {bucket: 0 for bucket in OUTCOME_BUCKETS}
        for p in self.store.plan_history(since):
            status = str(p.get("status", ""))
            for bucket, statuses in OUTCOME_BUCKETS.items():
                if status in statuses:
                    plan_outcomes[bucket] += 1
                    break

        return {
            "available": True,
            "window_hours": hours,
            "generated_at": now,
            "congestion_by_hour": congestion_by_hour,
            "hotspots": hotspots,
            "incident_counts": incident_counts,
            "plan_outcomes": plan_outcomes,
            "vision_sweep": {"incidents_confirmed": vision_confirmed},
            "total_samples": len(rows),
        }