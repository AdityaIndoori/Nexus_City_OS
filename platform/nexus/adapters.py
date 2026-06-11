"""
Nexus City OS — City Adapter SDK.

The extensibility pillar: every city plugs in via the ``CityAdapter``
abstract interface. The platform core never contains city-specific logic.

A new city deployment = implement one adapter class:
  * ``load_topology()``    — intersections, road segments, cameras.
  * ``poll_transit()``     — GTFS-RT-shaped vehicle positions.
  * ``poll_weather()``     — NWS-shaped weather observation.
  * ``poll_closures()``    — open-data closure/roadwork records.
  * ``controller_bridge()``— optional ATMS/NTCIP signal bridge (Live Mode).

``SeattleAdapter`` is the reference implementation. It runs in deterministic
simulation mode by default (offline demo — see MARKET_RESEARCH §5.5), shaped
exactly like the real feeds (GTFS-RT vehicle positions, SDOT closures, NWS
observations), so swapping in live endpoints changes only the polling
internals — not a single platform-core line.
"""
from __future__ import annotations

import math
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from .livedata import (
    DOWNTOWN_BBOX,
    SEATTLE_BBOX,
    SFD_911_URL,
    TACOMA_BBOX,
    SeattleLiveData,
)
from .models import (
    Camera,
    Intersection,
    RoadSegment,
    SignalPhase,
    SignalTimingPlan,
    TransitVehicle,
    WeatherCondition,
    now_ts,
)


class ControllerBridge(ABC):
    """Optional signal-controller integration (Appendix A). Only used in
    Live Mode; Shadow/Advisory deployments never construct one."""

    @abstractmethod
    def push_timing(self, intersection_id: str,
                    timing: Dict[str, Any]) -> bool:
        """Push a timing plan to the physical controller. True on ack."""


class CityAdapter(ABC):
    """Contract every city integration must fulfil."""

    city_id: str = "unknown"
    display_name: str = "Unknown City"

    @abstractmethod
    def load_topology(self) -> Dict[str, list]:
        """Return {'intersections': [...], 'segments': [...], 'cameras': [...]}"""

    @abstractmethod
    def poll_transit(self) -> List[TransitVehicle]:
        """Current transit vehicle positions (GTFS-RT shaped)."""

    @abstractmethod
    def poll_weather(self) -> WeatherCondition:
        """Current weather observation (NWS shaped)."""

    @abstractmethod
    def poll_closures(self) -> List[Dict[str, Any]]:
        """Active roadwork / street closures (open-data shaped)."""

    def controller_bridge(self) -> Optional[ControllerBridge]:
        """Return a controller bridge, or None (Shadow/Advisory only)."""
        return None


def default_timing_plan(intersection_id: str,
                        approach_speed_mph: float = 30.0) -> SignalTimingPlan:
    """A MUTCD-compliant default timing plan for a 2-phase intersection."""
    return SignalTimingPlan(
        plan_id=f"STP-{intersection_id}",
        intersection_id=intersection_id,
        cycle_seconds=90.0,
        phases=[
            SignalPhase(phase_id=1, movement="through", green_seconds=35.0,
                        yellow_seconds=4.0, red_clearance_seconds=2.0,
                        approach_speed_mph=approach_speed_mph,
                        conflicts_with=[2]),
            SignalPhase(phase_id=2, movement="through", green_seconds=35.0,
                        yellow_seconds=4.0, red_clearance_seconds=2.0,
                        approach_speed_mph=approach_speed_mph,
                        conflicts_with=[1]),
        ],
        pedestrian_walk_seconds=8.0,
        crosswalk_length_ft=60.0,
    )


