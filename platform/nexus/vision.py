"""
Nexus City OS — AI Vision Sweep (Phase 2).

Background sweep that runs REAL multimodal AI vision (Claude Haiku) over
live traffic-camera frames and feeds genuine detections into the existing
incident pipeline. Detections enter through the same telemetry bus topic
as the edge layer, so the privacy gate, congestion update, incident
deduplication, and audit trail are reused unchanged — the only difference
is ``source="ai_vision"`` on the telemetry (→ ``Incident.detection_source``).

Design for testability:
  * ``analyze_fn(frame_bytes, context) -> dict`` and
    ``frame_fn(live_camera_id) -> Optional[bytes]`` are injectable;
    defaults wrap ``engine.copilot.analyze_frame`` and
    ``adapter.live.camera_image``.
  * ``sweep_once()`` is public and thread-free — the daemon thread simply
    calls it in a loop. Tests drive ``sweep_once()`` directly with fakes.
  * Every failure (LLM down, camera 404) marks the sweep degraded and
    NEVER raises out of the loop (graceful-degradation invariant).
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from .models import EdgeTelemetry, now_ts

CONFIDENCE_FLOOR_PCT = 70.0
CONGESTION_PRIORITY = 0.45
# congestion_visible keyword → congestion index
CONGESTION_LEVELS = {"high": 0.8, "moderate": 0.5, "low": 0.2}


def map_anomaly(assessment: str) -> str:
    """Map a vision assessment to an IncidentType value."""
    text = (assessment or "").lower()
    if "collision" in text or "crash" in text or "accident" in text:
        return "collision"
    if "stalled" in text or "stopped" in text or "disabled" in text:
        return "stopped_vehicle"
    return "congestion"


class VisionSweep:
    """Periodic AI-vision sweep over live camera frames."""

    def __init__(self, engine, adapter, interval_s: float = 120.0,
                 per_sweep: int = 6,
                 analyze_fn: Optional[Callable[[bytes, str], Dict]] = None,
                 frame_fn: Optional[Callable[[str], Optional[bytes]]] = None,
                 ) -> None:
        self.engine = engine
        self.adapter = adapter
        self.interval_s = interval_s
        self.per_sweep = per_sweep
        self.analyze_fn = analyze_fn or engine.copilot.analyze_frame
        self.frame_fn = frame_fn or self._default_frame_fn
        self.frames_analyzed = 0
        self.incidents_raised = 0
        self.degraded_count = 0
        self.last_sweep_at: float = 0.0
        self.last_error: Optional[str] = None
        self._rr_index = 0          # round-robin cursor through cameras
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- defaults ----------------------------------------------------------

    def _default_frame_fn(self, live_id: str) -> Optional[bytes]:
        live = getattr(self.adapter, "live", None)
        if live is None:
            return None
        result = live.camera_image(live_id, force_refresh=True)
        return result[0] if result else None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() \
            and not self._stop.is_set()

    def _loop(self) -> None:
        # Small initial delay so the platform settles before the first sweep.
        self._stop.wait(30.0)
        while not self._stop.is_set():
            try:
                self.sweep_once()
            except Exception:  # noqa: BLE001 — never kill the thread
                traceback.print_exc()
                self.degraded_count += 1
            self._stop.wait(self.interval_s)

    # -- camera selection ---------------------------------------------------

    def _pick_cameras(self) -> List[Dict[str, Any]]:
        """Pick up to ``per_sweep`` cameras: congested intersections
        (congestion > 0.45) first, then round-robin through the rest."""
        cam_map = getattr(self.adapter, "live_camera_map", {}) or {}
        if not cam_map:
            return []
        entries = []   # (cam_id, meta, congestion)
        for cam_id, meta in cam_map.items():
            inter = self.engine.graph.intersections.get(
                meta.get("intersection_id"))
            cong = inter.congestion if inter else 0.0
            entries.append((cam_id, meta, cong))
        hot = sorted((e for e in entries if e[2] > CONGESTION_PRIORITY),
                     key=lambda e: e[2], reverse=True)
        picked: List[Dict[str, Any]] = []
        seen = set()
        for cam_id, meta, _c in hot[:self.per_sweep]:
            picked.append({"cam_id": cam_id, **meta})
            seen.add(cam_id)
        # round-robin fill from the full list
        all_ids = sorted(cam_map.keys())
        n = len(all_ids)
        i = 0
        while len(picked) < self.per_sweep and i < n:
            cam_id = all_ids[(self._rr_index + i) % n]
            i += 1
            if cam_id in seen:
                continue
            picked.append({"cam_id": cam_id, **cam_map[cam_id]})
            seen.add(cam_id)
        self._rr_index = (self._rr_index + i) % max(1, n)
        return picked

    # -- one sweep ------------------------------------------------------------

    def sweep_once(self) -> Dict[str, Any]:
        """Analyze up to ``per_sweep`` live frames; publish redacted
        telemetry for each result. Returns sweep stats. Never raises."""
        self.last_sweep_at = time.time()
        analyzed = 0
        raised = 0
        for cam in self._pick_cameras():
            iid = cam.get("intersection_id", "")
            try:
                frame = self.frame_fn(cam.get("live_id", ""))
                if not frame:
                    self.degraded_count += 1
                    continue
                inter = self.engine.graph.intersections.get(iid)
                context = (f"Routine AI sweep. Camera at "
                           f"{cam.get('name', iid)}. Platform congestion "
                           f"index {inter.congestion:.0%}."
                           if inter else "Routine AI sweep.")
                analysis = self.analyze_fn(frame, context)
                if not isinstance(analysis, dict) \
                        or not analysis.get("available", False):
                    self.degraded_count += 1
                    self.last_error = str(
                        (analysis or {}).get("error", "vision unavailable"))
                    continue
                analyzed += 1
                self.frames_analyzed += 1
                raised += self._publish(cam, analysis)
            except Exception as exc:  # noqa: BLE001 — degrade, never crash
                self.degraded_count += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
        return {"analyzed": analyzed, "incidents_raised": raised,
                "at": self.last_sweep_at}

    def _publish(self, cam: Dict[str, Any], analysis: Dict[str, Any]) -> int:
        """Translate a vision result into redacted EdgeTelemetry on the
        engine's telemetry topic. Returns 1 if an anomaly was raised."""
        iid = cam.get("intersection_id", "")
        cong_word = str(analysis.get("congestion_visible", "")).lower()
        congestion = CONGESTION_LEVELS.get(cong_word, 0.2)
        confidence = float(analysis.get("confidence_pct", 0) or 0)
        incident_visible = bool(analysis.get("incident_visible"))
        anomaly: Optional[str] = None
        if incident_visible and confidence >= CONFIDENCE_FLOOR_PCT:
            anomaly = map_anomaly(str(analysis.get("assessment", "")))
        telemetry = EdgeTelemetry(
            camera_id=cam.get("cam_id", ""),
            intersection_id=iid,
            captured_at=now_ts(),
            vehicle_count=10,
            avg_speed_mph=round(25.0 * (1.0 - congestion), 1),
            stopped_vehicles=int(congestion * 5),
            anomaly=anomaly,
            redacted=True,             # AI sees only the public frame
            source="ai_vision",
        )
        self.engine.bus.publish(self.engine.telemetry_topic,
                                telemetry.to_json())
        if anomaly:
            self.incidents_raised += 1
            self.engine.audit.record(
                actor="ai_vision_sweep", action="vision_detection",
                targets=[iid],
                detail=(f"{anomaly} ({confidence:.0f}% conf): "
                        + str(analysis.get("assessment", ""))[:200]),
                after_state={"anomaly": anomaly,
                             "confidence_pct": confidence,
                             "camera": cam.get("name", "")})
            return 1
        return 0

    # -- stats -----------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "frames_analyzed": self.frames_analyzed,
            "incidents_raised": self.incidents_raised,
            "last_sweep_at": self.last_sweep_at,
            "interval_s": self.interval_s,
            "per_sweep": self.per_sweep,
            "degraded": self.degraded_count > 0,
            "degraded_count": self.degraded_count,
            "last_error": self.last_error,
        }