"""
Nexus City OS — Live municipal data clients (Seattle).

Real data sources (all public, no API keys beyond OneBusAway's TEST key):

  * SDOT/WSDOT traffic camera registry:
      https://web.seattle.gov/Travelers/api/Map/Data?zoomId=14&type=2
    Live JPEG frames:
      sdot  → https://www.seattle.gov/trafficcams/images/<file>
      wsdot → https://images.wsdot.wa.gov/nw/<file>
  * King County Metro vehicle positions (OneBusAway / GTFS-RT derived):
      https://api.pugetsound.onebusaway.org/api/where/vehicles-for-agency/1.json
  * National Weather Service observations (station KBFI — Boeing Field):
      https://api.weather.gov/stations/KBFI/observations/latest
  * Seattle Fire Dept Real-Time 911 dispatches (Citizen-app style feed,
    Socrata open data, minutes-fresh, geocoded):
      https://data.seattle.gov/resource/kzjm-xkqj.json
  * NWS active hazard alerts for the Seattle point:
      https://api.weather.gov/alerts/active?point=47.61,-122.33

Every client caches responses and degrades gracefully: on network failure it
returns the last good payload (and the platform's freshness tracker will
surface the staleness to operators per PRD §1 / §8 — never hiding failures).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import datetime as _datetime, timedelta as _timedelta, \
    timezone as _timezone
from typing import Any, Dict, List, Optional, Tuple


USER_AGENT = "NexusCityOS/1.0 (municipal traffic decision-support reference)"

CAMERA_REGISTRY_URL = ("https://web.seattle.gov/Travelers/api/Map/Data"
                       "?zoomId=14&type=2")
SDOT_IMAGE_BASE = "https://www.seattle.gov/trafficcams/images/"
WSDOT_IMAGE_BASE = "https://images.wsdot.wa.gov/nw/"
OBA_VEHICLES_URL_TMPL = ("https://api.pugetsound.onebusaway.org/api/where/"
                         "vehicles-for-agency/{agency}.json?key=TEST")
OBA_VEHICLES_URL = OBA_VEHICLES_URL_TMPL.format(agency="1")
NWS_OBSERVATION_URL_TMPL = ("https://api.weather.gov/stations/{station}/"
                            "observations/latest")
NWS_OBSERVATION_URL = NWS_OBSERVATION_URL_TMPL.format(station="KBFI")
SFD_911_URL = ("https://data.seattle.gov/resource/kzjm-xkqj.json"
               "?$order=datetime%20DESC&$limit=80")
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active?point=47.61,-122.33"
WSDOT_FLOW_URL = ("https://wsdot.wa.gov/Traffic/api/TrafficFlow/"
                  "TrafficFlowREST.svc/GetTrafficFlowsAsJson"
                  "?AccessCode={code}")
# WSDOT FlowReadingValue (1=free, 2=moderate, 3=heavy, 4=stop&go) → mph
WSDOT_FLOW_SPEEDS = {1: 55.0, 2: 40.0, 3: 25.0, 4: 10.0}

# SFD dispatch type → category + whether it plausibly impacts traffic.
# (Real-Time 911 types observed in the live feed.)
EMERGENCY_CATEGORIES = {
    "fire": ("fire", True), "fire in building": ("fire", True),
    "fire in single family res": ("fire", True),
    "car fire": ("fire", True), "brush fire": ("fire", True),
    "rubbish fire": ("fire", False),
    "auto fire alarm": ("fire_alarm", False),
    "automatic fire alarm resd": ("fire_alarm", False),
    "aid response": ("medical", False),
    "medic response": ("medical", True),
    "aid response yellow": ("medical", False),
    "motor vehicle accident": ("mva", True),
    "mvi - motor vehicle incident": ("mva", True),
    "mvi freeway": ("mva", True),
    "rescue extrication": ("rescue", True),
    "water rescue": ("rescue", False),
    "rescue elevator": ("rescue", False),
    "hazmat": ("hazmat", True),
    "natural gas leak": ("hazmat", True),
    "scenes of violence 7": ("violence", True),
    "assault w/weapons 7 per rule": ("violence", True),
    "shooting": ("violence", True),
}

# Bounding boxes (lat_min, lat_max, lon_min, lon_max)
DOWNTOWN_BBOX = (47.592, 47.632, -122.368, -122.318)   # original PRD pilot box
SEATTLE_BBOX = (47.48, 47.745, -122.46, -122.22)       # full city limits
# Tacoma / Pierce County + the South Sound I-5/SR-167 corridor (the
# regional camera registry's southern coverage runs along this corridor).
TACOMA_BBOX = (47.18, 47.35, -122.58, -122.24)


def _fetch_json(url: str, timeout: float = 12.0) -> Any:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_bytes(url: str, timeout: float = 12.0) -> Tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "image/jpeg")


def _us_pacific_is_dst(dt: "datetime") -> bool:
    """US DST: 2nd Sunday of March 02:00 → 1st Sunday of November 02:00.
    Evaluated against the naive local clock (good enough for a 1-hour
    freshness window; the only ambiguous instants are the two DST seams)."""
    year = dt.year
    # 2nd Sunday of March
    march = _datetime(year, 3, 8)
    dst_start = march + _timedelta(days=(6 - march.weekday()) % 7)
    dst_start = dst_start.replace(hour=2)
    # 1st Sunday of November
    nov = _datetime(year, 11, 1)
    dst_end = nov + _timedelta(days=(6 - nov.weekday()) % 7)
    dst_end = dst_end.replace(hour=2)
    return dst_start <= dt < dst_end


def _pacific_naive_to_epoch(ts: str) -> float:
    """Convert a naive 'YYYY-MM-DDTHH:MM:SS' string in America/Los_Angeles
    to a true Unix epoch, independent of the SERVER's timezone (Render = UTC).

    The Socrata 911 feed stamps dispatches in Pacific local time with no tz
    suffix; reading them with time.mktime() on a UTC host shifted everything
    ~7h into the future and the freshness filter dropped them all (911 ×0)."""
    try:
        naive = _datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return time.time()
    offset_h = -7 if _us_pacific_is_dst(naive) else -8   # PDT / PST
    aware = naive.replace(tzinfo=_timezone(_timedelta(hours=offset_h)))
    return aware.timestamp()



class _Cached:
    """Thread-safe TTL cache around a fetch function with stale fallback."""

    def __init__(self, fetch, ttl_s: float) -> None:
        self._fetch = fetch
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._value: Any = None
        self._fetched_at: float = 0.0
        self.last_error: Optional[str] = None
        self.last_success_at: Optional[float] = None

    def get(self) -> Any:
        with self._lock:
            now = time.time()
            if self._value is not None and now - self._fetched_at < self._ttl:
                return self._value
        try:
            value = self._fetch()
        except Exception as exc:  # noqa: BLE001 — degrade, never crash
            with self._lock:
                self.last_error = f"{type(exc).__name__}: {exc}"
                return self._value  # stale or None — caller handles
        with self._lock:
            self._value = value
            self._fetched_at = time.time()
            self.last_success_at = self._fetched_at
            self.last_error = None
            return value


class SeattleLiveData:
    """Aggregated live-data client for Puget Sound deployments.

    Parameterized (Phase 4 — multi-city SDK): the same client serves any
    OneBusAway agency / NWS station / region. The camera registry endpoint
    (web.seattle.gov/Travelers) covers WSDOT cameras across the region
    including Pierce County — filter with ``region_bbox``.

      * ``oba_agency``       — OneBusAway agency id ("1"=KC Metro,
                               "3"=Pierce Transit). TEST key, no signup.
      * ``nws_station``      — NWS observation station (KBFI / KTIW).
      * ``region_bbox``      — default camera/vehicle bounding box.
      * ``socrata_911_url``  — Socrata 911 feed URL, or None to disable
                               (health shows "disabled", emergencies()=[]).
    """

    def __init__(self, oba_agency: str = "1",
                 nws_station: str = "KBFI",
                 region_bbox: Optional[Tuple[float, float, float, float]]
                 = None,
                 socrata_911_url: Optional[str] = SFD_911_URL) -> None:
        self.oba_agency = str(oba_agency)
        self.nws_station = str(nws_station)
        self.region_bbox = region_bbox if region_bbox is not None \
            else SEATTLE_BBOX
        self.socrata_911_url = socrata_911_url
        self._oba_url = OBA_VEHICLES_URL_TMPL.format(agency=self.oba_agency)
        self._nws_url = NWS_OBSERVATION_URL_TMPL.format(
            station=self.nws_station)
        self._registry = _Cached(self._fetch_cameras, ttl_s=3600.0)
        self._vehicles = _Cached(self._fetch_vehicles, ttl_s=10.0)
        self._weather = _Cached(self._fetch_weather, ttl_s=300.0)
        self._emergencies = _Cached(self._fetch_emergencies, ttl_s=60.0)
        self._hazards = _Cached(self._fetch_hazard_alerts, ttl_s=300.0)
        self._flow = _Cached(self._fetch_flow, ttl_s=60.0)
        self._image_cache: Dict[str, Tuple[float, bytes, str]] = {}
        self._image_lock = threading.Lock()

    # -- camera registry (real SDOT/WSDOT cameras) -----------------------

    def _fetch_cameras(self) -> List[Dict[str, Any]]:
        """Fetch the FULL citywide SDOT/WSDOT camera registry."""
        data = _fetch_json(CAMERA_REGISTRY_URL)
        cams: List[Dict[str, Any]] = []
        for feature in data.get("Features", []):
            coord = feature.get("PointCoordinate") or [0, 0]
            lat, lon = float(coord[0]), float(coord[1])
            for cam in feature.get("Cameras", []):
                base = (WSDOT_IMAGE_BASE if cam.get("Type") == "wsdot"
                        else SDOT_IMAGE_BASE)
                cams.append({
                    "id": str(cam.get("Id") or cam.get("ImageUrl")),
                    "name": str(cam.get("Description", "Unknown")),
                    "lat": lat, "lon": lon,
                    "type": str(cam.get("Type", "sdot")),
                    "image_url": base + str(cam.get("ImageUrl", "")),
                })
        return cams

    def cameras(self, bbox: Optional[Tuple[float, float, float, float]]
                = None) -> List[Dict[str, Any]]:
        """All registry cameras, optionally filtered to a bounding box.
        Default (None) = entire city."""
        cams = self._registry.get() or []
        if bbox is None:
            return cams
        lat_min, lat_max, lon_min, lon_max = bbox
        return [c for c in cams
                if lat_min <= c["lat"] <= lat_max
                and lon_min <= c["lon"] <= lon_max]

    # -- transit vehicles (real King County Metro positions) -------------

    def _fetch_vehicles(self) -> List[Dict[str, Any]]:
        data = _fetch_json(self._oba_url)
        out: List[Dict[str, Any]] = []
        for v in data.get("data", {}).get("list", []):
            # KC Metro reports top-level "location"; some agencies (e.g.
            # Pierce Transit) only populate tripStatus.position.
            loc = v.get("location") \
                or (v.get("tripStatus") or {}).get("position")
            if not loc:
                continue
            updated_ms = (v.get("lastLocationUpdateTime")
                          or v.get("lastUpdateTime") or 0)
            out.append({
                "id": str(v.get("vehicleId", "")),
                "lat": float(loc["lat"]),
                "lon": float(loc["lon"]),
                "trip_id": str(v.get("tripId", "")),
                "updated_at": updated_ms / 1000.0,
            })
        return out

    def vehicles(self, bbox: Optional[Tuple[float, float, float, float]]
                 = None) -> List[Dict[str, Any]]:
        vehicles = self._vehicles.get() or []
        if bbox is None:
            return vehicles
        lat_min, lat_max, lon_min, lon_max = bbox
        return [v for v in vehicles
                if lat_min <= v["lat"] <= lat_max
                and lon_min <= v["lon"] <= lon_max]

    # -- weather (real NWS observation) -----------------------------------

    def _fetch_weather(self) -> Dict[str, Any]:
        data = _fetch_json(self._nws_url)
        props = data.get("properties", {})
        text = str(props.get("textDescription") or "").lower()
        temp_c = (props.get("temperature") or {}).get("value")
        temp_f = (temp_c * 9 / 5 + 32) if isinstance(temp_c, (int, float)) \
            else 50.0
        if "snow" in text:
            condition = "snow"
        elif "ice" in text or "freezing" in text:
            condition = "ice"
        elif "rain" in text or "drizzle" in text or "shower" in text:
            condition = "rain"
        elif "fog" in text or "mist" in text or "haze" in text:
            condition = "fog"
        else:
            condition = "clear"
        return {
            "condition": condition,
            "temperature_f": round(float(temp_f), 1),
            "raw_description": props.get("textDescription"),
            "station": f"{self.nws_station} (NWS)",
        }

    def weather(self) -> Optional[Dict[str, Any]]:
        return self._weather.get()

    # -- 911 emergency dispatches (SFD Real-Time 911, Citizen-style) ------

    def _fetch_emergencies(self) -> List[Dict[str, Any]]:
        if not self.socrata_911_url:
            return []
        rows = _fetch_json(self.socrata_911_url)
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                lat = float(r.get("latitude") or 0)
                lon = float(r.get("longitude") or 0)
            except (TypeError, ValueError):
                continue
            if not (47.0 < lat < 48.2 and -123.0 < lon < -121.5):
                continue
            raw_type = str(r.get("type", "Unknown"))
            cat, traffic = EMERGENCY_CATEGORIES.get(
                raw_type.lower(), ("other", False))
            # Socrata `datetime` is America/Los_Angeles local time with NO
            # timezone suffix. We must convert it to a true epoch using the
            # Pacific offset — NOT the server's local zone. (Render runs in
            # UTC, so the old time.mktime() read every dispatch ~7h in the
            # future and the "last hour" filter dropped them all → 911 ×0.)
            ts = str(r.get("datetime", ""))
            at = _pacific_naive_to_epoch(ts[:19])

            out.append({
                "id": "SFD-" + str(r.get("incident_number", ts)),
                "source": "sfd_realtime_911",
                "type": raw_type,
                "category": cat,
                "traffic_impacting": traffic,
                "address": str(r.get("address", "")),
                "lat": lat, "lon": lon,
                "at": at,
            })
        return out

    def emergencies(self, max_age_s: float = 3600.0,
                    bbox: Optional[Tuple[float, float, float, float]] = None,
                    ) -> List[Dict[str, Any]]:
        """Recent SFD 911 dispatches (default: last hour), newest first."""
        if not self.socrata_911_url:
            return []
        rows = self._emergencies.get() or []
        cutoff = time.time() - max_age_s
        rows = [r for r in rows if r["at"] >= cutoff]
        if bbox is not None:
            lat_min, lat_max, lon_min, lon_max = bbox
            rows = [r for r in rows
                    if lat_min <= r["lat"] <= lat_max
                    and lon_min <= r["lon"] <= lon_max]
        return rows

    # -- WSDOT traffic flow (optional — requires WSDOT_ACCESS_CODE) --------

    def _fetch_flow(self) -> List[Dict[str, Any]]:
        code = os.environ.get("WSDOT_ACCESS_CODE", "").strip()
        if not code:
            return []
        rows = _fetch_json(WSDOT_FLOW_URL.format(code=code))
        out: List[Dict[str, Any]] = []
        for r in rows:
            reading = r.get("FlowReadingValue")
            speed = WSDOT_FLOW_SPEEDS.get(reading)
            if speed is None:
                continue
            loc = r.get("FlowStationLocation") or {}
            try:
                lat = float(loc.get("Latitude") or 0)
                lon = float(loc.get("Longitude") or 0)
            except (TypeError, ValueError):
                continue
            if not lat or not lon:
                continue
            out.append({
                "id": str(r.get("FlowDataID", "")),
                "lat": lat, "lon": lon,
                "speed_mph": speed,
                "limit_mph": 55.0,
                "reading": reading,
                "description": str(loc.get("Description", "")),
            })
        return out

    def flow_speeds(self) -> List[Dict[str, Any]]:
        """WSDOT loop-detector flow speeds (mapped from FlowReadingValue).
        Empty when no WSDOT_ACCESS_CODE is configured — the platform then
        runs bus-GPS-only congestion estimation."""
        if not os.environ.get("WSDOT_ACCESS_CODE", "").strip():
            return []
        return self._flow.get() or []

    # -- NWS active hazard alerts ------------------------------------------

    def _fetch_hazard_alerts(self) -> List[Dict[str, Any]]:
        data = _fetch_json(NWS_ALERTS_URL)
        out = []
        for f in data.get("features", []):
            p = f.get("properties", {})
            out.append({
                "id": str(p.get("id", "")),
                "event": str(p.get("event", "")),
                "severity": str(p.get("severity", "")),
                "headline": str(p.get("headline", ""))[:200],
                "expires": str(p.get("expires", "")),
            })
        return out

    def hazard_alerts(self) -> List[Dict[str, Any]]:
        return self._hazards.get() or []

    # -- live camera frames (proxied to avoid browser CORS) ---------------

    def camera_image(self, camera_id: str, max_age_s: float = 60.0,
                     force_refresh: bool = False
                     ) -> Optional[Tuple[bytes, str, float]]:
        """Return (payload, content_type, fetched_at_unix). ``force_refresh``
        bypasses the cache to pull the newest frame from the source."""
        cam = next((c for c in self.cameras() if c["id"] == camera_id), None)
        if cam is None:
            return None
        if not force_refresh:
            with self._image_lock:
                cached = self._image_cache.get(camera_id)
                if cached and time.time() - cached[0] < max_age_s:
                    return cached[1], cached[2], cached[0]
        try:
            payload, content_type = _fetch_bytes(cam["image_url"])
        except Exception:  # noqa: BLE001
            with self._image_lock:
                cached = self._image_cache.get(camera_id)
                return (cached[1], cached[2], cached[0]) if cached else None
        fetched_at = time.time()
        with self._image_lock:
            self._image_cache[camera_id] = (fetched_at, payload, content_type)
        return payload, content_type, fetched_at

    # -- health -----------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return {
            "camera_registry": {
                "ok": self._registry.last_error is None,
                "error": self._registry.last_error,
                "last_success_at": self._registry.last_success_at,
            },
            "transit": {
                "ok": self._vehicles.last_error is None,
                "error": self._vehicles.last_error,
                "last_success_at": self._vehicles.last_success_at,
            },
            "weather": {
                "ok": self._weather.last_error is None,
                "error": self._weather.last_error,
                "last_success_at": self._weather.last_success_at,
            },
            "sfd_911": (
                {"ok": True, "state": "disabled",
                 "error": None, "last_success_at": None}
                if not self.socrata_911_url
                else {
                    "ok": self._emergencies.last_error is None,
                    "error": self._emergencies.last_error,
                    "last_success_at": self._emergencies.last_success_at,
                }),
            "nws_alerts": {
                "ok": self._hazards.last_error is None,
                "error": self._hazards.last_error,
                "last_success_at": self._hazards.last_success_at,
            },
            "wsdot_flow": (
                {"ok": True, "state": "disabled",
                 "error": None, "last_success_at": None}
                if not os.environ.get("WSDOT_ACCESS_CODE", "").strip()
                else {
                    "ok": self._flow.last_error is None,
                    "state": "enabled",
                    "error": self._flow.last_error,
                    "last_success_at": self._flow.last_success_at,
                }),
        }