class SeattleAdapter(CityAdapter):
    """Reference adapter: Downtown Seattle grid (city-managed SDOT
    intersections only — PRD jurisdictional scope).

    Real-feed mapping (when ``simulation=False`` endpoints are wired):
      * transit  → King County Metro / Sound Transit GTFS-RT (OneBusAway)
      * weather  → NWS API station KSEA
      * closures → SDOT open data portal
      * cameras  → SDOT camera registry (via data sharing agreement)
    """

    city_id = "seattle"
    display_name = "Seattle, WA — Downtown Grid"

    # Downtown grid: avenues (W→E) × streets (S→N)
    AVENUES = ["1st Ave", "2nd Ave", "3rd Ave", "4th Ave", "5th Ave", "6th Ave"]
    STREETS = ["Madison St", "Spring St", "Seneca St", "University St",
               "Union St", "Pike St", "Pine St"]
    BASE_LAT, BASE_LON = 47.6045, -122.3405
    LAT_STEP, LON_STEP = 0.0016, 0.0019
    ROUTES = ["RapidRide C", "RapidRide D", "RapidRide E", "Route 40",
              "Route 62", "Link 1 Line"]

    def __init__(self, seed: int = 42, monitored_ratio: float = 0.5) -> None:
        self._rng = random.Random(seed)
        self._monitored_ratio = monitored_ratio
        self._vehicle_state: List[TransitVehicle] = []

    # -- topology --------------------------------------------------------

    def load_topology(self) -> Dict[str, list]:
        intersections: List[Intersection] = []
        segments: List[RoadSegment] = []
        cameras: List[Camera] = []
        index: Dict[tuple, str] = {}

        n = 0
        for ai, ave in enumerate(self.AVENUES):
            for si, street in enumerate(self.STREETS):
                n += 1
                iid = f"INT-{n:04d}"
                lat = self.BASE_LAT + si * self.LAT_STEP
                lon = self.BASE_LON + ai * self.LON_STEP
                monitored = self._rng.random() < self._monitored_ratio
                inter = Intersection(
                    id=iid,
                    name=f"{ave} & {street}",
                    lat=lat, lon=lon,
                    monitored=monitored,
                    timing_plan=default_timing_plan(iid),
                    congestion=round(self._rng.uniform(0.15, 0.45), 3),
                    # 3rd Ave is the transit/EMS priority corridor downtown
                    ems_corridor=(ave == "3rd Ave"),
                )
                intersections.append(inter)
                index[(ai, si)] = iid
                if monitored:
                    cameras.append(Camera(
                        id=f"CAM-{n:04d}", intersection_id=iid,
                        lat=lat, lon=lon))

        s = 0
        for ai in range(len(self.AVENUES)):
            for si in range(len(self.STREETS)):
                if ai + 1 < len(self.AVENUES):
                    s += 1
                    segments.append(RoadSegment(
                        id=f"SEG-{s:04d}",
                        from_intersection=index[(ai, si)],
                        to_intersection=index[(ai + 1, si)],
                        name=f"{self.STREETS[si]} ({self.AVENUES[ai]}→"
                             f"{self.AVENUES[ai + 1]})",
                        speed_limit_mph=25.0,
                        current_speed_mph=self._rng.uniform(15.0, 25.0)))
                if si + 1 < len(self.STREETS):
                    s += 1
                    segments.append(RoadSegment(
                        id=f"SEG-{s:04d}",
                        from_intersection=index[(ai, si)],
                        to_intersection=index[(ai, si + 1)],
                        name=f"{self.AVENUES[ai]} ({self.STREETS[si]}→"
                             f"{self.STREETS[si + 1]})",
                        speed_limit_mph=25.0,
                        current_speed_mph=self._rng.uniform(15.0, 25.0)))

        return {"intersections": intersections, "segments": segments,
                "cameras": cameras}

    # -- live feeds (deterministic simulation shaped like the real APIs) --

    def poll_transit(self) -> List[TransitVehicle]:
        if not self._vehicle_state:
            for i in range(24):
                self._vehicle_state.append(TransitVehicle(
                    id=f"VEH-{i + 1:04d}",
                    route=self._rng.choice(self.ROUTES),
                    lat=self.BASE_LAT + self._rng.uniform(
                        0, self.LAT_STEP * (len(self.STREETS) - 1)),
                    lon=self.BASE_LON + self._rng.uniform(
                        0, self.LON_STEP * (len(self.AVENUES) - 1)),
                    speed_mph=self._rng.uniform(5.0, 22.0)))
        else:
            for v in self._vehicle_state:
                heading = self._rng.uniform(0, 2 * math.pi)
                v.lat += 0.00018 * math.sin(heading)
                v.lon += 0.00022 * math.cos(heading)
                v.speed_mph = max(0.0, min(
                    25.0, v.speed_mph + self._rng.uniform(-3.0, 3.0)))
                v.last_update = now_ts()
        return list(self._vehicle_state)

    def poll_weather(self) -> WeatherCondition:
        condition = self._rng.choices(
            ["clear", "rain", "fog"], weights=[0.55, 0.35, 0.10])[0]
        return WeatherCondition(
            condition=condition,
            temperature_f=round(self._rng.uniform(42.0, 58.0), 1),
            severe_alert=False,
            observed_at=now_ts())

    def poll_closures(self) -> List[Dict[str, Any]]:
        return [{
            "id": "CLOSURE-0001",
            "location": "5th Ave between Pike St and Pine St",
            "reason": "Utility work (scheduled)",
            "starts": now_ts() - 3600,
            "ends": now_ts() + 6 * 3600,
        }]

    def controller_bridge(self) -> Optional[ControllerBridge]:
        # Pending Appendix A investigation — Seattle starts Shadow/Advisory.
        return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters (good enough at city scale)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


