"""
Nexus City OS — Edge layer simulator (Pipeline A).

Simulates the per-camera edge processing devices: vehicle counting,
stopped-vehicle / collision / wrong-way detection, and — critically —
PII redaction before anything leaves the device (PRD §11.6: raw video
never leaves the edge).

Emits structured JSON ``EdgeTelemetry`` payloads to the telemetry bus on
``city.<city_id>.edge.telemetry``.

The simulator is deterministic given a seed, and supports scenario
injection (e.g., "multi-vehicle collision at INT-0023") so demos and tests
can exercise the full detection → recommendation → approval workflow.
"""
from __future__ import annotations

import random
import threading
from typing import Dict, List, Optional

from .bus import TelemetryBus
from .graph import CityGraph
from .models import EdgeTelemetry, IncidentType, now_ts


class EdgeSimulator:
    """Simulated fleet of edge processing devices, one per camera."""

    def __init__(self, graph: CityGraph, bus: TelemetryBus,
                 city_id: str, seed: int = 7) -> None:
        self._graph = graph
        self._bus = bus
        self._topic = f"city.{city_id}.edge.telemetry"
        self._rng = random.Random(seed)
        self._lock = threading.RLock()
        # scenario injections: intersection_id -> anomaly type
        self._injected: Dict[str, str] = {}

    def inject_scenario(self, intersection_id: str,
                        anomaly: IncidentType) -> None:
        """Force the next tick's telemetry at this intersection to carry
        the given anomaly (demo/test scenario injection)."""
        with self._lock:
            self._injected[intersection_id] = anomaly.value

    def clear_scenarios(self) -> None:
        with self._lock:
            self._injected.clear()

    def tick(self) -> List[EdgeTelemetry]:
        """One capture cycle across all online cameras. Returns the emitted
        telemetry batch (already published to the bus)."""
        emitted: List[EdgeTelemetry] = []
        with self._lock:
            injected = dict(self._injected)
            self._injected.clear()

        for cam in list(self._graph.cameras.values()):
            if not cam.online:
                continue
            anomaly: Optional[str] = injected.get(cam.intersection_id)
            if anomaly is not None:
                avg_speed = self._rng.uniform(0.0, 3.0)
                stopped = self._rng.randint(4, 9)
                count = self._rng.randint(15, 30)
            else:
                try:
                    inter = self._graph.get_intersection(cam.intersection_id)
                    # Mean-reverting coupling (0.5 factor) keeps the
                    # speed↔congestion loop stable: base speed never drops
                    # below 12.5 mph for organic traffic, so spurious
                    # anomaly cascades cannot occur.
                    base_speed = 25.0 * (1.0 - 0.5 * inter.congestion)
                except KeyError:
                    base_speed = 18.0
                avg_speed = max(0.0, self._rng.gauss(base_speed, 2.5))
                stopped = self._rng.randint(0, 2)
                count = self._rng.randint(4, 18)
                # organic anomaly: near-total stop observed by the edge CV
                # model (rare for organic traffic; injected scenarios force
                # speeds in the 0–3 mph range)
                if avg_speed < 2.0 and stopped >= 2:
                    anomaly = IncidentType.STOPPED_VEHICLE.value

            telemetry = EdgeTelemetry(
                camera_id=cam.id,
                intersection_id=cam.intersection_id,
                captured_at=now_ts(),
                vehicle_count=count,
                avg_speed_mph=round(avg_speed, 1),
                stopped_vehicles=stopped,
                anomaly=anomaly,
                # Redaction always on at the edge; a camera with redaction
                # disabled emits payloads the platform will REJECT.
                redacted=cam.redaction_enabled,
            )
            self._bus.publish(self._topic, telemetry.to_json())
            emitted.append(telemetry)
        return emitted