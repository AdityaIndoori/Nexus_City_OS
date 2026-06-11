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
SPEED_ANOMALY_FRACTION = 0.15           # speed < 15% of limit ⇒ anomaly


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
        # intersections with a FRESH real-data congestion estimate (bus GPS /
        # WSDOT flow) — the simulator must not overwrite these (Phase 1).
        # Updated by the runtime each tick; empty by default so offline
        # deployments and tests behave exactly as before.
        self.real_congestion_ids: set = set()
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
        incident = Incident(
            id=new_id("INC"),
            type=itype,
            intersection_id=telemetry.intersection_id,
            severity=severity,
            description=(f"{itype.value} detected by {telemetry.camera_id} "
                         f"(avg speed {telemetry.avg_speed_mph:.1f} mph, "
                         f"{telemetry.stopped_vehicles} stopped)"),
            detection_source=telemetry.source,
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

        with self._lock:
            self.plans[plan.plan_id] = plan

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

    def advisory_instruction(self, plan_id: str) -> Dict[str, Any]:
        """Formatted instruction per PRD §5 Advisory Mode format."""
        plan = self.plans.get(plan_id)
        if plan is None:
            raise KeyError(f"Unknown plan {plan_id}")
        if plan.status != PlanStatus.ADVISORY_ISSUED:
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
        worsens ≥ 20% within 5 minutes of execution."""
        proposals: List[Dict[str, Any]] = []
        now = now_ts()
        with self._lock:
            monitors = dict(self._monitoring)
        for plan_id, monitor in monitors.items():
            if monitor["alerted"]:
                continue
            if now - monitor["executed_at"] > AUTO_REVERT_WINDOW_S:
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
        return proposals

    # ------------------------------------------------------------------
    # Status snapshot for the UI / API
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        with self._lock:
            plans = [p.to_dict() for p in
                     sorted(self.plans.values(),
                            key=lambda p: p.created_at, reverse=True)[:50]]
            alerts = list(self.alerts[-20:])
        incidents = [{
            "id": i.id, "type": i.type.value,
            "intersection_id": i.intersection_id,
            "severity": i.severity, "state": i.state.value,
            "detected_at": i.detected_at,
            "acknowledged_by": i.acknowledged_by,
            "description": i.description,
            "action_history": i.action_history[-10:],
            "detection_source": i.detection_source,
        } for i in self.active_incidents()]
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
            "audit_chain_intact": self.audit.verify_chain(),
            "dlq_count": self.bus.dlq_count,
        }