class RegistryLiveAdapter(CityAdapter):
    """REAL-DATA adapter parametrized by region (City Adapter SDK base).

    Builds a live topology from the regional SDOT/WSDOT camera registry
    (web.seattle.gov/Travelers API — covers Puget Sound including Pierce
    County) filtered to a city bounding box, with:

    * Topology: real camera intersections at their true lat/lon; road
      segments connect each intersection to its nearest neighbors
      (≤ 3, within 1.5 km to bridge sparser areas).
    * Transit: real vehicle positions via OneBusAway (GTFS-RT-derived);
      any agency on the Puget Sound OBA instance (KC Metro=1, Pierce
      Transit=3). Speeds are estimated from consecutive position polls.
    * Weather: real NWS observation (configurable station).
    * Camera frames: real live JPEGs proxied by the platform server.

    Graceful degradation: if the live registry is unreachable at startup
    (or yields < ``min_live_cameras``), falls back to the deterministic
    ``SeattleAdapter`` topology — and the platform's freshness indicators
    surface any subsequent staleness (PRD §8: never hide failures).
    """

    city_id = "unknown"
    display_name = "Unknown City (LIVE data)"
    # Fall back to the offline topology below this camera count. Sparse
    # regions (e.g. Tacoma) override with a higher floor.
    min_live_cameras = 1

    def __init__(self, bbox: Optional[Tuple[float, float, float, float]]
                 = None,
                 oba_agency: str = "1",
                 nws_station: str = "KBFI",
                 socrata_911_url: Optional[str] = SFD_911_URL,
                 transit_label: str = "KC Metro") -> None:
        """``bbox`` limits coverage (lat_min, lat_max, lon_min, lon_max);
        default None = the entire default region."""
        self.bbox = bbox
        self.transit_label = transit_label
        self.live = SeattleLiveData(
            oba_agency=oba_agency, nws_station=nws_station,
            region_bbox=bbox, socrata_911_url=socrata_911_url)
        self._fallback = SeattleAdapter()
        self.using_live_topology = False
        # vehicle speed estimation: id -> (lat, lon, feed_ts, speed_mph)
        self._last_positions: Dict[str, Tuple[float, float, float, float]] = {}
        # camera_id (live registry) -> our CAM id; and intersection mapping
        self.live_camera_map: Dict[str, Dict[str, Any]] = {}

    # -- topology from the real camera registry ---------------------------

    def load_topology(self) -> Dict[str, list]:
        live_cams = self.live.cameras(bbox=self.bbox)
        if len(live_cams) < self.min_live_cameras:
            self.using_live_topology = False
            return self._fallback.load_topology()
        self.using_live_topology = True

        # Group cameras by (lat, lon) point — each point is an intersection.
        points: Dict[Tuple[float, float], List[Dict[str, Any]]] = {}
        for cam in live_cams:
            points.setdefault((cam["lat"], cam["lon"]), []).append(cam)

        intersections: List[Intersection] = []
        cameras: List[Camera] = []
        coords: List[Tuple[str, float, float]] = []
        n = 0
        for (lat, lon), cams in sorted(points.items()):
            n += 1
            iid = f"INT-{n:04d}"
            name = cams[0]["name"]
            intersections.append(Intersection(
                id=iid, name=name, lat=lat, lon=lon, monitored=True,
                timing_plan=default_timing_plan(iid),
                congestion=0.25,
                # 3rd Ave is Seattle's downtown transit/EMS priority corridor
                ems_corridor=("3rd Ave" in name),
            ))
            coords.append((iid, lat, lon))
            for k, cam in enumerate(cams):
                cam_id = f"CAM-{n:04d}" + (chr(ord('a') + k) if k else "")
                cameras.append(Camera(id=cam_id, intersection_id=iid,
                                      lat=lat, lon=lon))
                self.live_camera_map[cam_id] = {
                    "live_id": cam["id"], "name": cam["name"],
                    "type": cam["type"], "image_url": cam["image_url"],
                    "intersection_id": iid,
                }

        # Connect each intersection to its ≤3 nearest neighbors within
        # 1.5 km (camera density is sparser outside the downtown core).
        segments: List[RoadSegment] = []
        seen_pairs = set()
        s = 0
        for iid, lat, lon in coords:
            dists = sorted(
                ((_haversine_m(lat, lon, lat2, lon2), iid2)
                 for iid2, lat2, lon2 in coords if iid2 != iid))
            for dist, iid2 in dists[:3]:
                if dist > 1500.0:
                    break
                pair = tuple(sorted((iid, iid2)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                s += 1
                segments.append(RoadSegment(
                    id=f"SEG-{s:04d}",
                    from_intersection=pair[0], to_intersection=pair[1],
                    name=f"{pair[0]}↔{pair[1]}",
                    speed_limit_mph=25.0, current_speed_mph=20.0,
                    length_miles=round(dist / 1609.34, 3)))

        return {"intersections": intersections, "segments": segments,
                "cameras": cameras}

    # -- real transit positions -------------------------------------------

    def poll_transit(self) -> List[TransitVehicle]:
        lat_min, lat_max, lon_min, lon_max = self.bbox or SEATTLE_BBOX
        # Slightly wider box so vehicles entering the area are tracked.
        raw = self.live.vehicles(bbox=(lat_min - 0.02, lat_max + 0.02,
                                       lon_min - 0.02, lon_max + 0.02))
        if not raw:
            return self._fallback.poll_transit()
        now = time.time()
        out: List[TransitVehicle] = []
        for v in raw:
            # Speed is estimated between *feed updates*, not poll cycles:
            # the upstream cache can return the same position for several
            # ticks, which must not zero out the estimate.
            feed_ts = v["updated_at"] or now
            prev = self._last_positions.get(v["id"])
            if prev is None:
                speed_mph = 0.0
            else:
                plat, plon, pts, pspeed = prev
                moved = (plat != v["lat"] or plon != v["lon"])
                if moved and feed_ts > pts:
                    dist_m = _haversine_m(plat, plon, v["lat"], v["lon"])
                    speed_mph = min(60.0,
                                    (dist_m / (feed_ts - pts)) * 2.23694)
                elif moved:
                    # position changed but timestamp didn't advance —
                    # estimate over one poll interval
                    dist_m = _haversine_m(plat, plon, v["lat"], v["lon"])
                    speed_mph = min(60.0, (dist_m / 10.0) * 2.23694)
                else:
                    speed_mph = pspeed  # no new fix: keep last estimate
            self._last_positions[v["id"]] = (
                v["lat"], v["lon"], feed_ts, speed_mph)
            out.append(TransitVehicle(
                id=v["id"], route=self.transit_label,
                lat=v["lat"], lon=v["lon"],
                speed_mph=round(speed_mph, 1),
                last_update=feed_ts))
        return out

    # -- real weather -------------------------------------------------------

    def poll_weather(self) -> WeatherCondition:
        w = self.live.weather()
        if w is None:
            return self._fallback.poll_weather()
        return WeatherCondition(
            condition=w["condition"],
            temperature_f=w["temperature_f"],
            severe_alert=False,
            observed_at=now_ts())

    def poll_closures(self) -> List[Dict[str, Any]]:
        return self._fallback.poll_closures()

    def controller_bridge(self) -> Optional[ControllerBridge]:
        # Pending Appendix A investigation — deployments start
        # Shadow/Advisory; no physical controller bridge.
        return None


class SeattleLiveAdapter(RegistryLiveAdapter):
    """REAL-DATA Seattle adapter — CITYWIDE coverage.

    Thin subclass of ``RegistryLiveAdapter`` with Seattle parameters
    (kept as a named class for backward compatibility — the server and
    tests import it by name). King County Metro (OBA agency 1), NWS
    station KBFI, SFD Real-Time 911 feed.
    """

    city_id = "seattle"
    display_name = "Seattle, WA — Citywide (LIVE data)"

    def __init__(self, bbox: Optional[Tuple[float, float, float, float]]
                 = None) -> None:
        super().__init__(bbox=bbox, oba_agency="1", nws_station="KBFI",
                         socrata_911_url=SFD_911_URL,
                         transit_label="KC Metro")


class TacomaAdapter(RegistryLiveAdapter):
    """REAL-DATA Tacoma, WA adapter — multi-city SDK proof (Phase 4).

    Zero new API keys:
    * Cameras: WSDOT cameras from the same regional registry endpoint,
      filtered to ``TACOMA_BBOX`` (Pierce County coverage).
    * Transit: real Pierce Transit vehicle positions (OneBusAway agency
      "3" on the same Puget Sound API with the TEST key).
    * Weather: real NWS observation at KTIW (Tacoma Narrows Airport).
    * 911: Tacoma has no Socrata real-time fire feed — disabled
      (``emergencies()`` returns [], health shows "disabled").

    Falls back to the deterministic offline topology if the registry
    yields too few Tacoma cameras (graceful-degradation invariant).
    """

    city_id = "tacoma"
    display_name = "Tacoma, WA — Pierce Transit (LIVE data)"
    min_live_cameras = 5     # registry may be sparse outside Seattle

    def __init__(self, bbox: Optional[Tuple[float, float, float, float]]
                 = None) -> None:
        super().__init__(bbox=bbox or TACOMA_BBOX, oba_agency="3",
                         nws_station="KTIW", socrata_911_url=None,
                         transit_label="Pierce Transit")
