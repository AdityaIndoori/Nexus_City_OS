# Implementation Plan

[Overview]
Convert the last simulated pieces of Nexus City OS into real data (congestion, computer vision), add historical analytics, prove the multi-city adapter SDK with a TacomaAdapter + city switcher, harden for deployment, and ship operator UX wins — six phases executed in order.

The platform (`platform/`) is a zero-dependency Python 3.10+ stdlib reference implementation of a smart-city traffic decision-intelligence platform. It already runs on real Seattle data: ~420 SDOT/WSDOT camera intersections (`SeattleLiveAdapter` in `platform/nexus/adapters.py`), live KC Metro bus positions (OneBusAway), NWS weather, SFD Real-Time 911, OSRM road geometry (`platform/nexus/roadgeom.py`), with a production LLM layer (Sonnet 4.5 planner / Haiku 4.5 vision+chat in `platform/nexus/llm.py` + `copilot.py`), an independent SafetyGate, SQLite persistence (`store.py`), token auth (`auth.py`), SSE push, and a 76-test suite that runs with zero network access.

Two things remain simulated: **intersection congestion** (driven by `EdgeSimulator` random-walk telemetry in `edge.py` → `NexusEngine._on_edge_telemetry` in `engine.py` lines 199–209) and **incident detection** (only via injected scenarios). Phase 1 derives real congestion from live bus GPS speeds (200+ fixes/min already flowing through `PlatformRuntime.tick()`) plus optional WSDOT flow data. Phase 2 runs real Claude Haiku vision on live camera frames in a background sweep, feeding actual detections into the existing incident pipeline. Phase 3 records congestion/incident/plan history into SQLite and gives the Analyst role a dashboard. Phase 4 adds `TacomaAdapter` (Pierce Transit agency 3 on the same OneBusAway API, WSDOT cameras from the same registry endpoint pattern, NWS station KTIW — zero new API keys) and a `--city` launcher flag + UI awareness. Phase 5 adds Dockerfile, CI, and host/port CLI flags. Phase 6 adds browser notifications for traffic-impacting 911 calls, an incident timeline, and small UX polish.

Engineering invariants that must hold in every phase: stdlib only (no pip installs), all 76+ tests pass with zero network access, graceful degradation on every live feed (cache + fallback + health surfacing), the SafetyGate is never bypassed, and the audit chain records every new automated action.

[Types]
New dataclasses/enums are minimal: a `CongestionSample` tuple shape inside livedata, a `congestion_source` string on the grid snapshot, and history-row dicts; no changes to existing model dataclasses except one additive field.

- `platform/nexus/models.py`: add field `detection_source: str = "edge_simulator"` to `Incident` dataclass (additive, default keeps all existing constructors valid). Values: `"edge_simulator"`, `"ai_vision"`.
- Congestion sample (internal to `congestion.py`, plain tuple/dict, no dataclass needed): `{intersection_id: str, speed_mph: float, speed_limit_mph: float, at: float}`.
- History rows (returned by new `Store` methods as `List[Dict]`):
  - congestion history row: `{"intersection_id": str, "congestion": float, "at": float}`
  - incident history row: existing incident JSON + `"updated_at": float`
- Grid snapshot additions (in `server.py` `/api/grid` enrichment, not `graph.snapshot()`): `snapshot["congestion_source"]: str` — `"live (bus GPS×N + WSDOT)"` or `"simulated"`.
- `/api/analytics` response shape:
  ```json
  {
    "available": true,
    "window_hours": 24,
    "congestion_by_hour": [{"hour": "2026-06-10T21:00", "avg": 0.31, "max": 0.9, "samples": 412}],
    "hotspots": [{"intersection_id": "INT-0212", "name": "...", "avg": 0.61, "samples": 88}],
    "incident_counts": {"collision": 3, "congestion": 1},
    "plan_outcomes": {"approved": 4, "rejected": 1, "blocked": 0, "reverted": 1},
    "vision_sweep": {"frames_analyzed": 120, "incidents_confirmed": 2}
  }
  ```
- `/api/cities` response: `{"current": "seattle", "available": [{"id": "seattle", "name": "Seattle, WA"}, {"id": "tacoma", "name": "Tacoma, WA"}]}`.

[Files]
Three new platform modules, two deployment files, one CI workflow, one new test file per phase, and targeted edits to nine existing files.

