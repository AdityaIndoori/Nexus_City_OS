"""
Nexus City OS — Core domain models.

The city is modeled as a living graph of strongly-typed entities (PRD §1.2).
The schema is extensible: new entity types register via EntityType without
breaking changes (PRD extensibility requirement).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

MODEL_VERSION = "1.0.0"  # semver — recorded in every audit entry (PRD §4.6)


def now_ts() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


class OperatingMode(str, Enum):
    """Shadow → Advisory → Live product ladder (PRD Scope Variants, §7.1)."""
    SHADOW = "shadow"
    ADVISORY = "advisory"
    LIVE = "live"


class Role(str, Enum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    OPERATOR = "operator"
    ADMIN = "admin"


class IncidentState(str, Enum):
    """Formal incident lifecycle (PRD §2)."""
    DETECTED = "detected"
    ACKNOWLEDGED = "acknowledged"
    MITIGATING = "mitigating"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentType(str, Enum):
    COLLISION = "collision"
    STOPPED_VEHICLE = "stopped_vehicle"
    WRONG_WAY_DRIVER = "wrong_way_driver"
    PEDESTRIAN_ON_HIGHWAY = "pedestrian_on_highway"
    CONGESTION = "congestion"
    TRANSIT_BREAKDOWN = "transit_breakdown"


class IncidentStatusFlag(str, Enum):
    NONE = "none"
    EMS_RESPONDING = "EMS_RESPONDING"


class PlanStatus(str, Enum):
    GENERATED = "generated"
    BLOCKED_CONSTRAINT = "blocked_constraint"
    BLOCKED_HALLUCINATION = "blocked_hallucination"
    SUPPRESSED_PROVENANCE = "suppressed_provenance"
    WITHHELD_CONFIDENCE = "withheld_confidence"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTED = "executed"
    SHADOW_LOGGED = "shadow_logged"
    ADVISORY_ISSUED = "advisory_issued"
    REJECTED = "rejected"
    REVERTED = "reverted"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Graph entities
# ---------------------------------------------------------------------------

@dataclass
class SignalPhase:
    phase_id: int
    movement: str                  # "through" | "left_turn"
    green_seconds: float
    yellow_seconds: float
    red_clearance_seconds: float
    approach_speed_mph: float
    conflicts_with: List[int] = field(default_factory=list)


@dataclass
class SignalTimingPlan:
    plan_id: str
    intersection_id: str
    cycle_seconds: float
    phases: List[SignalPhase]
    pedestrian_walk_seconds: float
    crosswalk_length_ft: float
    near_school_or_senior_center: bool = False

    def copy(self) -> "SignalTimingPlan":
        return SignalTimingPlan(
            plan_id=new_id("STP"),
            intersection_id=self.intersection_id,
            cycle_seconds=self.cycle_seconds,
            phases=[SignalPhase(p.phase_id, p.movement, p.green_seconds,
                                p.yellow_seconds, p.red_clearance_seconds,
                                p.approach_speed_mph, list(p.conflicts_with))
                    for p in self.phases],
            pedestrian_walk_seconds=self.pedestrian_walk_seconds,
            crosswalk_length_ft=self.crosswalk_length_ft,
            near_school_or_senior_center=self.near_school_or_senior_center,
        )


@dataclass
class Intersection:
    id: str
    name: str
    lat: float
    lon: float
    monitored: bool                 # camera coverage (PRD §1.2 coverage tracking)
    timing_plan: SignalTimingPlan
    intersection_width_ft: float = 80.0
    congestion: float = 0.2         # 0..1 congestion index
    ems_corridor: bool = False      # part of an emergency response corridor


@dataclass
class RoadSegment:
    id: str
    from_intersection: str
    to_intersection: str
    name: str
    speed_limit_mph: float
    current_speed_mph: float
    length_miles: float = 0.1


@dataclass
class TransitVehicle:
    id: str
    route: str
    lat: float
    lon: float
    speed_mph: float
    last_update: float = field(default_factory=now_ts)


@dataclass
class Camera:
    id: str
    intersection_id: str
    lat: float
    lon: float
    online: bool = True
    last_frame_ts: float = field(default_factory=now_ts)
    redaction_enabled: bool = True   # PII redaction at the edge (PRD §11.6)


@dataclass
class WeatherCondition:
    condition: str                   # "clear" | "rain" | "snow" | "ice" | "fog"
    temperature_f: float
    severe_alert: bool
    observed_at: float = field(default_factory=now_ts)


@dataclass
class Incident:
    id: str
    type: IncidentType
    intersection_id: str
    severity: float                  # 0..1
    state: IncidentState = IncidentState.DETECTED
    status_flag: IncidentStatusFlag = IncidentStatusFlag.NONE
    detected_at: float = field(default_factory=now_ts)
    acknowledged_at: Optional[float] = None
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[float] = None
    resolution: Optional[str] = None
    description: str = ""
    action_history: List[Dict[str, Any]] = field(default_factory=list)
    detection_source: str = "edge_simulator"   # "edge_simulator" | "ai_vision"
    camera_id: str = ""                          # camera that produced the detection
    # AI classification provenance (PRD §4.2): WHY the platform classified
    # this activity as this incident type. Populated for AI-vision detections
    # (the Claude Haiku assessment) and a deterministic explanation otherwise.
    ai_justification: str = ""
    ai_confidence: Optional[float] = None        # 0..100, model self-assessment
    # Free-form operator notes (auto-saved from the incident workspace,
    # persisted, audit-logged on change). Shift-handover context lives here.
    operator_notes: str = ""
    # The actual camera frame AT DETECTION TIME (jpeg bytes), frozen so the
    # operator always sees what the detector saw — never the latest live image.
    # Kept in memory only; never serialized into JSON status payloads.
    detection_frame_jpeg: Optional[bytes] = field(default=None, repr=False)



# ---------------------------------------------------------------------------
# ActionPlan — the strictly typed AI output (PRD §4.1–4.3, MASTER_PROMPT §3C)
# ---------------------------------------------------------------------------

@dataclass
class Operation:
    type: str                        # "extend_green" | "reduce_green" | "adjust_cycle"
    intersection_id: str
    phase_id: int
    delta_seconds: float


@dataclass
class Provenance:
    """Mandatory provenance (PRD §4.2). Plans missing it are auto-suppressed."""
    entities: List[str]
    data_sources: List[Dict[str, Any]]   # [{"source": ..., "timestamp": ...}]
    weather: Optional[Dict[str, Any]]
    rationale: str

    def is_complete(self) -> bool:
        return bool(self.entities) and bool(self.data_sources) and bool(self.rationale) \
            and self.weather is not None


@dataclass
class ConfidenceBreakdown:
    """Composite confidence per PRD §4.3 weighting."""
    model_certainty: float           # 0..100, weight 40%
    data_freshness: float            # 0..100, weight 25%
    coverage_completeness: float     # 0..100, weight 20%
    historical_accuracy: float       # 0..100, weight 15%

    @property
    def composite(self) -> float:
        return round(
            0.40 * self.model_certainty
            + 0.25 * self.data_freshness
            + 0.20 * self.coverage_completeness
            + 0.15 * self.historical_accuracy, 1)


@dataclass
class ActionPlan:
    plan_id: str
    created_at: float
    model_version: str
    incident_id: str
    targets: List[str]
    operations: List[Operation]
    justification: str
    provenance: Provenance
    confidence: ConfidenceBreakdown
    requires_human_approval: bool = True   # constant — HITL gatekeeping
    status: PlanStatus = PlanStatus.GENERATED
    block_reason: Optional[str] = None
    simulation: Optional[Dict[str, Any]] = None
    approved_by: Optional[str] = None
    approved_at: Optional[float] = None
    executed_at: Optional[float] = None
    expires_at: Optional[float] = None     # Advisory instructions expire in 15 min
    previous_timing: Dict[str, Any] = field(default_factory=dict)

    def plan_hash(self) -> str:
        """Stable hash of the exact plan content the operator approves."""
        payload = {
            "plan_id": self.plan_id,
            "targets": sorted(self.targets),
            "operations": [asdict(o) for o in self.operations],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["plan_hash"] = self.plan_hash()
        d["confidence_score"] = self.confidence.composite
        return d


# ---------------------------------------------------------------------------
# Telemetry payloads (edge → platform)
# ---------------------------------------------------------------------------

@dataclass
class EdgeTelemetry:
    """Structured, PII-redacted metadata emitted by the edge layer."""
    camera_id: str
    intersection_id: str
    captured_at: float
    vehicle_count: int
    avg_speed_mph: float
    stopped_vehicles: int
    anomaly: Optional[str]           # IncidentType value or None
    redacted: bool                   # must be True or the platform rejects it
    source: str = "edge_simulator"   # "edge_simulator" | "ai_vision"
    # Classification provenance carried from the detector to the incident:
    # the human-readable justification (the AI-vision assessment), the model's
    # self-reported confidence, and the base64 jpeg of the frame the detector
    # actually saw (frozen detection-time evidence).
    ai_assessment: str = ""
    ai_confidence: Optional[float] = None
    frame_b64: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> "EdgeTelemetry":
        data = json.loads(raw)
        conf = data.get("ai_confidence")
        return EdgeTelemetry(
            camera_id=str(data["camera_id"]),
            intersection_id=str(data["intersection_id"]),
            captured_at=float(data["captured_at"]),
            vehicle_count=int(data["vehicle_count"]),
            avg_speed_mph=float(data["avg_speed_mph"]),
            stopped_vehicles=int(data["stopped_vehicles"]),
            anomaly=data.get("anomaly"),
            redacted=bool(data["redacted"]),
            source=str(data.get("source", "edge_simulator")),
            ai_assessment=str(data.get("ai_assessment", "")),
            ai_confidence=float(conf) if isinstance(conf, (int, float))
            else None,
            frame_b64=data.get("frame_b64"),
        )



# Freshness thresholds in seconds (PRD §1, Data Freshness Requirements)
FRESHNESS_THRESHOLDS: Dict[str, float] = {
    "camera": 5.0,
    "transit_gps": 15.0,
    "closures": 15 * 60.0,
    "weather": 10 * 60.0,
}