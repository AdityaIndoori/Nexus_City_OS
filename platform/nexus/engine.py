"""
Nexus City OS — Platform Engine.

The central orchestrator wiring together the city graph, telemetry bus,
AI copilot, safety gate, simulator, and audit trail. Owns:

  * The operating-mode ladder: Shadow → Advisory → Live (PRD Scope Variants).
    There is NO code path that executes a physical mutation in Shadow or
    Advisory mode.
  * Incident lifecycle management (PRD §2): Detected → Acknowledged →
    Mitigating → Monitoring → Resolved → Closed.
  * The HITL approval workflow (PRD §5): generate → safety gate → simulate →
    operator approves → execute (mode-dependent) → monitor → rollback.
  * Manual rollback and automatic rollback monitoring (PRD §6).
  * RBAC enforcement on every privileged action (PRD §11.2).
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from .audit import AuditTrail
from .bus import TelemetryBus
from .copilot import Copilot
from .graph import CityGraph
from .store import Store
from .models import (
    ActionPlan,
    EdgeTelemetry,
    FRESHNESS_THRESHOLDS,
    Incident,
    IncidentState,
    IncidentType,
    OperatingMode,
    PlanStatus,
    Role,
    new_id,
    now_ts,
)
from .safety import SafetyGate
from .simulation import simulate_impact

ADVISORY_EXPIRATION_S = 15 * 60.0       # PRD §5 instruction expiration
AUTO_REVERT_WORSEN_PCT = 20.0           # PRD §6.2 (configurable)
AUTO_REVERT_WINDOW_S = 5 * 60.0         # PRD §6.2 (configurable)
# An alerted (worsening) change is held registered for operator action;
# if no rollback happens within this hard cap it settles anyway so the
# R6 concurrency budget is never permanently consumed.
ALERTED_SETTLE_S = 30 * 60.0
SPEED_ANOMALY_FRACTION = 0.15           # speed < 15% of limit ⇒ anomaly
# Working-memory bounds (durable history lives in the Store).
MAX_PLANS_IN_MEMORY = 400
FRAME_RETENTION_AFTER_RESOLVE_S = 3600.0    # free frozen jpegs after 1 h
INCIDENT_RETENTION_S = 7 * 86400.0          # drop resolved incidents > 7 d

# Plans in one of these states are finished — safe to prune from memory.
_TERMINAL_PLAN_STATUSES = {
    PlanStatus.REJECTED, PlanStatus.REVERTED, PlanStatus.EXPIRED,
    PlanStatus.SHADOW_LOGGED, PlanStatus.BLOCKED_CONSTRAINT,
    PlanStatus.BLOCKED_HALLUCINATION, PlanStatus.SUPPRESSED_PROVENANCE,
    PlanStatus.WITHHELD_CONFIDENCE,
}


class PermissionDenied(Exception):
    pass


class NexusEngine:
    """Singleton-style platform engine (one per deployment)."""

    def __init__(self, city_id: str = "seattle",
                 store: Optional[Store] = None,
                 use_llm: bool = False) -> None:
        self.city_id = city_id
        self._lock = threading.RLock()
        self.store = store
        self.graph = CityGraph()
        self.bus = TelemetryBus()
        self.audit = AuditTrail(store=store)
        self.copilot = Copilot(self.graph, use_llm=use_llm)
        self.safety = SafetyGate(self.graph)
        # Governance state survives restarts: a crash must never silently
        # reset the operating mode (a Live deployment that rebooted into
        # Shadow would be safe; the reverse would be catastrophic — so we
        # restore exactly what an Admin last authorized, audit-logged).
        self.mode = OperatingMode.SHADOW   # every city starts in Shadow
        if store is not None:
            saved = store.get_kv("operating_mode")
            if saved:
                self.mode = OperatingMode(saved)
            saved_threshold = store.get_kv("confidence_threshold")
            if saved_threshold is not None:
                self.safety.confidence_threshold = float(saved_threshold)
        self.plans: Dict[str, ActionPlan] = {}
        self.feed_last_update: Dict[str, float] = {}
        self.alerts: List[Dict[str, Any]] = []
        # users: user_id -> role (reference impl; production uses SSO+MFA)
        self.users: Dict[str, Role] = {
            "op-1": Role.OPERATOR,
            "analyst-1": Role.ANALYST,
            "admin-1": Role.ADMIN,
            "viewer-1": Role.VIEWER,
        }
        # rollback monitoring: plan_id -> baseline congestion per intersection
        self._monitoring: Dict[str, Dict[str, Any]] = {}
        # Optional hook the runtime sets so the engine can freeze a
        # detection-time camera frame for edge/911 incidents (the vision
        # sweep already attaches its own frame). Signature: (camera_id) ->
        # Optional[bytes]. Left None in tests / offline runs.
        self.frame_capture_fn = None
        # Resolve the *live* camera identity (name + feed id) for a platform
        # camera_id, so the incident card can name the exact camera the
        # frozen frame came from. Several physically-distinct live cameras can
        # share one intersection (different viewing directions), so the
        # intersection name alone is ambiguous. Signature:
        # (camera_id) -> Optional[dict(name, live_id, type)]. Set by runtime.
        self.camera_meta_fn = None


        # intersections with a FRESH real-data congestion estimate (bus GPS /
        # WSDOT flow) — the simulator must not overwrite these (Phase 1).
        # Updated by the runtime each tick; empty by default so offline
        # deployments and tests behave exactly as before.
        self.real_congestion_ids: set = set()
        # 911 dispatch ids already correlated into incidents (M2 dedupe)
        self._correlated_911: set = set()
        # congestion history sampling throttle (Phase 3)
        self._last_history_at: float = 0.0
        # real-time event hub (SSE push): seq bump + condvar wakeup
        self._event_cond = threading.Condition()
        self.event_seq = 0
        topic = f"city.{self.city_id}.edge.telemetry"
        self.bus.subscribe(topic, self._on_edge_telemetry)
        self.telemetry_topic = topic
        self.audit.record(actor="system", action="platform_start",
                          detail=f"mode={self.mode.value}"
                                 + (" (restored from store)"
                                    if store is not None else ""))

    # ------------------------------------------------------------------
    # Real-time event hub (server push)
    # ------------------------------------------------------------------

    def emit_event(self, kind: str) -> None:
        """Signal state change to any waiting SSE streams."""
        with self._event_cond:
            self.event_seq += 1
            self._event_cond.notify_all()

    def wait_for_event(self, last_seq: int, timeout: float = 25.0) -> int:
        """Block until event_seq advances past last_seq (or timeout)."""
        with self._event_cond:
            self._event_cond.wait_for(
                lambda: self.event_seq > last_seq, timeout=timeout)
            return self.event_seq

    # ------------------------------------------------------------------
    # Persistence write-through
    # ------------------------------------------------------------------

    def _persist_incident(self, inc: Incident) -> None:
        if self.store is None:
            return
        self.store.upsert_incident(inc.id, inc.state.value, {
            "id": inc.id, "type": inc.type.value,
            "intersection_id": inc.intersection_id,
            "severity": inc.severity, "state": inc.state.value,
            "detected_at": inc.detected_at,
            "resolution": inc.resolution,
            "description": inc.description,
            "detection_source": inc.detection_source,
        }, now_ts())

    def _persist_plan(self, plan: ActionPlan) -> None:
        if self.store is None:
            return
        self.store.upsert_plan(plan.plan_id, plan.status.value,
                               plan.incident_id, plan.to_dict(), now_ts())

    # ------------------------------------------------------------------
    # RBAC
    # ------------------------------------------------------------------

    def _require(self, user_id: str, *roles: Role) -> Role:
        role = self.users.get(user_id)
        if role is None:
            raise PermissionDenied(f"Unknown user {user_id}")
        if role not in roles:
            self.audit.record(actor=user_id, action="permission_denied",
                              outcome="denied",
                              detail=f"required={[r.value for r in roles]}, "
                                     f"actual={role.value}")
            raise PermissionDenied(
                f"Role {role.value} cannot perform this action "
                f"(requires {' or '.join(r.value for r in roles)}).")
        return role

    # ------------------------------------------------------------------
    # Operating-mode ladder
    # ------------------------------------------------------------------

    def set_mode(self, user_id: str, mode: OperatingMode) -> None:
        """Mode transitions require Admin authorization (PRD §7.1, §11.2)."""
        self._require(user_id, Role.ADMIN)
        with self._lock:
            before = self.mode
            self.mode = mode
        if self.store is not None:
            self.store.set_kv("operating_mode", mode.value)
        self.audit.record(actor=user_id, action="mode_transition",
                          before_state={"mode": before.value},
                          after_state={"mode": mode.value},
                          approval_chain=[user_id])
        self.emit_event("mode")

    def set_confidence_threshold(self, user_id: str, value: float) -> float:
        """Governed confidence-threshold adjustment (PRD §4.3): Admin-only,
        range-checked by the SafetyGate, persisted, audit-logged."""
        role = self._require(user_id, Role.ADMIN)
        before = self.safety.confidence_threshold
        self.safety.set_confidence_threshold(float(value),
                                             actor_role=role.value)
        if self.store is not None:
            self.store.set_kv("confidence_threshold", float(value))
        self.audit.record(actor=user_id,
                          action="confidence_threshold_changed",
                          before_state={"threshold": before},
                          after_state={"threshold": float(value)},
                          approval_chain=[user_id])
        self.emit_event("threshold")
        return float(value)

    # ------------------------------------------------------------------
    # Telemetry ingestion (Pipeline A)
    # ------------------------------------------------------------------

    def _on_edge_telemetry(self, message: Dict[str, Any]) -> None:
        telemetry = EdgeTelemetry.from_json(
            __import__("json").dumps(message))
        # Privacy gate: unredacted payloads are rejected (PRD §11.6).
        if not telemetry.redacted:
            self.audit.record(actor="ingestion", action="telemetry_rejected",
                              outcome="rejected",
                              detail=f"unredacted payload from "
                                     f"{telemetry.camera_id}")
            raise ValueError("Unredacted telemetry rejected at ingestion")

        with self._lock:
            self.feed_last_update["camera"] = telemetry.captured_at

        cam = self.graph.cameras.get(telemetry.camera_id)
        if cam is not None:
            cam.last_frame_ts = telemetry.captured_at

        # Update congestion from observed speed. The 0.9 damping factor keeps
        # the speed↔congestion feedback loop stable (fixed point ≈ 0.4·stopped
        # instead of saturating at 1.0): normal traffic hovers at moderate
        # congestion; genuine anomalies (speed≈0, many stopped) still drive
        # the index toward 1.0.
        #
        # Guard (Phase 1 — real congestion): when an intersection has a FRESH
        # real-data estimate (bus GPS / WSDOT flow), the simulator must NOT
        # overwrite it. Anomalous telemetry always drives congestion so
        # injected scenarios work everywhere.
        if self.graph.has_intersection(telemetry.intersection_id) and (
                telemetry.intersection_id not in self.real_congestion_ids
                or telemetry.anomaly):
            congestion = min(1.0, max(
                0.0,
                0.9 * (1.0 - telemetry.avg_speed_mph / 25.0)
                + 0.04 * telemetry.stopped_vehicles))
            self.graph.update_congestion(telemetry.intersection_id, congestion)

        if telemetry.anomaly:
            self._raise_incident(telemetry)

    def _raise_incident(self, telemetry: EdgeTelemetry) -> Optional[Incident]:
        try:
            itype = IncidentType(telemetry.anomaly)
        except ValueError:
            self.audit.record(actor="ingestion", action="anomaly_unknown",
                              outcome="ignored", detail=str(telemetry.anomaly))
            return None
        # Deduplicate: one active incident per intersection+type.
        for inc in self.graph.incidents.values():
            if (inc.intersection_id == telemetry.intersection_id
                    and inc.type == itype
                    and inc.state.value not in ("resolved", "closed")):
                return inc
        severity = {
            IncidentType.COLLISION: 0.9,
            IncidentType.WRONG_WAY_DRIVER: 0.95,
            IncidentType.PEDESTRIAN_ON_HIGHWAY: 0.85,
            IncidentType.STOPPED_VEHICLE: 0.5,
            IncidentType.TRANSIT_BREAKDOWN: 0.6,
            IncidentType.CONGESTION: 0.4,
        }.get(itype, 0.5)
        # Classification justification (PRD §4.2): for AI-vision detections
        # this is the Claude Haiku assessment carried on the telemetry — the
        # actual *why*. For the edge CV simulator it's a deterministic
        # explanation of the rule that fired.
        if telemetry.source == "ai_vision" and telemetry.ai_assessment:
            ai_justification = telemetry.ai_assessment
        else:
            ai_justification = (
                f"Classified as '{itype.value.replace('_', ' ')}' because the "
                f"edge computer-vision layer observed an average approach speed "
                f"of {telemetry.avg_speed_mph:.1f} mph with "
                f"{telemetry.stopped_vehicles} stopped vehicle(s) at camera "
                f"{telemetry.camera_id}. Speeds near zero with multiple "
                f"stationary vehicles match the stopped-vehicle/collision "
                f"signature rather than normal signal queueing.")
        # Freeze the detection-time frame (decode the base64 jpeg carried on
        # the telemetry). The operator must always see what the detector saw —
        # never a newer live image. If the detector didn't attach a frame
        # (edge simulator), best-effort fetch the current frame ONCE now and
        # freeze that as the detection-time evidence.
        frame_jpeg = None
        if telemetry.frame_b64:
            try:
                import base64 as _b64
                frame_jpeg = _b64.b64decode(telemetry.frame_b64)
            except Exception:  # noqa: BLE001
                frame_jpeg = None
        if frame_jpeg is None:
            frame_jpeg = self._capture_frame(telemetry.camera_id)
        incident = Incident(
            id=new_id("INC"),
            type=itype,
            intersection_id=telemetry.intersection_id,
            severity=severity,
            description=(f"{itype.value} detected by {telemetry.camera_id} "
                         f"(avg speed {telemetry.avg_speed_mph:.1f} mph, "
                         f"{telemetry.stopped_vehicles} stopped)"),
            detection_source=telemetry.source,
            camera_id=telemetry.camera_id,
            ai_justification=ai_justification,
            ai_confidence=telemetry.ai_confidence,
            detection_frame_jpeg=frame_jpeg,
        )
        self.graph.add_incident(incident)

        self.audit.record(actor="anomaly_detection", action="incident_detected",
                          targets=[incident.intersection_id],
                          after_state={"incident_id": incident.id,
                                       "type": itype.value,
                                       "severity": severity,
                                       "detection_source":
                                           incident.detection_source})
        self._persist_incident(incident)
        self._alert("incident_detected",
                    f"{itype.value.replace('_', ' ').title()} at "
                    f"{incident.intersection_id}", "high")
        return incident

    def correlate_911(self, dispatches: List[Dict[str, Any]],
                      radius_m: float = 150.0) -> int:
        """M2 — 911↔incident auto-correlation.

        A traffic-impacting SFD dispatch (MVI / collision) within
        ``radius_m`` of a camera intersection raises a real platform
        incident tagged ``detection_source="sfd_911"`` — so dispatch
        evidence flows into the same incident pipeline as edge/vision
        detections (dedupe, audit, operator queue). Returns the number of
        incidents raised this call. Idempotent: a dispatch id is correlated
        at most once.

        ``dispatches`` are SFD rows (``id/type/category/traffic_impacting/
        lat/lon``). Non-traffic dispatches (fires, medical) are ignored —
        they already render on the 911 layer."""
        raised = 0
        for d in dispatches:
            if not d.get("traffic_impacting"):
                continue
            did = str(d.get("id", ""))
            if did in self._correlated_911:
                continue
            iid = self._nearest_intersection(
                d.get("lat"), d.get("lon"), radius_m)
            if iid is None:
                continue
            self._correlated_911.add(did)
            # Dedupe against an existing active collision at this node.
            existing = next(
                (inc for inc in self.graph.incidents.values()
                 if inc.intersection_id == iid
                 and inc.type == IncidentType.COLLISION
                 and inc.state.value not in ("resolved", "closed")), None)
            if existing is not None:
                existing.action_history.append({
                    "at": now_ts(), "actor": "sfd_911_correlation",
                    "action": f"911 dispatch corroborated: {d.get('type')}"})
                continue
            inter = self.graph.intersections.get(iid)
            incident = Incident(
                id=new_id("INC"), type=IncidentType.COLLISION,
                intersection_id=iid, severity=0.8,
                description=(f"SFD 911 dispatch '{d.get('type')}' at "
                             f"{d.get('address', 'unknown')} "
                             f"(~{int(radius_m)} m from "
                             f"{inter.name if inter else iid})"),
                detection_source="sfd_911")
            self.graph.add_incident(incident)
            self.audit.record(
                actor="sfd_911_correlation", action="incident_detected",
                targets=[iid],
                after_state={"incident_id": incident.id,
                             "type": "collision", "severity": 0.8,
                             "detection_source": "sfd_911",
                             "dispatch_id": did})
            self._persist_incident(incident)
            self._alert("incident_detected",
                        f"911 traffic dispatch near "
                        f"{inter.name if inter else iid}", "high")
            self.emit_event("incident")
            raised += 1
        # Bound the dedupe set so it can't grow unbounded over a long run.
        if len(self._correlated_911) > 2000:
            self._correlated_911 = set(list(self._correlated_911)[-1000:])
        return raised

    def _nearest_intersection(self, lat: Optional[float], lon: Optional[float],
                              radius_m: float) -> Optional[str]:
        if lat is None or lon is None:
            return None
        # ~111 km per degree lat; cos-corrected lon. Linear search is fine
        # at city scale and keeps this dependency-free.
        import math
        best_iid, best_m = None, radius_m
        coslat = math.cos(math.radians(float(lat)))
        for inter in self.graph.intersections.values():
            dlat = (inter.lat - lat) * 111000.0
            dlon = (inter.lon - lon) * 111000.0 * coslat
            dist = math.hypot(dlat, dlon)
            if dist <= best_m:
                best_iid, best_m = inter.id, dist
        return best_iid

    def _alert(self, kind: str, message: str, priority: str) -> None:
        with self._lock:
            self.alerts.append({"kind": kind, "message": message,
                                "priority": priority, "at": now_ts()})
            self.alerts = self.alerts[-100:]
        self.emit_event("alert")

    # ------------------------------------------------------------------
    # Historical analytics recording (Phase 3)
    # ------------------------------------------------------------------

    def record_history(self) -> None:
        """Sample monitored intersections' congestion into the store
        (throttled to once per 60 s; prunes rows older than 7 days once
        per hour). No-op without a store."""
        if self.store is None:
            return
        now = now_ts()
        if now - self._last_history_at < 60.0:
            return
        first_run = self._last_history_at == 0.0
        self._last_history_at = now
        rows = [(i.id, round(i.congestion, 4), now)
                for i in self.graph.intersections.values() if i.monitored]
        try:
            self.store.add_congestion_samples(rows)
            # Prune once per hour (and skip the very first sample cycle).
            if not first_run and int(now) % 3600 < 60:
                self.store.prune_history(now - 7 * 86400.0)
        except Exception:  # noqa: BLE001 — history must never break ticks
            pass
        self._prune_incident_memory(now)

    def _prune_incident_memory(self, now: float) -> None:
        """Bound working-set memory on long-running deployments: free the
        frozen detection-time jpeg an hour after resolution (the audit /
        store record remains), and drop resolved incidents older than the
        retention window from the in-memory graph (they stay in the Store
        for analytics / discovery)."""
        for inc in list(self.graph.incidents.values()):
            if inc.state.value not in ("resolved", "closed"):
                continue
            resolved_at = inc.resolved_at or inc.detected_at
            if (inc.detection_frame_jpeg is not None
                    and now - resolved_at > FRAME_RETENTION_AFTER_RESOLVE_S):
                inc.detection_frame_jpeg = None
            if now - resolved_at > INCIDENT_RETENTION_S:
                self.graph.incidents.pop(inc.id, None)

    # ------------------------------------------------------------------
    # Feed freshness (PRD §1)
    # ------------------------------------------------------------------

    def touch_feed(self, source: str) -> None:
        with self._lock:
            self.feed_last_update[source] = now_ts()

    def feed_freshness(self) -> Dict[str, float]:
        now = now_ts()
        with self._lock:
            return {src: now - ts for src, ts in self.feed_last_update.items()}

    def feed_status(self) -> List[Dict[str, Any]]:
        ages = self.feed_freshness()
        out = []
        for source, threshold in FRESHNESS_THRESHOLDS.items():
            age = ages.get(source)
            if age is None:
                state = "missing"
            elif age <= 0.8 * threshold:
                state = "fresh"
            elif age <= threshold:
                state = "amber"
            else:
                state = "stale"
            out.append({"source": source, "age_seconds":
                        round(age, 1) if age is not None else None,
                        "threshold_seconds": threshold, "state": state})
        return out

    # ------------------------------------------------------------------
    # Incident lifecycle (PRD §2)
    # ------------------------------------------------------------------

    def acknowledge_incident(self, user_id: str, incident_id: str) -> Incident:
        self._require(user_id, Role.OPERATOR, Role.ADMIN)
        inc = self.graph.incidents.get(incident_id)
        if inc is None:
            raise KeyError(f"Unknown incident {incident_id}")
        if inc.state != IncidentState.DETECTED:
            return inc
        inc.state = IncidentState.ACKNOWLEDGED
        inc.acknowledged_at = now_ts()
        inc.acknowledged_by = user_id
        inc.action_history.append({"at": now_ts(), "actor": user_id,
                                   "action": "acknowledged"})
        self.audit.record(actor=user_id, action="incident_acknowledged",
                          targets=[inc.intersection_id],
                          after_state={"incident_id": inc.id,
                                       "state": inc.state.value})
        self._persist_incident(inc)
        self.emit_event("incident")
        return inc

    def resolve_incident(self, user_id: str, incident_id: str,
                         resolution: str, notes: str = "") -> Incident:
        self._require(user_id, Role.OPERATOR, Role.ADMIN)
        if resolution not in ("Resolved", "False Alarm", "Handed Off"):
            raise ValueError("Resolution must be Resolved, False Alarm, "
                             "or Handed Off")
        inc = self.graph.incidents.get(incident_id)
        if inc is None:
            raise KeyError(f"Unknown incident {incident_id}")
        before = inc.state.value
        inc.state = IncidentState.RESOLVED
        inc.resolved_at = now_ts()
        inc.resolution = resolution
        inc.action_history.append({"at": now_ts(), "actor": user_id,
                                   "action": f"resolved ({resolution})",
                                   "notes": notes})
        self.audit.record(actor=user_id, action="incident_resolved",
                          targets=[inc.intersection_id],
                          before_state={"state": before},
                          after_state={"state": inc.state.value,
                                       "resolution": resolution},
                          detail=notes)
        self._persist_incident(inc)
        self.emit_event("incident")
        return inc

    def active_incidents(self) -> List[Incident]:
        with self._lock:
            incidents = [i for i in self.graph.incidents.values()
                         if i.state.value not in ("resolved", "closed")]
        # Incident Queue ranking by severity (PRD §2 multi-incident handling)
        incidents.sort(key=lambda i: i.severity, reverse=True)
        return incidents

    def _capture_frame(self, camera_id: str) -> Optional[bytes]:
        """Best-effort freeze of the current camera frame at detection time.
        Uses the runtime-injected hook if present; otherwise None (offline /
        tests). Never raises."""
        fn = self.frame_capture_fn
        if fn is None or not camera_id:
            return None
        try:
            return fn(camera_id)
        except Exception:  # noqa: BLE001 — evidence capture is best-effort
            return None

    def incident_frame(self, incident_id: str) -> Optional[bytes]:
        """Return the frozen detection-time jpeg for an incident, if any."""
        inc = self.graph.incidents.get(incident_id)
        return inc.detection_frame_jpeg if inc is not None else None

    def query_incidents(self, since: Optional[float] = None,
                        until: Optional[float] = None,
                        types: Optional[List[str]] = None,
                        sources: Optional[List[str]] = None,
                        include_resolved: bool = True,
                        order: str = "desc",
                        limit: int = 50,
                        offset: int = 0) -> Dict[str, Any]:
        """Filtered, sorted, paginated incident query for the Incident Queue.

        Filters: ``since``/``until`` (epoch seconds, on detected_at),
        ``types`` (IncidentType values), ``sources`` (detection_source),
        ``include_resolved``. ``order`` is "asc"|"desc" by detection time.
        Returns a dict with the matched window (``incidents``) plus
        ``total`` (matches before paging) and ``returned``."""
        with self._lock:
            all_inc = list(self.graph.incidents.values())
        type_set = set(types) if types else None
        source_set = set(sources) if sources else None

        def _match(i: Incident) -> bool:
            if not include_resolved and i.state.value in ("resolved", "closed"):
                return False
            if since is not None and i.detected_at < since:
                return False
            if until is not None and i.detected_at > until:
                return False
            if type_set is not None and i.type.value not in type_set:
                return False
            if source_set is not None and i.detection_source not in source_set:
                return False
            return True

        matched = [i for i in all_inc if _match(i)]
        matched.sort(key=lambda i: i.detected_at, reverse=(order != "asc"))
        total = len(matched)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 200))
        page = matched[offset:offset + limit]
        return {
            "total": total,
            "returned": len(page),
            "offset": offset,
            "limit": limit,
            "order": "asc" if order == "asc" else "desc",
            "incidents": [self._incident_dict(i) for i in page],
        }

    def _incident_dict(self, i: Incident) -> Dict[str, Any]:
        """Full incident serialization for the queue (includes the AI
        classification justification and whether a frozen frame exists)."""
        return {
            "id": i.id, "type": i.type.value,
            "intersection_id": i.intersection_id,
            "intersection_name": (
                self.graph.intersections[i.intersection_id].name
                if i.intersection_id in self.graph.intersections
                else i.intersection_id),
            "severity": i.severity, "state": i.state.value,
            "detected_at": i.detected_at,
            "acknowledged_by": i.acknowledged_by,
            "resolved_at": i.resolved_at,
            "resolution": i.resolution,
            "description": i.description,
            "action_history": i.action_history[-10:],
            "detection_source": i.detection_source,
            "camera_id": i.camera_id,
            "ai_justification": i.ai_justification,
            "ai_confidence": i.ai_confidence,
            "has_detection_frame": i.detection_frame_jpeg is not None,
            # The *exact* live camera the frozen frame came from. Several
            # cameras can share one intersection (different viewing
            # directions), so this disambiguates which physical feed was
            # analyzed at detection time.
            **self._camera_meta(i.camera_id),
        }

    def _camera_meta(self, camera_id: Optional[str]) -> Dict[str, Any]:
        """Resolve the live camera name / feed id for a platform camera_id
        via the runtime hook. Returns {} when unavailable (tests/offline)."""
        fn = self.camera_meta_fn
        if fn is None or not camera_id:
            return {}
        try:
            meta = fn(camera_id)
        except Exception:  # noqa: BLE001 — never break serialization
            return {}
        if not meta:
            return {}
        return {
            "camera_name": meta.get("name"),
            "camera_live_id": meta.get("live_id"),
            "camera_type": meta.get("type"),
        }


    # ------------------------------------------------------------------
    # Recommendation workflow (PRD §5)
    # ------------------------------------------------------------------

    def recommend(self, incident_id: str) -> ActionPlan:
        """Generate a plan, pass it through the safety gate, and (if it
        survives) attach a dry-run simulation. The incident moves to
        Mitigating."""
        inc = self.graph.incidents.get(incident_id)
        if inc is None:
            raise KeyError(f"Unknown incident {incident_id}")

        plan = self.copilot.generate_plan(inc, self.feed_freshness())
        plan = self.safety.evaluate(plan)

        if plan.status == PlanStatus.PENDING_APPROVAL:
            plan.simulation = simulate_impact(self.graph, plan)
            if inc.state in (IncidentState.ACKNOWLEDGED,
                             IncidentState.DETECTED):
                inc.state = IncidentState.MITIGATING
                self._persist_incident(inc)

        with self._lock:
            self.plans[plan.plan_id] = plan
        self._prune_plans()

        self.audit.record(
            actor="ai_copilot", action="recommendation_generated",
            targets=plan.targets,
            after_state={"plan_id": plan.plan_id,
                         "status": plan.status.value,
                         "confidence": plan.confidence.composite,
                         "block_reason": plan.block_reason},
            data_sources=plan.provenance.data_sources,
            outcome=("blocked" if plan.status not in
                     (PlanStatus.PENDING_APPROVAL,) else "ok"),
            detail=plan.justification[:300])
        self._persist_plan(plan)
        self.emit_event("plan")
        return plan

    def approve(self, user_id: str, plan_id: str) -> ActionPlan:
        """Operator approval → mode-dependent execution (PRD §5 step 3)."""
        self._require(user_id, Role.OPERATOR, Role.ADMIN)
        plan = self.plans.get(plan_id)
        if plan is None:
            raise KeyError(f"Unknown plan {plan_id}")
        if plan.status != PlanStatus.PENDING_APPROVAL:
            raise ValueError(f"Plan {plan_id} is not pending approval "
                             f"(status: {plan.status.value})")

        # Re-verify immediately before execution — conditions may have
        # changed since generation (defense in depth).
        recheck = self.safety.verifier.verify(plan)
        if not recheck.passed:
            plan.status = PlanStatus.BLOCKED_CONSTRAINT
            plan.block_reason = recheck.reason()
            self.audit.record(actor=user_id, action="approval_blocked",
                              targets=plan.targets, outcome="blocked",
                              detail=plan.block_reason)
            self._persist_plan(plan)
            self.emit_event("plan")
            return plan

        plan.approved_by = user_id
        plan.approved_at = now_ts()
        plan.status = PlanStatus.APPROVED
        self.audit.record(actor=user_id, action="plan_approved",
                          targets=plan.targets,
                          approval_chain=[user_id],
                          after_state={"plan_id": plan.plan_id,
                                       "plan_hash": plan.plan_hash()})
        return self._execute(plan, user_id)

    def reject(self, user_id: str, plan_id: str, reason: str = "") -> ActionPlan:
        self._require(user_id, Role.OPERATOR, Role.ADMIN)
        plan = self.plans.get(plan_id)
        if plan is None:
            raise KeyError(f"Unknown plan {plan_id}")
        # Only a plan awaiting approval can be rejected. Rejecting an
        # EXECUTED plan would strand the live timing change with no
        # rollback path (rollback requires status EXECUTED).
        if plan.status != PlanStatus.PENDING_APPROVAL:
            raise ValueError(
                f"Plan {plan_id} is not pending approval "
                f"(status: {plan.status.value}); cannot reject. "
                f"Use rollback for executed plans.")
        plan.status = PlanStatus.REJECTED
        inc = self.graph.incidents.get(plan.incident_id)
        if inc is not None:
            self.copilot.record_outcome(inc.type.value, accepted=False)
        self.audit.record(actor=user_id, action="plan_rejected",
                          targets=plan.targets, outcome="rejected",
                          detail=reason)
        self._persist_plan(plan)
        self.emit_event("plan")
        return plan

    # ------------------------------------------------------------------
    # Execution (mode ladder enforcement)
    # ------------------------------------------------------------------

    def _execute(self, plan: ActionPlan, user_id: str) -> ActionPlan:
        """Execute an APPROVED plan according to the operating mode.

        Shadow:   log only — never touches timing plans.
        Advisory: emit a formatted instruction with 15-min expiration.
        Live:     apply timing change + register for rollback monitoring.
        """
        inc = self.graph.incidents.get(plan.incident_id)

        if self.mode == OperatingMode.SHADOW:
            plan.status = PlanStatus.SHADOW_LOGGED
            self.audit.record(actor="system", action="shadow_logged",
                              targets=plan.targets,
                              approval_chain=[user_id],
                              detail="Shadow Mode: approved action logged, "
                                     "NOT executed (PRD §7.1).")
        elif self.mode == OperatingMode.ADVISORY:
            plan.status = PlanStatus.ADVISORY_ISSUED
            plan.expires_at = now_ts() + ADVISORY_EXPIRATION_S
            self.audit.record(actor="system", action="advisory_issued",
                              targets=plan.targets,
                              approval_chain=[user_id],
                              detail="Advisory Mode: formatted instruction "
                                     "issued; expires in 15 minutes.")
        else:  # LIVE
            baselines: Dict[str, float] = {}
            previous: Dict[str, Any] = {}
            for target in plan.targets:
                inter = self.graph.get_intersection(target)
                previous[target] = {
                    "cycle_seconds": inter.timing_plan.cycle_seconds,
                    "phases": {p.phase_id: p.green_seconds
                               for p in inter.timing_plan.phases},
                }
                baselines[target] = inter.congestion
                # Apply the operations to the live timing plan.
                from .safety import apply_operations_to_plan
                ops = [o for o in plan.operations
                       if o.intersection_id == target]
                inter.timing_plan = apply_operations_to_plan(
                    inter.timing_plan, ops)
                self.safety.verifier.register_active_change(
                    target, plan.plan_id)
            plan.previous_timing = previous
            plan.status = PlanStatus.EXECUTED
            plan.executed_at = now_ts()
            with self._lock:
                self._monitoring[plan.plan_id] = {
                    "baselines": baselines,
                    "executed_at": plan.executed_at,
                    "alerted": False,
                }
            self.audit.record(actor="system", action="signal_change_executed",
                              targets=plan.targets,
                              approval_chain=[user_id],
                              before_state=previous,
                              after_state={"plan_id": plan.plan_id})

        if inc is not None:
            inc.state = IncidentState.MONITORING
            inc.action_history.append({
                "at": now_ts(), "actor": user_id,
                "action": f"plan {plan.status.value}",
                "plan_id": plan.plan_id})
            self.copilot.record_outcome(inc.type.value, accepted=True)
            self._persist_incident(inc)
        self._persist_plan(plan)
        self.emit_event("plan")
        return plan

    def expire_advisories(self) -> int:
        """Flip ADVISORY_ISSUED plans past their 15-minute expiration to
        EXPIRED (PRD §5). Called from the platform tick. Returns the number
        expired this pass."""
        now = now_ts()
        with self._lock:
            due = [p for p in self.plans.values()
                   if p.status == PlanStatus.ADVISORY_ISSUED
                   and p.expires_at and now > p.expires_at]
        for plan in due:
            plan.status = PlanStatus.EXPIRED
            self.audit.record(
                actor="system", action="advisory_expired",
                targets=plan.targets,
                detail="Advisory instruction expired unconfirmed after "
                       "15 minutes (PRD §5); a fresh recommendation is "
                       "required.")
            self._persist_plan(plan)
        if due:
            self.emit_event("plan")
        return len(due)

    def _prune_plans(self) -> None:
        """Bound the in-memory plan table: oldest finished plans are pruned
        first (their durable snapshots remain in the Store)."""
        with self._lock:
            if len(self.plans) <= MAX_PLANS_IN_MEMORY:
                return
            terminal = sorted(
                (p for p in self.plans.values()
                 if p.status in _TERMINAL_PLAN_STATUSES),
                key=lambda p: p.created_at)
            excess = len(self.plans) - MAX_PLANS_IN_MEMORY
            for p in terminal[:excess]:
                self.plans.pop(p.plan_id, None)

    def advisory_instruction(self, plan_id: str) -> Dict[str, Any]:
        """Formatted instruction per PRD §5 Advisory Mode format."""
        plan = self.plans.get(plan_id)
        if plan is None:
            raise KeyError(f"Unknown plan {plan_id}")
        if plan.status not in (PlanStatus.ADVISORY_ISSUED,
                               PlanStatus.EXPIRED):
            raise ValueError("Plan has no advisory instruction")
        lines = []
        for op in plan.operations:
            inter = self.graph.get_intersection(op.intersection_id)
            phase = next((p for p in inter.timing_plan.phases
                          if p.phase_id == op.phase_id), None)
            current = phase.green_seconds if phase else 0.0
            lines.append({
                "intersection": f"{op.intersection_id}: {inter.name}",
                "current_timing": {
                    "cycle_seconds": inter.timing_plan.cycle_seconds,
                    "phase_green_seconds": current,
                },
                "requested_change": (
                    f"{'Increase' if op.type == 'extend_green' else 'Decrease'}"
                    f" Phase {op.phase_id} green from {current:.0f}s to "
                    f"{current + op.delta_seconds:.0f}s"),
            })
        return {
            "plan_id": plan.plan_id,
            "priority": "Urgent" if (plan.confidence.composite >= 85) else "Standard",
            "expires_at": plan.expires_at,
            "expired": bool(plan.expires_at and now_ts() > plan.expires_at),
            "instructions": lines,
            "confirmation_protocol": ("Mark as Relayed when communicated; "
                                      "mark Executed / Unable to Execute on "
                                      "field confirmation."),
        }

    # ------------------------------------------------------------------
    # Rollback (PRD §6)
    # ------------------------------------------------------------------

    def rollback(self, user_id: str, plan_id: str,
                 reason: str = "manual rollback") -> ActionPlan:
        """One-click revert to the previous timing plan (PRD §6.1)."""
        self._require(user_id, Role.OPERATOR, Role.ADMIN)
        plan = self.plans.get(plan_id)
        if plan is None:
            raise KeyError(f"Unknown plan {plan_id}")
        if plan.status != PlanStatus.EXECUTED:
            raise ValueError(f"Plan {plan_id} is not executed "
                             f"(status: {plan.status.value}); nothing to revert.")
        before: Dict[str, Any] = {}
        for target, prev in plan.previous_timing.items():
            inter = self.graph.get_intersection(target)
            before[target] = {
                "cycle_seconds": inter.timing_plan.cycle_seconds,
                "phases": {p.phase_id: p.green_seconds
                           for p in inter.timing_plan.phases},
            }
            inter.timing_plan.cycle_seconds = prev["cycle_seconds"]
            for p in inter.timing_plan.phases:
                if p.phase_id in prev["phases"]:
                    p.green_seconds = prev["phases"][p.phase_id]
            self.safety.verifier.clear_active_change(target)
        plan.status = PlanStatus.REVERTED
        with self._lock:
            self._monitoring.pop(plan_id, None)
        self.audit.record(actor=user_id, action="rollback_executed",
                          targets=plan.targets,
                          before_state=before,
                          after_state=plan.previous_timing,
                          detail=reason)
        self._persist_plan(plan)
        self.emit_event("plan")
        return plan

    def check_rollback_monitors(self) -> List[Dict[str, Any]]:
        """Automatic rollback monitoring (PRD §6.2): alert when congestion
        worsens ≥ 20% within 5 minutes of execution.

        Lifecycle completion: once a change survives its monitoring window
        without worsening, it SETTLES — the monitor is retired and the R6
        active-change registration is cleared, returning the system-wide
        concurrency budget. (Previously changes stayed registered forever
        unless manually rolled back, eventually blocking every new plan and
        leaking monitors.) Alerted changes are held for operator action,
        then hard-settle after 30 minutes."""
        proposals: List[Dict[str, Any]] = []
        settled: List[str] = []
        now = now_ts()
        with self._lock:
            monitors = dict(self._monitoring)
        for plan_id, monitor in monitors.items():
            age = now - monitor["executed_at"]
            if age > AUTO_REVERT_WINDOW_S:
                if not monitor["alerted"] or age > ALERTED_SETTLE_S:
                    settled.append(plan_id)
                continue
            if monitor["alerted"]:
                continue
            for target, baseline in monitor["baselines"].items():
                inter = self.graph.get_intersection(target)
                if baseline <= 0:
                    continue
                worsening = 100.0 * (inter.congestion - baseline) / baseline
                if worsening >= AUTO_REVERT_WORSEN_PCT:
                    monitor["alerted"] = True
                    self._alert("conditions_worsening",
                                f"Conditions worsening at {target} "
                                f"(+{worsening:.0f}%) after {plan_id}; "
                                f"reversion proposed.", "critical")
                    self.audit.record(actor="system",
                                      action="auto_revert_proposed",
                                      targets=[target],
                                      detail=f"congestion +{worsening:.0f}% "
                                             f"within monitoring window")
                    proposals.append({"plan_id": plan_id, "target": target,
                                      "worsening_pct": round(worsening, 1)})
                    break
        for plan_id in settled:
            self._settle_change(plan_id)
        return proposals

    def _settle_change(self, plan_id: str) -> None:
        """Retire a monitored change: free its R6 registration and monitor.
        The plan stays EXECUTED (still manually revertible)."""
        with self._lock:
            monitor = self._monitoring.pop(plan_id, None)
        if monitor is None:
            return
        plan = self.plans.get(plan_id)
        targets = plan.targets if plan is not None \
            else list(monitor["baselines"].keys())
        for target in targets:
            self.safety.verifier.clear_active_change(target)
        self.audit.record(
            actor="system", action="change_settled", targets=targets,
            detail=f"Timing change {plan_id} completed its monitoring "
                   f"window without reversion; active-change registration "
                   f"cleared.")

    # ------------------------------------------------------------------
    # Status snapshot for the UI / API
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        with self._lock:
            plans = [p.to_dict() for p in
                     sorted(self.plans.values(),
                            key=lambda p: p.created_at, reverse=True)[:50]]
            alerts = list(self.alerts[-20:])
        incidents = [self._incident_dict(i) for i in self.active_incidents()]

        return {
            "city_id": self.city_id,
            "mode": self.mode.value,
            "feeds": self.feed_status(),
            "incidents": incidents,
            "plans": plans,
            "alerts": alerts,
            "safety_metrics": self.safety.metrics.as_dict(),
            "confidence_threshold": self.safety.confidence_threshold,
            "active_changes": self.safety.verifier.active_changes(),
            "audit_entries": len(self.audit),
            "audit_chain_intact": self.audit.verify_chain_cached(),
            "dlq_count": self.bus.dlq_count,
        }