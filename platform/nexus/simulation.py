"""
Nexus City OS — Dry-Run Impact Simulation (PRD §7.2).

Mesoscopic traffic model in the spirit of the cell transmission model (CTM):
each intersection is a cell with an occupancy (congestion index 0..1);
green-time changes alter the cell's discharge capacity, and effects propagate
to neighboring cells with hop-distance damping.

Deterministic, fast (< 5s for ≤ 20 intersections — in practice, milliseconds),
and weather-aware: adverse weather reduces effective discharge gains.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .graph import CityGraph
from .models import ActionPlan

# Discharge-capacity sensitivity: fraction of congestion relieved per
# +1 second of green at the bottleneck (empirically tuned for the demo).
GREEN_SECOND_RELIEF = 0.012
# Hop damping: each hop away receives this fraction of the upstream effect.
HOP_DAMPING = 0.45
# Weather multipliers on discharge effectiveness (PRD §3 weather awareness).
WEATHER_FACTOR = {
    "clear": 1.0,
    "fog": 0.9,
    "rain": 0.8,
    "snow": 0.6,
    "ice": 0.45,
}


def simulate_impact(graph: CityGraph, plan: ActionPlan) -> Dict[str, Any]:
    """Project the congestion impact of an ActionPlan.

    Returns per-intersection estimates (better/worse/neutral with
    percentages), projected incident clear time, and affected transit routes.
    """
    weather = graph.weather
    weather_factor = WEATHER_FACTOR.get(
        weather.condition if weather else "clear", 1.0)

    # Net green delta per target intersection.
    green_delta: Dict[str, float] = {}
    for op in plan.operations:
        delta = 0.0
        if op.type == "extend_green":
            delta = abs(op.delta_seconds)
        elif op.type == "reduce_green":
            delta = -abs(op.delta_seconds)
        green_delta[op.intersection_id] = (
            green_delta.get(op.intersection_id, 0.0) + delta)

    per_intersection: List[Dict[str, Any]] = []
    affected_routes: set = set()
    total_relief = 0.0

    for target, delta in green_delta.items():
        try:
            inter = graph.get_intersection(target)
        except KeyError:
            continue
        relief = delta * GREEN_SECOND_RELIEF * weather_factor
        projected = min(1.0, max(0.0, inter.congestion - relief))
        change_pct = round(100.0 * (projected - inter.congestion), 1)
        per_intersection.append(_entry(inter.id, inter.name,
                                       inter.congestion, projected,
                                       change_pct, hops=0))
        total_relief += max(0.0, -change_pct)

        # Propagate (damped) to neighbors. Extending green at the bottleneck
        # relieves upstream queues but slightly loads cross-street neighbors.
        for impact in graph.cascading_impact(target, max_hops=2):
            n = graph.get_intersection(impact["intersection_id"])
            damped = relief * (HOP_DAMPING ** impact["hops"])
            # Cross-street penalty: ~30% of the relief is displaced.
            displaced = -0.3 * damped if delta > 0 else -damped
            neighbor_projected = min(1.0, max(
                0.0, n.congestion - damped + abs(displaced) * 0.5))
            n_change = round(
                100.0 * (neighbor_projected - n.congestion), 1)
            per_intersection.append(_entry(n.id, n.name, n.congestion,
                                           neighbor_projected, n_change,
                                           hops=impact["hops"]))

        for vehicle in graph.vehicles_near(target):
            affected_routes.add(vehicle.route)

    # De-duplicate, keeping the largest-magnitude estimate per intersection.
    best: Dict[str, Dict[str, Any]] = {}
    for entry in per_intersection:
        existing = best.get(entry["intersection_id"])
        if existing is None or abs(entry["change_pct"]) > abs(existing["change_pct"]):
            best[entry["intersection_id"]] = entry
    estimates = sorted(best.values(), key=lambda e: e["change_pct"])

    # Projected time to clear: base 18 min, reduced by total relief, raised
    # by adverse weather.
    projected_clear_min = round(
        max(4.0, (18.0 - 0.8 * total_relief) / max(0.4, weather_factor)), 1)

    improved = sum(1 for e in estimates if e["verdict"] == "better")
    worsened = sum(1 for e in estimates if e["verdict"] == "worse")

    return {
        "weather_factor": weather_factor,
        "estimates": estimates,
        "projected_clear_minutes": projected_clear_min,
        "transit_routes_affected": sorted(affected_routes),
        "estimated_transit_delay_min": round(2.0 + 6.0 * (1.0 - weather_factor)
                                             + 0.5 * worsened, 1),
        "summary": {
            "intersections_improved": improved,
            "intersections_worsened": worsened,
            "intersections_neutral": len(estimates) - improved - worsened,
        },
    }


def _entry(iid: str, name: str, current: float, projected: float,
           change_pct: float, hops: int) -> Dict[str, Any]:
    if change_pct < -1.0:
        verdict = "better"
    elif change_pct > 1.0:
        verdict = "worse"
    else:
        verdict = "neutral"
    return {
        "intersection_id": iid,
        "name": name,
        "hops": hops,
        "current_congestion": round(current, 3),
        "projected_congestion": round(projected, 3),
        "change_pct": change_pct,
        "verdict": verdict,
    }