New files:
- `platform/nexus/congestion.py` — Phase 1. `CongestionEstimator` class: derives per-intersection congestion from live bus GPS samples + optional WSDOT flow; pure-computation core (network-free, fully testable).
- `platform/nexus/vision.py` — Phase 2. `VisionSweep` class: background AI-vision sweep over live camera frames; emits redacted `EdgeTelemetry` onto the existing bus topic so the engine pipeline (privacy gate → congestion → incident dedupe) is reused unchanged.
- `platform/nexus/analytics.py` — Phase 3. `Analytics` class: pure aggregation over `Store` history tables (hourly congestion, hotspots, incident counts, plan outcomes).
- `platform/tests/test_congestion.py` — Phase 1 tests (≥6 tests).
- `platform/tests/test_vision_sweep.py` — Phase 2 tests (≥5 tests, fake LLM client).
- `platform/tests/test_analytics.py` — Phase 3 tests (≥5 tests, `Store(":memory:")`).
- `platform/tests/test_tacoma.py` — Phase 4 tests (≥4 tests, offline topology fallback).
- `Dockerfile` — Phase 5 (python:3.12-slim, copies `platform/` + `models.json`, runs `python platform/run.py --host 0.0.0.0`).
- `docker-compose.yml` — Phase 5 (single service, volume for `platform/data/`).
- `.github/workflows/ci.yml` — Phase 5 (runs `python -m unittest discover -s platform/tests -t platform` on push, Python 3.10/3.12 matrix).

Modified files:
- `platform/nexus/livedata.py` — Phase 1: add `WSDOT_FLOW_URL` const + `flow_speeds()` method on `SeattleLiveData` (gated on `os.environ.get("WSDOT_ACCESS_CODE")`); health entry `"wsdot_flow"`. Phase 4: parameterize the class — add constructor params `oba_agency: str = "1"`, `nws_station: str = "KBFI"`, `region_bbox` default `SEATTLE_REGION_BBOX`, `socrata_911_url: Optional[str]` (None disables 911 feed); build `OBA_VEHICLES_URL`/`NWS_OBSERVATION_URL` from params. Add `TACOMA_BBOX = (47.18, 47.32, -122.58, -122.38)`.
- `platform/nexus/adapters.py` — Phase 1: no change. Phase 4: extract shared live-topology logic so `TacomaAdapter(CityAdapter)` reuses it; add class `TacomaAdapter` with `city_id="tacoma"`, `display_name="Tacoma, WA — Pierce Transit"`, OBA agency `"3"`, NWS `KTIW`, camera registry filtered to `TACOMA_BBOX`, `controller_bridge()` → None.
- `platform/nexus/engine.py` — Phase 1: in `_on_edge_telemetry`, only apply simulator-speed congestion when no real estimate is fresh (see Functions). Phase 2: accept incidents tagged `detection_source="ai_vision"`. Phase 3: call `store.add_congestion_samples()` from a new `record_history()` method.
- `platform/nexus/server.py` — Phase 1: wire `CongestionEstimator` into `PlatformRuntime.tick()`; add `congestion_source` to `/api/grid`. Phase 2: construct + start `VisionSweep`; new route `GET /api/vision/status`; runtime stats. Phase 3: new route `GET /api/analytics?hours=24` (Analyst/Admin/Operator allowed); history recording each tick. Phase 4: `PlatformRuntime(adapter=...)` already supported — add `serve(city: str = "seattle")` selection + `GET /api/cities`. Phase 5: `serve(host, port)` already parameterized — add argparse in `__main__` (`--host`, `--port`, `--sim`, `--city`, `--no-vision`).
- `platform/nexus/store.py` — Phase 3: two new tables + methods (see Classes).
- `platform/nexus/models.py` — Phase 2: `Incident.detection_source` field.
- `platform/run.py` — Phase 4/5: pass through argparse args to `serve()`.
- `platform/ui/index.html` — Phase 1: header `congestion_source` chip next to databadge. Phase 3: new "Analytics" panel — replace the bottom-left "AI Safety Metrics" panel with a tabbed panel (`Safety | Analytics` tabs; analytics tab renders hourly congestion sparkline-style bars, top-5 hotspots with `focusIntersection` click, plan outcomes). Phase 4: `cityname` element populated from `/api/cities` current; city list shown as a header dropdown when >1 available (switch = full reload with `?city=` is NOT needed — server runs one city; dropdown shows current city and the launch hint for others). Phase 6: browser `Notification` for new traffic-impacting 911 dispatches (permission requested on first toggle of a new 🔔 legend control); incident timeline (state-change history rendered inside the incident card from audit entries already returned by `/api/audit`).
- `README.md` — final phase: update tests count, new features, Docker/CI instructions, Tacoma launch instructions.

