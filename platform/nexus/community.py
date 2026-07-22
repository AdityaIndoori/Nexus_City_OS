"""
Nexus City OS — Community Watch (civilian participation layer).

Civilians are invited through Cloudflare Access (Role.CITIZEN via the
email→role map — strictly outside the operator RBAC ladder); their profile
(display name from the email local-part, home location, customizable alert
radius) is auto-created on their first mutating community call. They can:

  * CONFIRM an active incident with a geo-checked photo. The photo passes
    the AI vision gate (Copilot.analyze_frame); a verified confirmation is
    appended to the ACTUAL incident's action history and the hash-chained
    audit trail — never published as a separate post.
  * REPORT a new incident with a photo. Vision-verified reports become real
    platform incidents (detection_source="community", the citizen photo is
    the frozen evidence frame) flowing through the standard operator queue.
    Reports matching an existing active incident attach as corroboration
    instead of duplicating it.
  * COMMENT on incidents (length-capped, prompt-injection-guarded via the
    copilot _sanitize gate).

Trust ladder: vision-verified → published; vision-rejected → discarded;
vision unavailable → PENDING moderation queue for operators (unverified
content is never auto-published). Every publish/moderation decision lands
in the audit chain. Citizens can never reach a governance action: the
server hard-gates them off every operator API. Service-token principals
(``svc:*``, empty email) are machines — community WRITES reject them.
"""
from __future__ import annotations

import base64
import binascii
import math
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .copilot import RateLimitExceeded
from .models import Incident, IncidentType, new_id, now_ts
from .store import Store

RADIUS_MIN_M = 100.0            # governed alert-radius range
RADIUS_MAX_M = 10000.0
DEFAULT_RADIUS_M = 1500.0
CONFIRM_PROXIMITY_M = 750.0     # must plausibly SEE the incident
COVERAGE_RADIUS_M = 500.0       # report must land near a known intersection
VISION_MIN_CONFIDENCE = 50.0    # below this the model is guessing
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_NOTE_LEN = 300
MAX_COMMENT_LEN = 500
REPORT_LIMIT = 5                # submissions per user per window
REPORT_WINDOW_S = 3600.0

REPUTATION_VERIFIED = 5         # verified evidence earns standing
REPUTATION_REJECTED = -1        # crying wolf costs it


def _dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    coslat = math.cos(math.radians(lat1))
    return math.hypot((lat2 - lat1) * 111000.0,
                      (lon2 - lon1) * 111000.0 * coslat)


