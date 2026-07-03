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
WSDOT_TRAVELTIMES_URL = ("https://wsdot.wa.gov/Traffic/api/TravelTimes/"
                         "TravelTimesREST.svc/GetTravelTimesAsJson"
                         "?AccessCode={code}")
WSDOT_ALERTS_URL = ("https://wsdot.wa.gov/Traffic/api/HighwayAlerts/"
                    "HighwayAlertsREST.svc/GetAlertsAsJson"
                    "?AccessCode={code}")
# WSDOT FlowReadingValue (1=free, 2=moderate, 3=heavy, 4=stop&go) → mph
WSDOT_FLOW_SPEEDS = {1: 55.0, 2: 40.0, 3: 25.0, 4: 10.0}
KMH_TO_MPH = 0.621371


def _waze_feed_url() -> str:
    """Waze for Cities (CCP) partner feed URL — a georss JSON endpoint
    issued to data-sharing partners, containing crowdsourced "jams" and
    "alerts". Read at call time so a deployment can set it without a code
    change; empty disables the feed."""
    return os.environ.get("NEXUS_WAZE_FEED_URL", "").strip()


def _dotnet_date_to_epoch(raw: Any) -> Optional[float]:
    """Parse the WSDOT '/Date(1633033200000-0700)/' .NET stamp → epoch s."""
    try:
        s = str(raw)
        start = s.index("(") + 1
        digits = ""
        for ch in s[start:]:
            if ch.isdigit() or (ch == "-" and not digits):
                digits += ch
            else:
                break
        return int(digits) / 1000.0
    except (ValueError, TypeError, AttributeError, IndexError):
        return None