No files deleted.

[Functions]
New functions center on congestion estimation, the vision sweep loop, analytics aggregation, and runtime wiring; modifications are confined to the engine telemetry handler, runtime tick, and serve().

New functions/methods:
- `platform/nexus/congestion.py`:
  - `CongestionEstimator.__init__(self, graph: CityGraph, fresh_window_s: float = 180.0, min_samples: int = 2, radius_deg: float = 0.008)` — stores graph ref; `self._estimates: Dict[str, Tuple[float, float]]` (intersection_id → (congestion, at)).
  - `CongestionEstimator.ingest_vehicles(self, vehicles: List[TransitVehicle], now: Optional[float] = None) -> int` — for each vehicle, finds intersections within `radius_deg` (grid-bucketed index built lazily from `graph.intersections` for O(1) lookup; rebuild when intersection count changes), appends speed samples; returns sample count. Buses with `speed_mph <= 0.5` and `last_update` older than 120 s are skipped (dwelling at stops is filtered by requiring `min_samples` distinct vehicles).
  - `CongestionEstimator.ingest_flow(self, flows: List[Dict[str, Any]]) -> int` — optional WSDOT flow records `{lat, lon, speed_mph, limit_mph}` treated as high-weight samples (weight 3 vs bus weight 1).
  - `CongestionEstimator.compute(self, now: Optional[float] = None) -> Dict[str, float]` — per intersection with ≥ `min_samples` weighted samples in window: `congestion = clamp(1 - median_speed / speed_limit_estimate, 0, 1)` where `speed_limit_estimate = 25.0` for surface streets, `55.0` if the intersection name contains "I-5", "I-90", "I-405", "SR-" or "@" (WSDOT highway cams use "@" in names — e.g. "I-5 @ NE 195th St"). Stores into `self._estimates`; returns the dict.
  - `CongestionEstimator.fresh_ids(self, now: Optional[float] = None) -> set[str]` — intersections with an estimate newer than `fresh_window_s`.
  - `CongestionEstimator.apply(self, graph: CityGraph) -> int` — calls `graph.update_congestion(iid, value)` for each fresh estimate; returns count applied.
- `platform/nexus/vision.py`:
  - `VisionSweep.__init__(self, engine, adapter, interval_s: float = 120.0, per_sweep: int = 6, llm=None)` — `llm` defaults to `engine.copilot._llm`-equivalent access via a passed callable; **design: takes `analyze_fn: Callable[[bytes, str], Dict]` defaulting to `engine.copilot.analyze_frame`** to stay decoupled and trivially fakeable in tests.
  - `VisionSweep.start(self) -> None` / `VisionSweep.stop(self) -> None` — daemon thread; loop: every `interval_s`, pick `per_sweep` cameras (priority: intersections with congestion > 0.45 first, then round-robin through `adapter.live_camera_map`), fetch frame via `adapter.live.camera_image(live_id)`, call `analyze_fn(frame_bytes, context)`, and if `incident_visible` is true with `confidence_pct >= 70`, publish a redacted `EdgeTelemetry` (anomaly=`"collision"` mapping from assessment keywords: "collision"/"crash"→collision, "stalled"/"stopped"→stopped_vehicle, else congestion) onto `engine.telemetry_topic` via `engine.bus.publish`. Always also derive a congestion reading from `congestion_visible` ("high"→0.8, "moderate"→0.5, "low"→0.2) and update the camera's intersection via the same telemetry message (set `avg_speed_mph` consistent with the engine's formula: `speed = 25*(1-c)/0.9` inverse not required — simpler: set `avg_speed_mph = 25.0*(1.0-cong)` and `stopped_vehicles = int(cong*5)`).
  - `VisionSweep.stats(self) -> Dict[str, Any]` — `{"running": bool, "frames_analyzed": int, "incidents_raised": int, "last_sweep_at": float, "degraded": bool}`.
  - All failures (LLM down, camera 404) increment a `degraded` counter and never raise out of the loop.