class CommunityHub:
    """Profiles, nearby feed, photo-verified reports, comments."""

    def __init__(self, engine, store: Store) -> None:
        self._engine = engine
        self._store = store
        self._lock = threading.RLock()
        # per-user report throttle: user_id -> deque[submission ts]
        self._report_times: Dict[str, Deque[float]] = {}

    # ---- identity gates ------------------------------------------------------

    @staticmethod
    def _require_person(user_id: str) -> None:
        # Service-token principals (svc:<client-id>) are machines — they
        # can read but never author community content.
        if user_id.startswith("svc:"):
            raise ValueError(
                "Service-token identities cannot post to Community Watch.")

    def _ensure_profile(self, user_id: str) -> Dict[str, Any]:
        """Idempotent lazy profile upsert on first mutating community call.
        Display name defaults to the email local-part."""
        profile = self._store.get_community_profile(user_id)
        if profile is not None:
            return profile
        profile = self._default_profile(user_id)
        profile["display_name"] = (
            user_id.split("@", 1)[0].strip()[:40] or user_id)
        self._store.upsert_community_profile(user_id, profile, now_ts())
        return profile

    # ---- profile -----------------------------------------------------------

    @staticmethod
    def _default_profile(user_id: str) -> Dict[str, Any]:
        return {"user_id": user_id, "display_name": user_id,
                "home_lat": None, "home_lon": None,
                "radius_m": DEFAULT_RADIUS_M,
                "notify": True, "joined_at": now_ts(),
                "reputation": 0, "reports_total": 0, "reports_verified": 0}

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        profile = self._store.get_community_profile(user_id)
        if profile is None:
            profile = self._default_profile(user_id)
        return profile

    def update_profile(self, user_id: str,
                       fields: Dict[str, Any]) -> Dict[str, Any]:
        self._require_person(user_id)
        self._ensure_profile(user_id)
        profile = self.get_profile(user_id)
        # Only citizen-editable fields; the acting user comes from the
        # verified token — a user_id in the body is ignored.
        if "display_name" in fields:
            name = str(fields["display_name"]).strip()[:40]
            if name:
                profile["display_name"] = name
        if "home_lat" in fields and "home_lon" in fields:
            try:
                profile["home_lat"] = float(fields["home_lat"])
                profile["home_lon"] = float(fields["home_lon"])
            except (TypeError, ValueError):
                raise ValueError("home_lat/home_lon must be numbers.")
        if "radius_m" in fields:
            try:
                radius = float(fields["radius_m"])
            except (TypeError, ValueError):
                raise ValueError("radius_m must be a number.")
            profile["radius_m"] = min(RADIUS_MAX_M, max(RADIUS_MIN_M, radius))
        if "notify" in fields:
            profile["notify"] = bool(fields["notify"])
        self._store.upsert_community_profile(user_id, profile, now_ts())
        return profile

    def _display_name(self, user_id: str) -> str:
        profile = self._store.get_community_profile(user_id)
        return profile["display_name"] if profile else user_id

    def _bump_reputation(self, user_id: str, verified: bool) -> None:
        profile = self.get_profile(user_id)
        profile["reports_total"] += 1
        if verified:
            profile["reports_verified"] += 1
            profile["reputation"] += REPUTATION_VERIFIED
        else:
            profile["reputation"] = max(
                0, profile["reputation"] + REPUTATION_REJECTED)
        self._store.upsert_community_profile(user_id, profile, now_ts())

    # ---- nearby feed ---------------------------------------------------------

    def nearby(self, user_id: str, lat: Optional[float] = None,
               lon: Optional[float] = None,
               radius: Optional[float] = None) -> Dict[str, Any]:
        profile = self.get_profile(user_id)
        if lat is None or lon is None:
            lat, lon = profile.get("home_lat"), profile.get("home_lon")
        if lat is None or lon is None:
            raise ValueError(
                "No location: set your home location in the profile or "
                "pass lat/lon.")
        radius = float(radius) if radius is not None \
            else float(profile.get("radius_m", DEFAULT_RADIUS_M))
        radius = min(RADIUS_MAX_M, max(RADIUS_MIN_M, radius))
        graph = self._engine.graph
        rows: List[Dict[str, Any]] = []
        for inc in graph.incidents.values():
            if inc.state.value in ("resolved", "closed"):
                continue
            inter = graph.intersections.get(inc.intersection_id)
            if inter is None:
                continue
            dist = _dist_m(float(lat), float(lon), inter.lat, inter.lon)
            if dist > radius:
                continue
            confirmations = self._store.community_reports(
                status="verified", incident_id=inc.id, limit=50)
            comments = self._store.community_comments(inc.id, limit=1)
            rows.append({
                "id": inc.id, "type": inc.type.value,
                "state": inc.state.value, "severity": inc.severity,
                "detected_at": inc.detected_at,
                "detection_source": inc.detection_source,
                "intersection_name": inter.name,
                "lat": inter.lat, "lon": inter.lon,
                "distance_m": round(dist, 1),
                "confirmations": len(confirmations),
                "has_comments": bool(comments),
            })
        rows.sort(key=lambda r: r["distance_m"])
        return {"center": {"lat": float(lat), "lon": float(lon)},
                "radius_m": radius, "incidents": rows[:50],
                "generated_at": now_ts()}

    # ---- photo handling ------------------------------------------------------

    @staticmethod
    def _decode_photo(photo_b64: str) -> bytes:
        if not photo_b64:
            raise ValueError("A photo is required.")
        try:
            photo = base64.b64decode(photo_b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("photo_b64 is not valid base64.")
        if not photo:
            raise ValueError("A photo is required.")
        if len(photo) > MAX_PHOTO_BYTES:
            raise ValueError("Photo exceeds the 5 MB limit.")
        return photo

    def _vision_verdict(self, photo: bytes, context: str) -> Dict[str, Any]:
        """Run the AI vision gate. Returns {"status": verified|rejected|
        pending, "analysis": {...}}. Unverifiable content is NEVER
        auto-published — it parks in the moderation queue."""
        analysis = self._engine.copilot.analyze_frame(photo, context)
        if not analysis.get("available"):
            return {"status": "pending", "analysis": analysis}
        visible = bool(analysis.get("incident_visible"))
        confidence = float(analysis.get("confidence_pct", 0.0) or 0.0)
        if visible and confidence >= VISION_MIN_CONFIDENCE:
            return {"status": "verified", "analysis": analysis}
        return {"status": "rejected", "analysis": analysis}

    # ---- confirmations (attach to the REAL incident) -------------------------

    def confirm(self, user_id: str, incident_id: str, lat: Any, lon: Any,
                note: str, photo_b64: str) -> Dict[str, Any]:
        self._require_person(user_id)
        self._ensure_profile(user_id)
        inc = self._engine.graph.incidents.get(incident_id)
        if inc is None:
            raise KeyError(f"Unknown incident {incident_id}")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            raise ValueError("lat/lon are required numbers.")
        inter = self._engine.graph.intersections.get(inc.intersection_id)
        if inter is not None:
            dist = _dist_m(lat, lon, inter.lat, inter.lon)
            if dist > CONFIRM_PROXIMITY_M:
                raise ValueError(
                    f"You must be close to the incident to confirm it "
                    f"(within {int(CONFIRM_PROXIMITY_M)} m; you are "
                    f"{int(dist)} m away).")
        note = str(note or "").strip()[:MAX_NOTE_LEN]
        if note:
            self._engine.copilot._sanitize(user_id, note)
        photo = self._decode_photo(photo_b64)
        self._throttle(user_id)
        inter_name = inter.name if inter else inc.intersection_id
        verdict = self._vision_verdict(
            photo,
            f"A community member photographed the scene near {inter_name} "
            f"to confirm a reported {inc.type.value.replace('_', ' ')}. "
            f"Does the photo show a real traffic incident?")
        report_id = new_id("CRPT")
        payload = {"lat": lat, "lon": lon, "note": note,
                   "type": inc.type.value,
                   "display_name": self._display_name(user_id),
                   "vision": {k: v for k, v in verdict["analysis"].items()
                              if k != "error"} if verdict["analysis"] else {}}
        self._store.insert_community_report(
            report_id, user_id, inc.id, "confirm", verdict["status"],
            payload, photo, now_ts())
        if verdict["status"] == "verified":
            self._attach_confirmation(user_id, inc, report_id, note,
                                      verdict["analysis"])
        self._bump_reputation(user_id, verdict["status"] == "verified")
        return {"report_id": report_id, "incident_id": inc.id,
                "status": verdict["status"],
                "vision": verdict["analysis"].get("assessment", "")}

    def _attach_confirmation(self, user_id: str, inc: Incident,
                             report_id: str, note: str,
                             analysis: Dict[str, Any]) -> None:
        """A verified confirmation becomes PART OF the incident record."""
        display = self._display_name(user_id)
        inc.action_history.append({
            "at": now_ts(), "actor": user_id,
            "action": f"community confirmation by {display}"
                      + (f": {note}" if note else "")
                      + " (AI-vision verified)"})
        self._engine.audit.record(
            actor=user_id, action="community_confirmation",
            targets=[inc.intersection_id],
            after_state={"incident_id": inc.id, "report_id": report_id,
                         "vision_confidence":
                             analysis.get("confidence_pct")},
            detail=f"photo confirmation attached to {inc.id}")
        self._engine._persist_incident(inc)
        self._engine.emit_event("community")

    # ---- citizen reports (create REAL incidents) -----------------------------

    def report(self, user_id: str, lat: Any, lon: Any, itype: str,
               note: str, photo_b64: str) -> Dict[str, Any]:
        self._require_person(user_id)
        self._ensure_profile(user_id)
        self._throttle(user_id)
        try:
            incident_type = IncidentType(str(itype))
        except ValueError:
            raise ValueError(
                f"Unknown incident type '{itype}'. Valid: "
                + ", ".join(t.value for t in IncidentType))
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            raise ValueError("lat/lon are required numbers.")
        iid = self._engine._nearest_intersection(lat, lon, COVERAGE_RADIUS_M)
        if iid is None:
            raise ValueError(
                "That location is outside the pilot coverage area "
                f"(no monitored intersection within "
                f"{int(COVERAGE_RADIUS_M)} m).")
        note = str(note or "").strip()[:MAX_NOTE_LEN]
        if note:
            self._engine.copilot._sanitize(user_id, note)
        photo = self._decode_photo(photo_b64)
        inter = self._engine.graph.intersections.get(iid)
        inter_name = inter.name if inter else iid
        # Dedupe FIRST: an active incident at this intersection means the
        # citizen is corroborating, not discovering.
        existing = next(
            (i for i in self._engine.graph.incidents.values()
             if i.intersection_id == iid
             and i.state.value not in ("resolved", "closed")), None)
        verdict = self._vision_verdict(
            photo,
            f"A community member reported a "
            f"{incident_type.value.replace('_', ' ')} near {inter_name} "
            f"and attached this photo. Does it show a real traffic "
            f"incident?")
        report_id = new_id("CRPT")
        payload = {"lat": lat, "lon": lon, "note": note,
                   "type": incident_type.value,
                   "intersection_id": iid,
                   "intersection_name": inter_name,
                   "display_name": self._display_name(user_id),
                   "vision": {k: v for k, v in verdict["analysis"].items()
                              if k != "error"} if verdict["analysis"] else {}}
        status = verdict["status"]
        incident_id: Optional[str] = None
        attached = False
        if status == "verified":
            if existing is not None:
                self._attach_confirmation(user_id, existing, report_id,
                                          note, verdict["analysis"])
                incident_id, attached = existing.id, True
            else:
                incident_id = self._publish_incident(
                    user_id, report_id, incident_type, iid, note, photo,
                    verdict["analysis"])
        self._store.insert_community_report(
            report_id, user_id, incident_id, "report", status, payload,
            photo, now_ts())
        self._bump_reputation(user_id, status == "verified")
        return {"report_id": report_id, "status": status,
                "incident_id": incident_id, "attached": attached,
                "vision": verdict["analysis"].get("assessment", "")}

    def _publish_incident(self, user_id: str, report_id: str,
                          incident_type: IncidentType, iid: str, note: str,
                          photo: bytes,
                          analysis: Dict[str, Any]) -> str:
        display = self._display_name(user_id)
        inter = self._engine.graph.intersections.get(iid)
        confidence = analysis.get("confidence_pct")
        incident = Incident(
            id=new_id("INC"), type=incident_type, intersection_id=iid,
            severity=0.7,
            description=(f"Community report by {display} near "
                         f"{inter.name if inter else iid}"
                         + (f": {note}" if note else "")),
            detection_source="community",
            ai_justification=str(analysis.get("assessment", ""))[:400],
            ai_confidence=float(confidence)
            if isinstance(confidence, (int, float)) else None,
            detection_frame_jpeg=photo)
        incident.action_history.append({
            "at": now_ts(), "actor": user_id,
            "action": f"reported by community member {display} "
                      f"(AI-vision verified, report {report_id})"})
        self._engine.graph.add_incident(incident)
        self._engine.audit.record(
            actor=user_id, action="incident_detected",
            targets=[iid],
            after_state={"incident_id": incident.id,
                         "type": incident_type.value, "severity": 0.7,
                         "detection_source": "community",
                         "report_id": report_id})
        self._engine._persist_incident(incident)
        self._engine._alert(
            "incident_detected",
            f"Community-reported {incident_type.value.replace('_', ' ')} "
            f"near {inter.name if inter else iid}", "high")
        self._engine.emit_event("incident")
        return incident.id

    def _throttle(self, user_id: str) -> None:
        now = now_ts()
        with self._lock:
            times = self._report_times.setdefault(user_id, deque())
            while times and now - times[0] > REPORT_WINDOW_S:
                times.popleft()
            if len(times) >= REPORT_LIMIT:
                raise RateLimitExceeded(
                    f"Community submission limit reached "
                    f"({REPORT_LIMIT} per hour). Try again later.")
            times.append(now)

    # ---- comments ------------------------------------------------------------

    def comment(self, user_id: str, incident_id: str,
                text: str) -> Dict[str, Any]:
        self._require_person(user_id)
        self._ensure_profile(user_id)
        inc = self._engine.graph.incidents.get(incident_id)
        if inc is None:
            raise KeyError(f"Unknown incident {incident_id}")
        text = str(text or "").strip()
        if not text:
            raise ValueError("Comment text is required.")
        if len(text) > MAX_COMMENT_LEN:
            raise ValueError(
                f"Comment exceeds {MAX_COMMENT_LEN} characters.")
        # Prompt-injection guard: community text can end up in AI context.
        self._engine.copilot._sanitize(user_id, text)
        comment_id = new_id("CCMT")
        self._store.insert_community_comment(
            comment_id, incident_id, user_id, text, now_ts())
        self._engine.emit_event("community")
        return {"comment_id": comment_id, "incident_id": incident_id,
                "at": now_ts()}

    # ---- public incident view --------------------------------------------------

    def incident_view(self, incident_id: str) -> Dict[str, Any]:
        inc = self._engine.graph.incidents.get(incident_id)
        if inc is None:
            raise KeyError(f"Unknown incident {incident_id}")
        inter = self._engine.graph.intersections.get(inc.intersection_id)
        confirmations = []
        for r in self._store.community_reports(status="verified",
                                               incident_id=incident_id):
            confirmations.append({
                "report_id": r["report_id"],
                "display_name": r.get("display_name", r["user_id"]),
                "note": r.get("note", ""), "at": r["created_at"],
                "kind": r.get("kind", "confirm"), "has_photo": True,
                "vision_confidence":
                    (r.get("vision") or {}).get("confidence_pct")})
        comments = []
        for c in self._store.community_comments(incident_id):
            comments.append({
                "comment_id": c["comment_id"],
                "display_name": self._display_name(c["user_id"]),
                "text": c["text"], "at": c["at"]})
        return {
            "id": inc.id, "type": inc.type.value,
            "state": inc.state.value, "severity": inc.severity,
            "detected_at": inc.detected_at,
            "detection_source": inc.detection_source,
            "intersection_name": inter.name if inter else
            inc.intersection_id,
            "lat": inter.lat if inter else None,
            "lon": inter.lon if inter else None,
            "confirmations": confirmations,
            "comments": comments,
        }

    # ---- moderation (operators) --------------------------------------------------

    def pending(self) -> List[Dict[str, Any]]:
        return [{k: v for k, v in r.items() if k != "photo"}
                for r in self._store.community_reports(status="pending")]

    def moderate(self, operator_id: str, report_id: str,
                 decision: str) -> Dict[str, Any]:
        report = self._store.get_community_report(report_id)
        if report is None:
            raise KeyError(f"Unknown report {report_id}")
        if report["status"] != "pending":
            raise ValueError("Report has already been moderated.")
        if decision not in ("approve", "reject"):
            raise ValueError("decision must be 'approve' or 'reject'.")
        incident_id = report.get("incident_id")
        status = "rejected"
        if decision == "approve":
            status = "verified"
            analysis = {"assessment": f"operator-approved by {operator_id}",
                        "confidence_pct": None}
            if report["kind"] == "confirm":
                inc = self._engine.graph.incidents.get(incident_id or "")
                if inc is not None:
                    self._attach_confirmation(
                        report["user_id"], inc, report_id,
                        report.get("note", ""), analysis)
            else:
                iid = report.get("intersection_id")
                existing = next(
                    (i for i in self._engine.graph.incidents.values()
                     if i.intersection_id == iid
                     and i.state.value not in ("resolved", "closed")), None)
                if existing is not None:
                    self._attach_confirmation(
                        report["user_id"], existing, report_id,
                        report.get("note", ""), analysis)
                    incident_id = existing.id
                else:
                    incident_id = self._publish_incident(
                        report["user_id"], report_id,
                        IncidentType(report.get("type", "collision")),
                        iid, report.get("note", ""),
                        report.get("photo") or b"", analysis)
            self._bump_reputation(report["user_id"], True)
        self._store.update_community_report(report_id, status, incident_id)
        self._engine.audit.record(
            actor=operator_id, action="community_moderation",
            after_state={"report_id": report_id, "decision": decision,
                         "incident_id": incident_id},
            detail=f"community report {report_id} {decision}d")
        self._engine.emit_event("community")
        return {"report_id": report_id, "status": status,
                "incident_id": incident_id}

    # ---- photo retrieval -----------------------------------------------------------

    def photo(self, report_id: str) -> Dict[str, Any]:
        report = self._store.get_community_report(report_id)
        if report is None or not report.get("photo"):
            raise KeyError(f"No photo for report {report_id}")
        return {"photo": report["photo"], "status": report["status"]}