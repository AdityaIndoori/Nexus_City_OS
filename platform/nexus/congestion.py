"""
Nexus City OS — Real congestion estimation (Phase 1).

Derives per-intersection congestion from REAL observed speeds instead of
the edge-simulator random walk:

  * Live transit GPS (King County Metro / Pierce Transit buses already
    flowing through ``PlatformRuntime.tick()``) — every moving bus is a
    probe vehicle reporting street speed.
  * Optional WSDOT loop-detector flow data (``SeattleLiveData.flow_speeds``,
    enabled by the ``WSDOT_ACCESS_CODE`` env var) — high-weight samples.

Pure computation: no I/O, no threads, fully unit-testable. The runtime
feeds samples in each tick and publishes the resulting fresh-intersection
set to ``NexusEngine.real_congestion_ids`` so the simulator stops
overwriting real estimates (anomalies always still drive congestion so
injected scenarios work everywhere).

Noise handling:
  * Buses dwelling at stops (speed ≤ 0.5 mph) are ignored.
  * Stale GPS fixes (> 120 s old) are ignored.
  * An intersection needs ≥ ``min_samples`` weighted samples from distinct
    sources inside the freshness window before an estimate is produced.
  * The median (weighted) speed is used, not the mean — robust to a single
    outlier probe.

Speed-limit heuristic: WSDOT highway cameras carry names like
"I-5 @ NE 195th St"; intersections whose name references a highway are
scored against a 55 mph baseline, surface streets against 25 mph.
"""
from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .graph import CityGraph
from .models import TransitVehicle

SURFACE_SPEED_MPH = 25.0
HIGHWAY_SPEED_MPH = 55.0
HIGHWAY_MARKERS = ("I-5", "I-90", "I-405", "SR-", "@")
STALE_FIX_S = 120.0


def speed_limit_estimate(intersection_name: str) -> float:
    """25 mph for surface streets; 55 mph when the intersection name
    references a highway (WSDOT cams use "@" — e.g. "I-5 @ NE 195th St")."""
    name = intersection_name or ""
    if any(marker in name for marker in HIGHWAY_MARKERS):
        return HIGHWAY_SPEED_MPH
    return SURFACE_SPEED_MPH