- `platform/nexus/analytics.py`:
  - `Analytics.__init__(self, store: Store)`.
  - `Analytics.summary(self, hours: float = 24.0) -> Dict[str, Any]` — builds the `/api/analytics` response shape above with stdlib `statistics`; resolves intersection names via an optional `name_lookup: Callable[[str], str]` param.
- `platform/nexus/livedata.py`:
  - `SeattleLiveData.flow_speeds(self) -> List[Dict[str, Any]]` — `WSDOT_FLOW_URL = "https://wsdot.wa.gov/Traffic/api/TrafficFlow/TrafficFlowREST.svc/GetTrafficFlowsAsJson?AccessCode={code}"`; maps WSDOT `FlowReadingValue` 1–4 to speeds (1=free→55, 2=moderate→40, 3=heavy→25, 4=stop&go→10) at `FlowStationLocation.Latitude/Longitude`; TTL cache 60 s, stale fallback, `[]` when no access code; health key `wsdot_flow` with `"disabled"` state when keyless.
- `platform/nexus/store.py`:
  - `Store.add_congestion_samples(self, rows: List[Tuple[str, float, float]]) -> None` — executemany insert `(intersection_id, congestion, at)`.
  - `Store.congestion_history(self, since: float) -> List[Dict]`.
  - `Store.prune_history(self, before: float) -> int` — delete rows older than `before` (called once/hour from tick; keep 7 days).
- `platform/nexus/engine.py`:
  - `NexusEngine.record_history(self) -> None` — if `self.store`: sample every monitored intersection's congestion into `add_congestion_samples` (throttled internally to once per 60 s via `self._last_history_at`).
- `platform/nexus/server.py`:
  - `PlatformRuntime.__init__` — new params `city: str = "seattle"`, `enable_vision: bool = True`; constructs adapter by city id (`{"seattle": SeattleLiveAdapter, "tacoma": TacomaAdapter}`); constructs `self.congestion = CongestionEstimator(self.engine.graph)`, `self.vision = VisionSweep(...)` (started only when `enable_vision` and the adapter is live and LLM available).
  - `serve(host="127.0.0.1", port=8757, background_ticks=True, live=True, db_path=DEFAULT_DB, city="seattle", enable_vision=True)`.
  - `__main__` argparse: `--host`, `--port`, `--sim`, `--city {seattle,tacoma}`, `--no-vision`.

