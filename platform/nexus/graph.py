"""
Nexus City OS — The Living City Graph.

Thread-safe, in-process graph store behind the ``CityGraph`` interface
(Pipeline B, MASTER_PROMPT §3). Production deployments may swap in Neo4j —
all access goes through this interface, so storage is swappable.

Supports:
  * Strongly-typed entity nodes with extensible entity types.
  * Adjacency edges with dynamic weights (CONGESTION_INDEX, CURRENT_TRAVEL_TIME).
  * Cascading Dependency Resolution: BFS up to N hops from a blocked node,
    returning downstream intersections with estimated time-to-gridlock.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
    Camera,
    Incident,
    Intersection,
    RoadSegment,
    TransitVehicle,
    WeatherCondition,
)


class CityGraph:
    """Thread-safe living graph of the city model."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.intersections: Dict[str, Intersection] = {}
        self.segments: Dict[str, RoadSegment] = {}
        self.vehicles: Dict[str, TransitVehicle] = {}
        self.cameras: Dict[str, Camera] = {}
        self.incidents: Dict[str, Incident] = {}
        self.weather: Optional[WeatherCondition] = None
        # adjacency: intersection_id -> list[(neighbor_id, segment_id)]
        self._adjacency: Dict[str, List[Tuple[str, str]]] = {}
        # extensible entity registry (PRD §1.2 extensibility): type -> {id: obj}
        self._extensions: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Mutations (transactional under the lock)
    # ------------------------------------------------------------------

    def add_intersection(self, node: Intersection) -> None:
        with self._lock:
            self.intersections[node.id] = node
            self._adjacency.setdefault(node.id, [])

    def add_segment(self, seg: RoadSegment) -> None:
        with self._lock:
            if seg.from_intersection not in self.intersections:
                raise KeyError(f"Unknown intersection {seg.from_intersection}")
            if seg.to_intersection not in self.intersections:
                raise KeyError(f"Unknown intersection {seg.to_intersection}")
            self.segments[seg.id] = seg
            self._adjacency.setdefault(seg.from_intersection, []).append(
                (seg.to_intersection, seg.id))
            self._adjacency.setdefault(seg.to_intersection, []).append(
                (seg.from_intersection, seg.id))

    def add_vehicle(self, v: TransitVehicle) -> None:
        with self._lock:
            self.vehicles[v.id] = v

    def add_camera(self, cam: Camera) -> None:
        with self._lock:
            self.cameras[cam.id] = cam
            inter = self.intersections.get(cam.intersection_id)
            if inter is not None:
                inter.monitored = True

    def add_incident(self, inc: Incident) -> None:
        with self._lock:
            self.incidents[inc.id] = inc

    def set_weather(self, w: WeatherCondition) -> None:
        with self._lock:
            self.weather = w

    def register_entity(self, entity_type: str, entity_id: str, obj: Any) -> None:
        """Extensible entity registration — new types without schema breaks."""
        with self._lock:
            self._extensions.setdefault(entity_type, {})[entity_id] = obj

    def update_segment_speed(self, segment_id: str, speed_mph: float) -> None:
        with self._lock:
            seg = self.segments.get(segment_id)
            if seg is None:
                raise KeyError(f"Unknown segment {segment_id}")
            seg.current_speed_mph = max(0.0, float(speed_mph))

    def update_congestion(self, intersection_id: str, congestion: float) -> None:
        with self._lock:
            inter = self.intersections.get(intersection_id)
            if inter is None:
                raise KeyError(f"Unknown intersection {intersection_id}")
            inter.congestion = min(1.0, max(0.0, float(congestion)))

    def update_vehicle(self, vehicle_id: str, lat: float, lon: float,
                       speed_mph: float, ts: float) -> None:
        with self._lock:
            v = self.vehicles.get(vehicle_id)
            if v is None:
                raise KeyError(f"Unknown vehicle {vehicle_id}")
            v.lat, v.lon, v.speed_mph, v.last_update = lat, lon, speed_mph, ts

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def has_intersection(self, intersection_id: str) -> bool:
        with self._lock:
            return intersection_id in self.intersections

    def get_intersection(self, intersection_id: str) -> Intersection:
        with self._lock:
            inter = self.intersections.get(intersection_id)
            if inter is None:
                raise KeyError(f"Unknown intersection {intersection_id}")
            return inter

    def neighbors(self, intersection_id: str) -> List[Tuple[str, str]]:
        with self._lock:
            return list(self._adjacency.get(intersection_id, []))

    def entity_exists(self, entity_id: str) -> bool:
        """Used by the hallucination monitor (PRD §4.5)."""
        with self._lock:
            if entity_id in self.intersections or entity_id in self.segments \
                    or entity_id in self.vehicles or entity_id in self.cameras \
                    or entity_id in self.incidents:
                return True
            return any(entity_id in reg for reg in self._extensions.values())

    def active_ems_intersections(self) -> Set[str]:
        """Intersections with an active EMS_RESPONDING incident attached,
        plus EMS corridor members (guardrail rule 7)."""
        with self._lock:
            out: Set[str] = set()
            for inc in self.incidents.values():
                if inc.status_flag.value == "EMS_RESPONDING" and \
                        inc.state.value not in ("resolved", "closed"):
                    out.add(inc.intersection_id)
            for inter in self.intersections.values():
                if inter.ems_corridor:
                    out.add(inter.id)
            return out

    # ------------------------------------------------------------------
    # Cascading Dependency Resolution (Pipeline B)
    # ------------------------------------------------------------------

    def cascading_impact(self, blocked_intersection_id: str,
                         max_hops: int = 3) -> List[Dict[str, Any]]:
        """BFS from a blocked intersection; estimates time-to-gridlock for
        each downstream intersection based on hop distance and current
        congestion. Returns nearest-first."""
        with self._lock:
            if blocked_intersection_id not in self.intersections:
                raise KeyError(f"Unknown intersection {blocked_intersection_id}")
            visited: Set[str] = {blocked_intersection_id}
            frontier: List[Tuple[str, int]] = [(blocked_intersection_id, 0)]
            impacts: List[Dict[str, Any]] = []
            while frontier:
                node_id, depth = frontier.pop(0)
                if depth >= max_hops:
                    continue
                for neighbor_id, segment_id in self._adjacency.get(node_id, []):
                    if neighbor_id in visited:
                        continue
                    visited.add(neighbor_id)
                    hop = depth + 1
                    inter = self.intersections[neighbor_id]
                    # Closer + more congested ⇒ faster gridlock. Base 4 min/hop,
                    # scaled down by existing congestion.
                    minutes = round(hop * 4.0 * (1.0 - 0.5 * inter.congestion), 1)
                    impacts.append({
                        "intersection_id": neighbor_id,
                        "name": inter.name,
                        "hops": hop,
                        "via_segment": segment_id,
                        "congestion": inter.congestion,
                        "est_minutes_to_gridlock": max(1.0, minutes),
                    })
                    frontier.append((neighbor_id, hop))
            impacts.sort(key=lambda x: x["est_minutes_to_gridlock"])
            return impacts

    def vehicles_near(self, intersection_id: str,
                      radius_deg: float = 0.01) -> List[TransitVehicle]:
        with self._lock:
            inter = self.intersections.get(intersection_id)
            if inter is None:
                return []
            return [v for v in self.vehicles.values()
                    if abs(v.lat - inter.lat) <= radius_deg
                    and abs(v.lon - inter.lon) <= radius_deg]

    # ------------------------------------------------------------------
    # Snapshot for the UI
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "intersections": [{
                    "id": i.id, "name": i.name, "lat": i.lat, "lon": i.lon,
                    "monitored": i.monitored, "congestion": round(i.congestion, 3),
                    "ems_corridor": i.ems_corridor,
                    "cycle_seconds": i.timing_plan.cycle_seconds,
                } for i in self.intersections.values()],
                "segments": [{
                    "id": s.id, "from": s.from_intersection,
                    "to": s.to_intersection, "name": s.name,
                    "speed_limit_mph": s.speed_limit_mph,
                    "current_speed_mph": round(s.current_speed_mph, 1),
                } for s in self.segments.values()],
                "vehicles": [{
                    "id": v.id, "route": v.route, "lat": v.lat, "lon": v.lon,
                    "speed_mph": round(v.speed_mph, 1),
                    "last_update": v.last_update,
                } for v in self.vehicles.values()],
                "cameras": [{
                    "id": c.id, "intersection_id": c.intersection_id,
                    "online": c.online, "last_frame_ts": c.last_frame_ts,
                } for c in self.cameras.values()],
                "weather": ({
                    "condition": self.weather.condition,
                    "temperature_f": self.weather.temperature_f,
                    "severe_alert": self.weather.severe_alert,
                } if self.weather else None),
            }