class CongestionEstimator:
    """Aggregates real speed observations into per-intersection congestion."""

    def __init__(self, graph: CityGraph, fresh_window_s: float = 180.0,
                 min_samples: int = 2, radius_deg: float = 0.008) -> None:
        self.graph = graph
        self.fresh_window_s = fresh_window_s
        self.min_samples = min_samples
        self.radius_deg = radius_deg
        # intersection_id -> {source_id: (speed_mph, weight, at)}
        self._samples: Dict[str, Dict[str, Tuple[float, float, float]]] = {}
        # intersection_id -> (congestion, computed_at)
        self._estimates: Dict[str, Tuple[float, float]] = {}
        # lazy spatial index: (bucket_lat, bucket_lon) -> [intersection_id]
        self._index: Dict[Tuple[int, int], List[str]] = {}
        self._index_size = -1
        self.flow_active = False    # last ingest saw WSDOT flow data

    # -- spatial index -----------------------------------------------------

    def _bucket(self, lat: float, lon: float) -> Tuple[int, int]:
        return (int(lat / self.radius_deg), int(lon / self.radius_deg))

    def _ensure_index(self) -> None:
        if self._index_size == len(self.graph.intersections):
            return
        index: Dict[Tuple[int, int], List[str]] = {}
        for inter in self.graph.intersections.values():
            index.setdefault(self._bucket(inter.lat, inter.lon),
                             []).append(inter.id)
        self._index = index
        self._index_size = len(self.graph.intersections)

    def _intersections_near(self, lat: float, lon: float) -> List[str]:
        self._ensure_index()
        blat, blon = self._bucket(lat, lon)
        out: List[str] = []
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                for iid in self._index.get((blat + dlat, blon + dlon), []):
                    inter = self.graph.intersections.get(iid)
                    if inter is None:
                        continue
                    if (abs(inter.lat - lat) <= self.radius_deg
                            and abs(inter.lon - lon) <= self.radius_deg):
                        out.append(iid)
        return out

    # -- sample ingestion ----------------------------------------------------

    def _add_sample(self, iid: str, source_id: str, speed_mph: float,
                    weight: float, at: float) -> None:
        self._samples.setdefault(iid, {})[source_id] = (
            float(speed_mph), float(weight), float(at))

    def ingest_vehicles(self, vehicles: List[TransitVehicle],
                        now: Optional[float] = None) -> int:
        """Feed live bus GPS fixes. Dwelling (≤0.5 mph) and stale (>120 s)
        fixes are skipped. Returns the number of samples recorded."""
        now = now if now is not None else time.time()
        count = 0
        for v in vehicles:
            if v.speed_mph <= 0.5:
                continue
            if now - v.last_update > STALE_FIX_S:
                continue
            for iid in self._intersections_near(v.lat, v.lon):
                self._add_sample(iid, f"bus:{v.id}", v.speed_mph, 1.0,
                                 v.last_update)
                count += 1
        return count

    def ingest_flow(self, flows: List[Dict[str, Any]],
                    now: Optional[float] = None) -> int:
        """Feed optional WSDOT flow records ``{lat, lon, speed_mph, ...}``
        as high-weight samples (weight 3 vs bus weight 1)."""
        now = now if now is not None else time.time()
        count = 0
        for i, f in enumerate(flows):
            try:
                lat, lon = float(f["lat"]), float(f["lon"])
                speed = float(f["speed_mph"])
            except (KeyError, TypeError, ValueError):
                continue
            station = str(f.get("id", i))
            for iid in self._intersections_near(lat, lon):
                self._add_sample(iid, f"flow:{station}", speed, 3.0, now)
                count += 1
        self.flow_active = count > 0
        return count

    # -- estimation -----------------------------------------------------------

    def compute(self, now: Optional[float] = None) -> Dict[str, float]:
        """Compute congestion for every intersection with enough fresh
        weighted samples. ``congestion = clamp(1 - median_speed/limit)``."""
        now = now if now is not None else time.time()
        cutoff = now - self.fresh_window_s
        results: Dict[str, float] = {}
        for iid, sources in list(self._samples.items()):
            # Drop expired samples in place.
            fresh = {sid: s for sid, s in sources.items() if s[2] >= cutoff}
            if fresh:
                self._samples[iid] = fresh
            else:
                self._samples.pop(iid, None)
                continue
            total_weight = sum(s[1] for s in fresh.values())
            if total_weight < self.min_samples:
                continue
            inter = self.graph.intersections.get(iid)
            if inter is None:
                continue
            # Weighted median via expansion (weights are small integers).
            expanded: List[float] = []
            for speed, weight, _at in fresh.values():
                expanded.extend([speed] * max(1, int(round(weight))))
            median_speed = statistics.median(expanded)
            limit = speed_limit_estimate(inter.name)
            congestion = min(1.0, max(0.0, 1.0 - median_speed / limit))
            results[iid] = round(congestion, 3)
            self._estimates[iid] = (results[iid], now)
        return results

    def fresh_ids(self, now: Optional[float] = None) -> Set[str]:
        """Intersections with an estimate newer than ``fresh_window_s``."""
        now = now if now is not None else time.time()
        cutoff = now - self.fresh_window_s
        return {iid for iid, (_c, at) in self._estimates.items()
                if at >= cutoff}

    def apply(self, graph: CityGraph, now: Optional[float] = None) -> int:
        """Write fresh estimates through to the graph. Returns count."""
        applied = 0
        for iid in self.fresh_ids(now):
            congestion = self._estimates[iid][0]
            try:
                graph.update_congestion(iid, congestion)
                applied += 1
            except KeyError:
                continue
        return applied

    def stats(self) -> Dict[str, Any]:
        return {
            "tracked_intersections": len(self._samples),
            "fresh_estimates": len(self.fresh_ids()),
            "flow_active": self.flow_active,
        }