Modified functions:
- `NexusEngine._on_edge_telemetry` (`platform/nexus/engine.py` lines ~199–209): wrap the congestion write in a guard — add attribute `self.real_congestion_ids: set = set()` (updated by the runtime each tick); only apply the simulator-derived congestion when `telemetry.intersection_id not in self.real_congestion_ids` **or** `telemetry.anomaly` is set (anomalies always drive congestion so injected scenarios still work everywhere). This preserves all existing tests (they don't populate `real_congestion_ids`).
- `PlatformRuntime.tick` (`platform/nexus/server.py`): after the vehicle-update loop, call `self.congestion.ingest_vehicles(list(engine.graph.vehicles.values()))`; every 60 s also `ingest_flow(live.flow_speeds())` when available; then `estimates = self.congestion.compute()`, `self.congestion.apply(engine.graph)`, `engine.real_congestion_ids = self.congestion.fresh_ids()`; then `engine.record_history()`.
- `/api/grid` handler: add `snapshot["congestion_source"]` (`"live (bus GPS"+optional "+WSDOT flow")"` when `len(real_congestion_ids)>0` else `"simulated"`), and `snapshot["city"] = runtime.adapter.city_id`.
- `Incident` creation in `_raise_incident`: set `detection_source` from a new optional key in telemetry message (`message.get("source", "edge_simulator")` — `EdgeTelemetry` gets an optional `source: str = "edge_simulator"` field; `VisionSweep` publishes with `source="ai_vision"`).
- UI `renderHeader` (`platform/ui/index.html`): show congestion source chip; populate `#cityname` from `grid.city`.
- UI `renderIncidents`: show a small `👁 AI-detected` chip when `inc.detection_source === "ai_vision"`; incident timeline `<details>` listing audit entries filtered client-side from the `/api/audit` data already fetched (entries whose `targets` include the incident's intersection or whose detail mentions the incident id — keep simple: filter on `incident_id` in `after_state`).

Removed functions: none.

[Classes]
Four new classes (`CongestionEstimator`, `VisionSweep`, `Analytics`, `TacomaAdapter`); modifications to `Store`, `SeattleLiveData`, `PlatformRuntime`, `NexusEngine`; no removals.

- `CongestionEstimator` (`platform/nexus/congestion.py`) — pure computation, no I/O, no threads. Fully unit-testable with synthetic `TransitVehicle` lists against an offline `SeattleAdapter` graph.
- `VisionSweep` (`platform/nexus/vision.py`) — owns one daemon thread; depends only on `engine.bus`, `engine.telemetry_topic`, `engine.graph`, `adapter.live`/`live_camera_map`, and an injectable `analyze_fn` + `frame_fn` (default `adapter.live.camera_image`); tests inject fakes for both and tick the loop body directly via a public `sweep_once()` method (the thread calls `sweep_once` in a loop — design it so tests never need the thread).
- `Analytics` (`platform/nexus/analytics.py`) — stateless aggregator over `Store`.
- `TacomaAdapter` (`platform/nexus/adapters.py`) — subclasses the same base as `SeattleLiveAdapter`; refactor: extract `SeattleLiveAdapter`'s registry-driven topology build into a shared `_LiveTopologyMixin` or parametrized base class `RegistryLiveAdapter(CityAdapter)` with constructor params `(bbox, oba_agency, nws_station, socrata_911_url, city_id, display_name)`; `SeattleLiveAdapter` becomes `RegistryLiveAdapter` with Seattle params (keep the public class name `SeattleLiveAdapter` as a thin subclass for backward compatibility — tests and server import it by name). Tacoma 911: Tacoma has no Socrata SFD feed — pass `socrata_911_url=None`; `emergencies()` returns `[]` and health shows `"disabled"`. The camera registry endpoint (`web.seattle.gov/Travelers`) covers WSDOT cameras across the region including Pierce County at zoom 14 — filter by `TACOMA_BBOX`; if the registry yields < 5 Tacoma cameras at runtime, the adapter falls back to its deterministic offline topology exactly like the Seattle one does (graceful degradation invariant).
- `Store` (`platform/nexus/store.py`) — add to `_SCHEMA`:
  ```sql
  CREATE TABLE IF NOT EXISTS congestion_history (
    intersection_id TEXT NOT NULL, congestion REAL NOT NULL, at REAL NOT NULL);
  CREATE INDEX IF NOT EXISTS idx_ch_at ON congestion_history(at);
  ```
  (incidents/plans history already exists via the `incidents` and `plans` tables with `updated_at` — Analytics reads those, no new tables needed for them.)
- `NexusEngine` — new attributes `real_congestion_ids: set`, `_last_history_at: float`; new method `record_history()`.
- `PlatformRuntime` — new attributes `congestion`, `vision`; city-keyed adapter construction.

[Dependencies]
Zero new package dependencies; two optional environment variables.

- No pip installs anywhere — stdlib only (`statistics`, `bisect`, `os`, existing `urllib`, `sqlite3`, `threading`).
- Optional env vars: `WSDOT_ACCESS_CODE` (enables WSDOT flow ingestion; absent = bus-GPS-only, health chip shows `wsdot_flow: disabled`), existing LLM gateway config unchanged (vision sweep auto-disables when the LLM client is unavailable, surfaced in `/api/vision/status`).
- Docker base image `python:3.12-slim`; CI uses `actions/setup-python@v5` matrix `["3.10", "3.12"]`.

[Testing]
Each phase adds a network-free test file using the established patterns (`bootstrap(SeattleAdapter(seed=42))`, `Store(":memory:")`, injected fakes), keeping the suite green at every step.

- `test_congestion.py`: estimator computes ~0 congestion for buses at limit speed; high congestion for crawling buses; ignores single-sample intersections (`min_samples`); freshness window expiry (`fresh_ids` empties); `apply()` writes through to `graph.update_congestion`; highway speed-limit heuristic ("I-5 @ X" → 55 mph baseline); engine guard test: when `real_congestion_ids` contains an intersection, simulator telemetry without anomaly does NOT overwrite congestion, but anomalous telemetry DOES.
- `test_vision_sweep.py`: with fake `analyze_fn` returning `incident_visible=True, confidence_pct=90, assessment="collision visible"` and fake `frame_fn` returning bytes — `sweep_once()` publishes telemetry that raises an incident with `detection_source="ai_vision"`; low-confidence result raises nothing; LLM failure marks degraded without exception; congestion mapping ("high"→0.8) applied; camera prioritization picks congested intersections first.
- `test_analytics.py`: seed `Store(":memory:")` with congestion samples across 3 hours + incidents/plans; `summary()` returns correct hourly buckets, hotspot ordering, plan outcome counts; pruning deletes old rows; empty store returns `available: true` with empty arrays (no crash).
- `test_tacoma.py`: `TacomaAdapter` offline fallback topology loads (network-independent — constructor must not require network, mirroring how `test_livedata.py` handles `SeattleLiveAdapter`); `city_id == "tacoma"`; OBA agency/NWS station params plumbed; `RegistryLiveAdapter` shared logic: Seattle subclass still produces identical topology (regression guard via existing `test_livedata.py` which must keep passing unchanged).
- Phase 5 validated by `docker build` succeeding locally (manual verification step) and CI workflow YAML lint (push not required).
- Phase 6 validated in-browser (Puppeteer): notification toggle renders, timeline expands, 911 chip behavior.
- Full regression after every phase: `python -m unittest discover -s platform/tests -t platform` — must stay green (76 + new tests).

[Implementation Order]
Six phases, each independently shippable, each ending with a green test suite and a live browser verification.

1. **Phase 1 — Real congestion.** (a) `congestion.py` + `test_congestion.py`; (b) `EdgeTelemetry.source` field + engine guard (`real_congestion_ids`) in `engine.py`; (c) `flow_speeds()` in `livedata.py`; (d) wire into `PlatformRuntime.tick()` + `/api/grid` `congestion_source`; (e) UI header chip; (f) run suite, restart server, verify in browser that flow-line colors now move with real bus speeds and the header shows "live (bus GPS…)".
2. **Phase 2 — Real CV sweep.** (a) `Incident.detection_source` + telemetry `source` plumbing; (b) `vision.py` + `test_vision_sweep.py` (injectable `analyze_fn`/`frame_fn`, public `sweep_once()`); (c) runtime wiring + `/api/vision/status` + `--no-vision` flag; (d) UI `👁 AI-detected` chip + vision stats line in safety panel; (e) suite + live verification (watch a sweep tick in the audit trail).
3. **Phase 3 — Analytics.** (a) `Store` schema + history methods + `prune_history`; (b) `engine.record_history()` + tick wiring; (c) `analytics.py` + `test_analytics.py`; (d) `GET /api/analytics`; (e) UI tabbed Safety|Analytics panel (hour bars, clickable hotspots, plan outcomes); (f) suite + live verification after letting history accumulate a few minutes.
4. **Phase 4 — Tacoma + SDK proof.** (a) refactor `SeattleLiveAdapter` → parametrized `RegistryLiveAdapter` (keep `SeattleLiveAdapter` name as subclass; `test_livedata.py` must pass untouched); (b) parameterize `SeattleLiveData` (agency/station/bbox/911-url ctor params); (c) `TacomaAdapter` + `TACOMA_BBOX` + `test_tacoma.py`; (d) `--city` flag, `/api/cities`, UI `cityname` from grid + header city indicator; (e) suite + launch `--city tacoma` and verify real Pierce Transit buses/cameras in browser.
5. **Phase 5 — Deployment.** (a) argparse in `server.py` `__main__` + `run.py` passthrough (`--host/--port/--city/--sim/--no-vision`); (b) `Dockerfile` + `docker-compose.yml`; (c) `.github/workflows/ci.yml`; (d) verify `docker build` + suite.
6. **Phase 6 — UX wins.** (a) browser notifications for new traffic-impacting 911 dispatches (🔔 legend toggle, `Notification.requestPermission`, dedupe by dispatch id); (b) incident timeline (`<details>` of audit entries for that incident); (c) README full update (test counts, features, Docker, Tacoma, analytics); (d) final suite + full browser walkthrough.