def parse_travel_times(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize WSDOT TravelTimes rows → corridor cards. CurrentTime 0 or
    negative means 'no data' and is dropped."""
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        try:
            current = float(r.get("CurrentTime") or 0)
            average = float(r.get("AverageTime") or 0)
        except (TypeError, ValueError):
            continue
        if current <= 0:
            continue
        out.append({
            "id": str(r.get("TravelTimeID", "")),
            "name": str(r.get("Description") or r.get("Name") or ""),
            "distance_miles": float(r.get("Distance") or 0),
            "current_minutes": current,
            "average_minutes": average,
            # >1 means slower than typical; the UI colors on this.
            "ratio": round(current / average, 2) if average > 0 else None,
            "updated_at": _dotnet_date_to_epoch(r.get("TimeUpdated")),
        })
    out.sort(key=lambda t: -(t["ratio"] or 0))
    return out


_TAG_RE = __import__("re").compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """WSDOT headline descriptions embed raw HTML (links, breaks) —
    strip tags so the UI renders clean text."""
    return _TAG_RE.sub("", text).replace("&nbsp;", " ").strip()


def parse_highway_alerts(rows: List[Dict[str, Any]],
                         bbox: Optional[Tuple[float, float, float, float]]
                         = None) -> List[Dict[str, Any]]:
    """Normalize WSDOT HighwayAlerts rows (collisions, closures, construction)
    → alert cards, optionally filtered to a bounding box."""
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        loc = r.get("StartRoadwayLocation") or {}
        try:
            lat = float(loc.get("Latitude") or 0)
            lon = float(loc.get("Longitude") or 0)
        except (TypeError, ValueError):
            lat = lon = 0.0
        if bbox is not None and lat and lon:
            lat_min, lat_max, lon_min, lon_max = bbox
            if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                continue
        out.append({
            "id": str(r.get("AlertID", "")),
            "category": str(r.get("EventCategory", "")),
            "priority": str(r.get("Priority", "")),
            "headline": _strip_html(
                str(r.get("HeadlineDescription", "")))[:300],
            "road": str(loc.get("RoadName", "")),
            "lat": lat, "lon": lon,
            "updated_at": _dotnet_date_to_epoch(r.get("LastUpdatedTime")),
            "source": "wsdot_highway_alerts",
        })
    priority_rank = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3,
                     "Lowest": 4}
    out.sort(key=lambda a: priority_rank.get(a["priority"], 5))
    return out


def parse_waze_feed(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Parse a Waze for Cities (CCP) georss JSON payload into normalized
    ``jams`` (speed + polyline) and ``alerts`` (crowdsourced reports)."""
    jams: List[Dict[str, Any]] = []
    for j in (data or {}).get("jams", []) or []:
        line = [(float(p.get("y", 0)), float(p.get("x", 0)))
                for p in j.get("line", []) or []
                if isinstance(p, dict)]
        if not line:
            continue
        speed_kmh = j.get("speedKMH")
        if not isinstance(speed_kmh, (int, float)):
            # older feeds carry "speed" in m/s
            ms = j.get("speed")
            speed_kmh = float(ms) * 3.6 if isinstance(ms, (int, float)) \
                else None
        jams.append({
            "id": str(j.get("uuid") or j.get("id") or len(jams)),
            "street": str(j.get("street") or ""),
            "speed_mph": (round(float(speed_kmh) * KMH_TO_MPH, 1)
                          if speed_kmh is not None else None),
            "level": int(j.get("level") or 0),        # 0..5 (5 = blocked)
            "delay_s": int(j.get("delay") or 0),
            "line": line,                              # [(lat, lon), ...]
        })
    alerts: List[Dict[str, Any]] = []
    for a in (data or {}).get("alerts", []) or []:
        loc = a.get("location") or {}
        try:
            lat = float(loc.get("y", 0))
            lon = float(loc.get("x", 0))
        except (TypeError, ValueError):
            continue
        alerts.append({
            "id": str(a.get("uuid") or a.get("id") or len(alerts)),
            "type": str(a.get("type", "")),            # ACCIDENT / HAZARD / JAM…
            "subtype": str(a.get("subtype", "")),
            "street": str(a.get("street") or ""),
            "lat": lat, "lon": lon,
            "reliability": int(a.get("reliability") or 0),   # 0..10
            "at": float(a.get("pubMillis") or 0) / 1000.0,
            "source": "waze",
        })
    return {"jams": jams, "alerts": alerts}

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
    to a true Unix epoch, independent of the SERVER's timezone (a UTC host
    would otherwise shift every timestamp).

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
    """Thread-safe TTL cache around a fetch function with stale fallback.

    Failure backoff: after a fetch error, further refetch attempts are
    suppressed for ``error_backoff_s`` (stale value / None is returned
    immediately). Without this, a feed outage made EVERY caller re-attempt
    the blocking network fetch — request threads could stall for the full
    socket timeout on each call. A single-flight guard additionally ensures
    only one thread refreshes at a time; others get the stale value."""

    def __init__(self, fetch, ttl_s: float,
                 error_backoff_s: float = 15.0) -> None:
        self._fetch = fetch
        self._ttl = ttl_s
        self._error_backoff = error_backoff_s
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._value: Any = None
        self._fetched_at: float = 0.0
        self._last_attempt_at: float = 0.0
        self.last_error: Optional[str] = None
        self.last_success_at: Optional[float] = None

    def get(self) -> Any:
        with self._lock:
            now = time.time()
            if self._value is not None and now - self._fetched_at < self._ttl:
                return self._value
            # Failure backoff: don't hammer a broken feed.
            if (self.last_error is not None
                    and now - self._last_attempt_at < self._error_backoff):
                return self._value
        # Single-flight: only one thread performs the (blocking) refresh;
        # concurrent callers return the stale value immediately.
        if not self._refresh_lock.acquire(blocking=False):
            with self._lock:
                return self._value
        try:
            with self._lock:
                self._last_attempt_at = time.time()
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
        finally:
            self._refresh_lock.release()


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
        self._traveltimes = _Cached(self._fetch_travel_times, ttl_s=120.0)
        self._hwalerts = _Cached(self._fetch_highway_alerts, ttl_s=120.0)
        self._waze = _Cached(self._fetch_waze, ttl_s=120.0)
        self._image_cache: Dict[str, Tuple[float, bytes, str]] = {}
        self._image_cache_max = 150   # LRU-ish bound (~a few MB of JPEGs)
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
            # Pacific offset — NOT the server's local zone. (On a UTC host
            # the old time.mktime() read every dispatch ~7h in the future
            # and the "last hour" filter dropped them all → 911 ×0.)
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

    # -- WSDOT corridor travel times + highway alerts (same access code) ----

    @staticmethod
    def _wsdot_code() -> str:
        return os.environ.get("WSDOT_ACCESS_CODE", "").strip()

    def _fetch_travel_times(self) -> List[Dict[str, Any]]:
        code = self._wsdot_code()
        if not code:
            return []
        return parse_travel_times(
            _fetch_json(WSDOT_TRAVELTIMES_URL.format(code=code)))

    def travel_times(self) -> List[Dict[str, Any]]:
        """WSDOT corridor travel times (current vs average minutes).
        Empty when no WSDOT_ACCESS_CODE is configured."""
        if not self._wsdot_code():
            return []
        return self._traveltimes.get() or []

    def _fetch_highway_alerts(self) -> List[Dict[str, Any]]:
        code = self._wsdot_code()
        if not code:
            return []
        return parse_highway_alerts(
            _fetch_json(WSDOT_ALERTS_URL.format(code=code)),
            bbox=(47.0, 48.2, -123.0, -121.5))   # Puget Sound region

    def highway_alerts(self) -> List[Dict[str, Any]]:
        """WSDOT highway alerts (collisions, closures, construction) in the
        region. Empty when no WSDOT_ACCESS_CODE is configured."""
        if not self._wsdot_code():
            return []
        return self._hwalerts.get() or []

    # -- Waze for Cities (CCP) partner feed ---------------------------------

    def _fetch_waze(self) -> Dict[str, List[Dict[str, Any]]]:
        url = _waze_feed_url()
        if not url:
            return {"jams": [], "alerts": []}
        return parse_waze_feed(_fetch_json(url))

    def waze_jams(self) -> List[Dict[str, Any]]:
        """Crowdsourced Waze traffic jams (speed + polyline). Empty when
        NEXUS_WAZE_FEED_URL is not configured."""
        if not _waze_feed_url():
            return []
        return (self._waze.get() or {}).get("jams", [])

    def waze_alerts(self) -> List[Dict[str, Any]]:
        """Crowdsourced Waze incident reports (accidents, hazards). Empty
        when NEXUS_WAZE_FEED_URL is not configured."""
        if not _waze_feed_url():
            return []
        return (self._waze.get() or {}).get("alerts", [])

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
            # Bound the cache: evict oldest frames beyond the cap so a
            # citywide camera sweep can't grow memory without limit.
            if len(self._image_cache) > self._image_cache_max:
                oldest = sorted(self._image_cache.items(),
                                key=lambda kv: kv[1][0])
                for cam_id, _ in oldest[:len(self._image_cache)
                                        - self._image_cache_max]:
                    self._image_cache.pop(cam_id, None)
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
            "wsdot_traveltimes": (
                {"ok": True, "state": "disabled",
                 "error": None, "last_success_at": None}
                if not self._wsdot_code()
                else {
                    "ok": self._traveltimes.last_error is None,
                    "state": "enabled",
                    "error": self._traveltimes.last_error,
                    "last_success_at": self._traveltimes.last_success_at,
                }),
            "wsdot_highway_alerts": (
                {"ok": True, "state": "disabled",
                 "error": None, "last_success_at": None}
                if not self._wsdot_code()
                else {
                    "ok": self._hwalerts.last_error is None,
                    "state": "enabled",
                    "error": self._hwalerts.last_error,
                    "last_success_at": self._hwalerts.last_success_at,
                }),
            "waze": (
                {"ok": True, "state": "disabled",
                 "error": None, "last_success_at": None}
                if not _waze_feed_url()
                else {
                    "ok": self._waze.last_error is None,
                    "state": "enabled",
                    "error": self._waze.last_error,
                    "last_success_at": self._waze.last_success_at,
                }),
        }
