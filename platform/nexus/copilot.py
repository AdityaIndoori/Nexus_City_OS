"""
Nexus City OS — AI Copilot (PRD §3, §4).

Generates structured ``ActionPlan`` objects through the tool-calling pattern
(PRD §4.1): it can only emit pre-validated, schema-checked operations — never
free-form infrastructure commands. Every plan carries mandatory provenance
(PRD §4.2) and a composite confidence score (PRD §4.3).

The recommendation logic is a deterministic, explainable expert system over
the live city graph (production deployments may substitute an LLM behind the
same generate_plan() contract — the SafetyGate downstream is model-agnostic
and never trusts the generator).

Also includes the operator query interface with prompt-injection sanitization
and rate limiting (PRD §4.1 adversarial input protections).
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .llm import (
    CHAT_SYSTEM,
    LLMClient,
    LLMUnavailable,
    MODEL_CHAT,
    MODEL_PLANNER,
    MODEL_VISION,
    PLANNER_SYSTEM,
    VISION_SYSTEM,
    extract_json,
)
from .graph import CityGraph
from .models import (
    ActionPlan,
    ConfidenceBreakdown,
    FRESHNESS_THRESHOLDS,
    Incident,
    IncidentType,
    MODEL_VERSION,
    Operation,
    Provenance,
    new_id,
    now_ts,
)

# Mitigation playbook: incident type -> green extension at the bottleneck's
# parallel arterials (seconds), scaled by severity.
BASE_GREEN_EXTENSION_S = 10.0
SEVERITY_EXTENSION_S = 10.0   # additional seconds at severity=1.0
MAX_NEIGHBOR_TARGETS = 3

# Adverse weather reduces model certainty (PRD §3 weather awareness).
WEATHER_CERTAINTY = {
    "clear": 92.0, "fog": 84.0, "rain": 80.0, "snow": 70.0, "ice": 62.0,
}

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"ignore (all|previous|prior) (instructions|rules)",
        r"disregard (the )?(safety|guardrails|constraints)",
        r"you are now",
        r"system prompt",
        r"jailbreak",
        r"execute .* without approval",
        r"bypass",
    )
]

RATE_LIMIT_WINDOW_S = 5 * 60.0
RATE_LIMIT_MAX_QUERIES = 30


class RateLimitExceeded(Exception):
    pass


class InjectionBlocked(Exception):
    pass


class Copilot:
    """Recommendation engine + grounded query interface.

    With ``use_llm=True`` (default), plan rationale/operations come from the
    production LLM (Claude Sonnet 4.5) and operator chat from Claude Haiku
    4.5 — both behind strict schema validation, with the deterministic
    expert system as an always-available fallback. The SafetyGate downstream
    re-verifies everything regardless of generator.
    """

    def __init__(self, graph: CityGraph, use_llm: bool = True) -> None:
        self._graph = graph
        self._lock = threading.RLock()
        self._query_times: Dict[str, Deque[float]] = {}
        self._injection_attempts: List[Dict[str, Any]] = []
        # Construct the client, but only treat the LLM as ON when it is
        # actually configured (absolute gateway URL + key). Otherwise the
        # platform runs purely on the deterministic expert system — the
        # copilot answers locally instead of erroring on a missing gateway.
        client = LLMClient() if use_llm else None
        self.use_llm = bool(use_llm and client is not None and client.configured)
        self.llm = client if self.use_llm else None
        self.last_generator = "deterministic"

        # Optional callable returning extra live context (e.g. 911 feed)
        # appended to the chat LLM's grounding context.
        self.extra_context_fn = None
        # Rolling historical accuracy by incident type (seeded from Shadow
        # Mode calibration data; updated as operators accept/reject).
        self._historical_accuracy: Dict[str, float] = {
            t.value: 88.0 for t in IncidentType}
        self._accept_counts: Dict[str, List[int]] = {
            t.value: [22, 25] for t in IncidentType}  # [accepted, total]

    # ------------------------------------------------------------------
    # Plan generation (tool-calling pattern)
    # ------------------------------------------------------------------

    def generate_plan(self, incident: Incident,
                      feed_freshness: Dict[str, float]) -> ActionPlan:
        """Generate a structured mitigation ActionPlan for an incident.

        ``feed_freshness`` maps source name -> seconds since last update.
        """
        graph = self._graph
        bottleneck_id = incident.intersection_id
        bottleneck = graph.get_intersection(bottleneck_id)

        impacts = graph.cascading_impact(bottleneck_id, max_hops=2)
        neighbor_targets = [i["intersection_id"]
                            for i in impacts[:MAX_NEIGHBOR_TARGETS]]

        extension = round(
            BASE_GREEN_EXTENSION_S + SEVERITY_EXTENSION_S * incident.severity, 1)

        operations: List[Operation] = []
        targets: List[str] = []
        entities: List[str] = [bottleneck_id, incident.id]

        for target_id in neighbor_targets:
            inter = graph.get_intersection(target_id)
            through = [p for p in inter.timing_plan.phases
                       if p.movement == "through"]
            if not through:
                continue
            operations.append(Operation(
                type="extend_green",
                intersection_id=target_id,
                phase_id=through[0].phase_id,
                delta_seconds=extension,
            ))
            targets.append(target_id)
            entities.append(target_id)

        weather = graph.weather
        weather_dict = ({
            "condition": weather.condition,
            "temperature_f": weather.temperature_f,
            "severe_alert": weather.severe_alert,
        } if weather else {"condition": "unknown", "temperature_f": 0.0,
                           "severe_alert": False})

        now = now_ts()
        data_sources = [
            {"source": "edge_camera_telemetry",
             "timestamp": now - feed_freshness.get("camera", 1.0)},
            {"source": "gtfs_rt_vehicle_positions",
             "timestamp": now - feed_freshness.get("transit_gps", 5.0)},
            {"source": "weather",
             "timestamp": now - feed_freshness.get("weather", 60.0)},
        ]

        rationale = (
            f"{incident.type.value.replace('_', ' ').title()} at "
            f"{bottleneck.name} ({bottleneck_id}, severity "
            f"{incident.severity:.0%}). Extending through-phase green by "
            f"{extension:.0f}s at {len(targets)} parallel arterial "
            f"intersection(s) to drain the upstream queue. "
            f"{len(impacts)} downstream intersections at risk of gridlock; "
            f"nearest in ~{impacts[0]['est_minutes_to_gridlock']:.0f} min."
            if impacts else
            f"{incident.type.value.replace('_', ' ').title()} at "
            f"{bottleneck.name}: isolated impact, monitoring recommended.")

        provenance = Provenance(
            entities=entities,
            data_sources=data_sources,
            weather=weather_dict,
            rationale=rationale,
        )

        confidence = self._score_confidence(
            incident, targets, feed_freshness, weather_dict)

        plan = ActionPlan(
            plan_id=new_id("PLAN"),
            created_at=now,
            model_version=MODEL_VERSION,
            incident_id=incident.id,
            targets=targets,
            operations=operations,
            justification=rationale,
            provenance=provenance,
            confidence=confidence,
        )

        # LLM enhancement: Claude Sonnet 4.5 may refine operations and write
        # the operator-facing rationale. Strictly validated; deterministic
        # plan stands on any failure. SafetyGate re-verifies downstream.
        if self.use_llm and self.llm is not None:
            self._llm_refine(plan, incident, bottleneck,
                             neighbor_targets, impacts, weather_dict)
        else:
            self.last_generator = "deterministic"
        return plan

    def _llm_refine(self, plan: ActionPlan, incident: Incident,
                    bottleneck: Any, candidates: List[str],
                    impacts: List[Dict[str, Any]],
                    weather: Dict[str, Any]) -> None:
        """Ask the planner LLM to refine the plan. Mutates ``plan`` only if
        the response passes strict schema validation."""
        graph = self._graph
        cand_lines = []
        for cid in candidates:
            inter = graph.get_intersection(cid)
            cand_lines.append(
                f"  {cid}: {inter.name} (congestion {inter.congestion:.0%}"
                + (", EMS corridor" if inter.ems_corridor else "") + ")")
        impact_lines = [
            f"  {i['intersection_id']}: gridlock in "
            f"~{i['est_minutes_to_gridlock']:.0f} min" for i in impacts[:5]]
        user = (
            f"INCIDENT: {incident.type.value} at {bottleneck.name} "
            f"({incident.intersection_id}), severity "
            f"{incident.severity:.0%}.\n"
            f"Detection: {incident.description}\n"
            f"Weather: {weather.get('condition')}, "
            f"{weather.get('temperature_f')}F\n"
            f"CANDIDATE intersections for green extension (parallel "
            f"arterials):\n" + "\n".join(cand_lines) +
            "\nDownstream gridlock risk:\n" + "\n".join(impact_lines))
        try:
            raw = self.llm.chat(MODEL_PLANNER, [
                {"role": "system", "content": PLANNER_SYSTEM},
                {"role": "user", "content": user}], max_tokens=900)
        except LLMUnavailable:
            self.last_generator = "deterministic (LLM unavailable)"
            return
        parsed = extract_json(raw)
        if not parsed:
            self.last_generator = "deterministic (LLM output unparseable)"
            return
        # STRICT validation: only candidate IDs, bounded deltas, ≤3 ops.
        ops_in = parsed.get("operations")
        rationale = str(parsed.get("rationale", "")).strip()
        certainty = parsed.get("model_certainty")
        if not isinstance(ops_in, list) or not rationale:
            self.last_generator = "deterministic (LLM schema invalid)"
            return
        valid_ops: List[Operation] = []
        valid_targets: List[str] = []
        for op in ops_in[:MAX_NEIGHBOR_TARGETS]:
            if not isinstance(op, dict):
                continue
            iid = str(op.get("intersection_id", ""))
            try:
                delta = float(op.get("delta_seconds", 0))
            except (TypeError, ValueError):
                continue
            if iid not in candidates or not (1.0 <= delta <= 25.0):
                continue   # reject hallucinated IDs / out-of-bound deltas
            inter = graph.get_intersection(iid)
            through = [p for p in inter.timing_plan.phases
                       if p.movement == "through"]
            if not through:
                continue
            valid_ops.append(Operation(
                type="extend_green", intersection_id=iid,
                phase_id=through[0].phase_id,
                delta_seconds=round(delta, 1)))
            valid_targets.append(iid)
        if not valid_ops:
            self.last_generator = "deterministic (LLM ops all rejected)"
            return
        plan.operations = valid_ops
        plan.targets = valid_targets
        plan.justification = rationale[:600]
        plan.provenance.rationale = rationale[:600]
        plan.provenance.entities = (
            [incident.intersection_id, incident.id] + valid_targets)
        if isinstance(certainty, (int, float)) and 0 <= certainty <= 100:
            # Blend LLM self-assessment with the weather-conditioned prior.
            plan.confidence.model_certainty = round(
                0.5 * plan.confidence.model_certainty
                + 0.5 * float(certainty), 1)
        plan.model_version = MODEL_VERSION + "+sonnet-4.5"
        self.last_generator = "llm (claude-sonnet-4.5)"

    # ------------------------------------------------------------------
    # Vision: live camera frame analysis (Claude Haiku 4.5)
    # ------------------------------------------------------------------

    def analyze_frame(self, image_jpeg: bytes,
                      context: str) -> Dict[str, Any]:
        """Visual triage of a live camera frame. Returns the structured
        assessment, or a degraded-mode notice if the model is unreachable."""
        if not self.use_llm or self.llm is None:
            return {"available": False,
                    "error": "AI vision disabled in this deployment."}
        prompt = (f"{VISION_SYSTEM}\n\nContext: {context}\n"
                  f"Analyze this live traffic camera frame now.")
        try:
            raw = self.llm.chat_vision(MODEL_VISION, prompt, image_jpeg)
        except LLMUnavailable as exc:
            return {"available": False,
                    "error": f"Vision model unreachable: {exc}"}
        parsed = extract_json(raw) or {}
        return {
            "available": True,
            "model": MODEL_VISION,
            "assessment": str(parsed.get("assessment", raw[:400])),
            "congestion_visible": str(parsed.get("congestion_visible",
                                                 "unknown")),
            "incident_visible": bool(parsed.get("incident_visible", False)),
            "visibility": str(parsed.get("visibility", "unknown")),
            "confidence_pct": float(parsed.get("confidence_pct", 0.0))
            if isinstance(parsed.get("confidence_pct"), (int, float))
            else 0.0,
        }

    # ------------------------------------------------------------------
    # Confidence scoring (PRD §4.3 weights)
    # ------------------------------------------------------------------

    def _score_confidence(self, incident: Incident, targets: List[str],
                          feed_freshness: Dict[str, float],
                          weather: Dict[str, Any]) -> ConfidenceBreakdown:
        # Model certainty: weather-conditioned base, reduced for low severity
        # signal (ambiguous detections).
        model_certainty = WEATHER_CERTAINTY.get(
            str(weather.get("condition", "clear")), 75.0)
        if incident.severity < 0.3:
            model_certainty -= 10.0

        # Data freshness: share of sources within threshold (PRD §4.3).
        fresh, total = 0, 0
        for source, age in feed_freshness.items():
            threshold = FRESHNESS_THRESHOLDS.get(source)
            if threshold is None:
                continue
            total += 1
            if age <= threshold:
                fresh += 1
        data_freshness = 100.0 * fresh / total if total else 50.0

        # Coverage: share of affected intersections with camera coverage.
        affected = targets + [incident.intersection_id]
        monitored = sum(
            1 for t in affected
            if self._graph.has_intersection(t)
            and self._graph.get_intersection(t).monitored)
        coverage = 100.0 * monitored / len(affected) if affected else 0.0

        historical = self._historical_accuracy.get(incident.type.value, 80.0)

        return ConfidenceBreakdown(
            model_certainty=round(max(0.0, min(100.0, model_certainty)), 1),
            data_freshness=round(data_freshness, 1),
            coverage_completeness=round(coverage, 1),
            historical_accuracy=round(historical, 1),
        )

    def record_outcome(self, incident_type: str, accepted: bool) -> None:
        """Update rolling historical accuracy as operators accept/reject."""
        with self._lock:
            counts = self._accept_counts.setdefault(incident_type, [0, 0])
            counts[1] += 1
            if accepted:
                counts[0] += 1
            self._historical_accuracy[incident_type] = round(
                100.0 * counts[0] / counts[1], 1)

    # ------------------------------------------------------------------
    # Grounded operator queries (PRD §4.1 adversarial protections)
    # ------------------------------------------------------------------

    def query(self, operator_id: str, text: str) -> Dict[str, Any]:
        """Answer a grounded query against the city model. Uses the chat
        LLM (Claude Haiku 4.5) with live city context; falls back to the
        deterministic keyword answers when the model is unreachable."""
        self._enforce_rate_limit(operator_id)
        self._sanitize(operator_id, text)

        if self.use_llm and self.llm is not None:
            answer = self._llm_query(text)
            if answer is not None:
                return answer
        return self._deterministic_query(text)

    def _llm_query(self, text: str) -> Optional[Dict[str, Any]]:
        graph = self._graph
        worst = sorted(graph.intersections.values(),
                       key=lambda i: i.congestion, reverse=True)[:8]
        active = [i for i in graph.incidents.values()
                  if i.state.value not in ("resolved", "closed")]
        slow = [v for v in graph.vehicles.values() if v.speed_mph < 8.0]
        w = graph.weather
        context = (
            "LIVE CITY CONTEXT (Seattle):\n"
            f"Weather: {w.condition}, {w.temperature_f:.0f}F\n" if w else ""
        )
        context += (
            f"Active incidents ({len(active)}): " + "; ".join(
                f"{i.type.value} at "
                f"{graph.get_intersection(i.intersection_id).name} "
                f"(severity {i.severity:.0%}, state {i.state.value})"
                for i in active[:8]) + "\n"
            f"Most congested: " + "; ".join(
                f"{i.name} {i.congestion:.0%}" for i in worst) + "\n"
            f"Transit: {len(graph.vehicles)} live vehicles, "
            f"{len(slow)} moving <8 mph (likely delayed)\n")
        if self.extra_context_fn is not None:
            try:
                context += str(self.extra_context_fn())
            except Exception:  # noqa: BLE001 — grounding is best-effort
                pass
        try:
            answer = self.llm.chat(MODEL_CHAT, [
                {"role": "system", "content": CHAT_SYSTEM},
                {"role": "user",
                 "content": context + "\nOPERATOR QUESTION: " + text}],
                max_tokens=400)
        except LLMUnavailable:
            return None
        return {"answer": answer.strip(),
                "model": MODEL_CHAT,
                "entities": [i.id for i in active][:10],
                "grounded": True}

    def _deterministic_query(self, text: str) -> Dict[str, Any]:
        q = text.lower()
        graph = self._graph

        if "delay" in q or "route" in q or "bus" in q:
            slow = [v for v in graph.vehicles.values() if v.speed_mph < 8.0]
            return {
                "answer": (f"{len(slow)} transit vehicle(s) currently moving "
                           f"below 8 mph (likely delayed)."),
                "entities": [v.id for v in slow][:20],
                "routes": sorted({v.route for v in slow}),
            }
        if "congest" in q or "gridlock" in q:
            worst = sorted(graph.intersections.values(),
                           key=lambda i: i.congestion, reverse=True)[:5]
            return {
                "answer": "Top congested intersections: " + "; ".join(
                    f"{i.name} ({i.congestion:.0%})" for i in worst),
                "entities": [i.id for i in worst],
            }
        if "incident" in q:
            active = [i for i in graph.incidents.values()
                      if i.state.value not in ("resolved", "closed")]
            return {
                "answer": f"{len(active)} active incident(s).",
                "entities": [i.id for i in active],
            }
        if "weather" in q:
            w = graph.weather
            return {
                "answer": (f"Current weather: {w.condition}, "
                           f"{w.temperature_f:.0f}°F"
                           + (", SEVERE ALERT ACTIVE" if w.severe_alert else ""))
                if w else "No weather data available.",
                "entities": [],
            }
        return {
            "answer": ("I can answer grounded questions about congestion, "
                       "incidents, transit delays, and weather in the city "
                       "model."),
            "entities": [],
        }

    def _enforce_rate_limit(self, operator_id: str) -> None:
        with self._lock:
            times = self._query_times.setdefault(operator_id, deque())
            now = time.time()
            while times and now - times[0] > RATE_LIMIT_WINDOW_S:
                times.popleft()
            if len(times) >= RATE_LIMIT_MAX_QUERIES:
                raise RateLimitExceeded(
                    f"Rate limit: {RATE_LIMIT_MAX_QUERIES} queries per "
                    f"{RATE_LIMIT_WINDOW_S / 60:.0f} minutes.")
            times.append(now)

    def _sanitize(self, operator_id: str, text: str) -> None:
        for pattern in INJECTION_PATTERNS:
            if pattern.search(text):
                with self._lock:
                    self._injection_attempts.append({
                        "operator_id": operator_id,
                        "query": text[:200],
                        "pattern": pattern.pattern,
                        "at": time.time(),
                    })
                raise InjectionBlocked(
                    "Query blocked: prompt-injection pattern detected. "
                    "The attempt has been logged for security review.")

    def injection_attempts(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._injection_attempts)