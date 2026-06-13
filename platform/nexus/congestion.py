"""
Nexus City OS — Real congestion estimation (Phase 1, research-calibrated).

Derives per-intersection congestion from REAL observed speeds instead of
the edge-simulator random walk:

  * Live transit GPS (King County Metro / Pierce Transit buses already
    flowing through ``PlatformRuntime.tick()``) — every moving bus is a
    probe vehicle reporting street speed.
  * Optional WSDOT loop-detector flow data (``SeattleLiveData.flow_speeds``,
    enabled by the ``WSDOT_ACCESS_CODE`` env var) — high-weight samples.

CALIBRATION (what the probe-vehicle literature says — Portland State's
high-resolution bus-GPS accuracy study, FHWA Probe Vehicle Techniques
handbook, bus dwell-time modeling work):

  1. Bus speeds BETWEEN stops correlate strongly with general traffic
     speed, but instantaneous fixes near stops are biased far LOW by
     dwell, deceleration, and acceleration. → For bus sources we keep the
     MAX observed speed per vehicle within the freshness window: the
     fastest a bus moved past an intersection is the best evidence of
     what traffic allowed (dwell reads are pessimistic noise).
  2. Buses never reach the posted limit even in free flow (stops, curb
     pullouts, conservative operation). Scoring buses against the raw
     limit makes free-flow look ~25% congested. → Bus samples are
     normalized to a bus free-flow speed = limit × BUS_FREEFLOW_FACTOR
     (0.75). Loop-detector flow records measure general traffic and use
     the raw limit.
  3. Congestion is the normalized position between free flow and jam
     (≈3 mph crawl), the standard speed-based mapping:
         ratio      = speed / source_free_flow_speed
         congestion = clamp((1 − ratio) / (1 − JAM_RATIO))
     so a bus cruising at 19 mph on a 25 mph arterial reads as FREE FLOW
     (congestion 0), and a 3 mph crawl reads ≈1.0.

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
  * The median (weighted) speed ratio is used, not the mean — robust to a
    single outlier probe.

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
# Buses in free flow cruise at ~75% of the posted limit between stops
# (dwell/decel/accel and conservative operation) — probe literature.
BUS_FREEFLOW_FACTOR = 0.75
# Jam crawl ≈ 3 mph on a 25 mph arterial → ratio 0.12. Congestion is the
# normalized position between free flow (ratio 1.0) and jam.
JAM_RATIO = 0.12


def speed_limit_estimate(intersection_name: str) -> float:
    """25 mph for surface streets; 55 mph when the intersection name
    references a highway (WSDOT cams use "@" — e.g. "I-5 @ NE 195th St")."""
    name = intersection_name or ""
    if any(marker in name for marker in HIGHWAY_MARKERS):
        return HIGHWAY_SPEED_MPH
    return SURFACE_SPEED_MPH


def ratio_to_congestion(ratio: float) -> float:
    """Map a speed/free-flow ratio to congestion ∈ [0, 1].

    ratio ≥ 1.0 → 0 (free flow); ratio ≤ JAM_RATIO → 1.0 (jam);
    linear in between (standard speed-based normalization)."""
    return min(1.0, max(0.0, (1.0 - ratio) / (1.0 - JAM_RATIO)))


class CongestionEstimator:
    """Aggregates real speed observations into per-intersection congestion."""

    def __init__(self, graph: CityGraph, fresh_window_s: float = 180.0,
                 min_samples: int = 2, radius_deg: float = 0.008) -> None:
        self.graph = graph
        self.fresh_window_s = fresh_window_s
        self.min_samples = min_samples
        self.radius_deg = radius_deg
        # intersection_id -> {source_id: (speed_ratio, weight, at)}
        self._samples: Dict[str, Dict[str, Tuple[float, float, float]]] = {}
        # intersection_id -> (congestion, computed_at)
        self._estimates: Dict[str, Tuple[float, float]] = {}
        # intersection_id -> {confidence, sources, n_sources, kind}
        self._meta: Dict[str, Dict[str, Any]] = {}
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

    def _free_flow_mph(self, iid: str, is_bus: bool) -> Optional[float]:
        inter = self.graph.intersections.get(iid)
        if inter is None:
            return None
        limit = speed_limit_estimate(inter.name)
        return limit * (BUS_FREEFLOW_FACTOR if is_bus else 1.0)

    def _add_sample(self, iid: str, source_id: str, ratio: float,
                    weight: float, at: float,
                    keep_max: bool = False) -> None:
        """Record a speed-ratio sample. ``keep_max`` retains the highest
        ratio per source within the freshness window (dwell-bias guard for
        bus probes: the fastest fix is the best evidence of what traffic
        allowed); the timestamp always advances to the newest fix."""
        sources = self._samples.setdefault(iid, {})
        if keep_max:
            prev = sources.get(source_id)
            if prev is not None and prev[2] >= at - self.fresh_window_s:
                ratio = max(ratio, prev[0])
        sources[source_id] = (float(ratio), float(weight), float(at))

    def ingest_vehicles(self, vehicles: List[TransitVehicle],
                        now: Optional[float] = None) -> int:
        """Feed live bus GPS fixes. Dwelling (≤0.5 mph) and stale (>120 s)
        fixes are skipped; per-vehicle MAX speed in the window is retained
        (dwell-robust). Returns the number of samples recorded."""
        now = now if now is not None else time.time()
        count = 0
        for v in vehicles:
            if v.speed_mph <= 0.5:
                continue
            if now - v.last_update > STALE_FIX_S:
                continue
            for iid in self._intersections_near(v.lat, v.lon):
                free_flow = self._free_flow_mph(iid, is_bus=True)
                if not free_flow:
                    continue
                self._add_sample(iid, f"bus:{v.id}",
                                 v.speed_mph / free_flow, 1.0,
                                 v.last_update, keep_max=True)
                count += 1
        return count

    def ingest_flow(self, flows: List[Dict[str, Any]],
                    now: Optional[float] = None) -> int:
        """Feed optional WSDOT flow records ``{lat, lon, speed_mph, ...}``
        as high-weight samples (weight 3 vs bus weight 1). Loop detectors
        measure general traffic → scored against the raw limit."""
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
                free_flow = self._free_flow_mph(iid, is_bus=False)
                if not free_flow:
                    continue
                self._add_sample(iid, f"flow:{station}",
                                 speed / free_flow, 3.0, now)
                count += 1
        self.flow_active = count > 0
        return count

    # -- estimation -----------------------------------------------------------

    def compute(self, now: Optional[float] = None) -> Dict[str, float]:
        """Compute congestion for every intersection with enough fresh
        weighted samples (weighted-median speed ratio → normalized
        congestion via ``ratio_to_congestion``)."""
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
            if iid not in self.graph.intersections:
                continue
            # Weighted median via expansion (weights are small integers).
            expanded: List[float] = []
            for ratio, weight, _at in fresh.values():
                expanded.extend([ratio] * max(1, int(round(weight))))
            median_ratio = statistics.median(expanded)
            congestion = round(ratio_to_congestion(median_ratio), 3)
            results[iid] = congestion
            self._estimates[iid] = (congestion, now)
            # Confidence: more independent sources + higher-weight (loop)
            # data → higher confidence. WSDOT loops alone are authoritative;
            # a lone bus pair is a weak directional signal.
            has_flow = any(sid.startswith("flow:") for sid in fresh)
            n_sources = len(fresh)
            confidence = min(1.0, 0.35 + 0.18 * n_sources
                             + (0.25 if has_flow else 0.0))
            self._meta[iid] = {
                "confidence": round(confidence, 2),
                "n_sources": n_sources,
                "kind": "loop+probe" if has_flow else "bus_probe",
            }
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

    def meta(self, iid: str) -> Optional[Dict[str, Any]]:
        """Per-intersection estimate metadata (confidence, source kind,
        number of independent sources), or None if no fresh estimate."""
        if iid in self.fresh_ids():
            return self._meta.get(iid)
        return None

    def stats(self) -> Dict[str, Any]:
        fresh = self.fresh_ids()
        confidences = [self._meta[i]["confidence"]
                       for i in fresh if i in self._meta]
        return {
            "tracked_intersections": len(self._samples),
            "fresh_estimates": len(fresh),
            "flow_active": self.flow_active,
            "mean_confidence": round(
                sum(confidences) / len(confidences), 2)
            if confidences else 0.0,
        }
