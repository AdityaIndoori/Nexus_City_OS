"""
Nexus City OS — HTTP API + Operator UI server.

Standard-library HTTP server exposing the platform API and serving the
operator Live Grid UI. Zero external dependencies — runs anywhere.

Production hardening in this layer:
  * AUTH — every /api route (except /api/login) requires a signed bearer
    session token (see ``nexus.auth``). The acting principal is taken from
    the verified token, NEVER from the request body — clients cannot
    impersonate another user. Login failures and logouts are audit-logged.
  * PERSISTENCE — the runtime opens the durable Store; the audit chain,
    operating mode, users, incidents, and plans survive restarts.
  * REAL-TIME PUSH — /api/events is a Server-Sent-Events stream driven by
    the engine's event hub: state changes push to the TOC immediately
    instead of waiting on a polling interval.

Endpoints (JSON):
  POST /api/login                — {user_id, password} → {token, role}
  POST /api/logout               — revoke the current session
  GET  /api/status               — full platform status snapshot
  GET  /api/grid                 — city graph snapshot (map data)
  GET  /api/events               — SSE stream (state-change push)
  GET  /api/camera?id=&refresh=  — live camera frame proxy
  GET  /api/audit                — recent audit entries + chain verification
  GET  /api/cascade?id=INT-xxxx  — cascading dependency analysis
  POST /api/tick                 — advance the simulation one cycle
  POST /api/scenario             — inject an incident scenario
  POST /api/incident/ack         — {incident_id}
  POST /api/incident/resolve     — {incident_id, resolution, notes}
  POST /api/recommend            — {incident_id}
  POST /api/plan/approve         — {plan_id}
  POST /api/plan/reject          — {plan_id, reason}
  POST /api/plan/rollback        — {plan_id}
  GET  /api/plan/instruction?id= — Advisory Mode formatted instruction
  POST /api/mode                 — {mode}
  POST /api/copilot/query        — {text}

A background thread ticks the edge simulator + transit/weather polling so
the Live Grid is alive without manual /api/tick calls.
"""
from __future__ import annotations

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from . import bootstrap
from .adapters import CityAdapter, SeattleLiveAdapter, TacomaAdapter
from .analytics import Analytics
from .auth import AuthError, Authenticator
from .cfaccess import AccessError, CloudflareAccess
from .congestion import CongestionEstimator

from .roadgeom import RoadGeometry
from .copilot import InjectionBlocked, RateLimitExceeded
from .engine import NexusEngine, PermissionDenied
from .models import IncidentType, OperatingMode, Role, now_ts
from .security import (
    IPRateLimiter,
    MAX_BODY_BYTES,
    client_ip,
    security_headers,
    verify_turnstile,
)
from .store import Store
from .vision import VisionSweep


import os as _os

UI_PATH = Path(__file__).resolve().parent.parent / "ui" / "index.html"
LANDING_PATH = Path(__file__).resolve().parent.parent / "ui" / "landing.html"
LANDING_ASSETS = Path(__file__).resolve().parent.parent / "ui" / "landing-assets"
TICK_INTERVAL_S = 3.0
# DB path is env-overridable (NEXUS_DB_PATH) so a deployment can point at a
# mounted volume or force a fresh store without a code change.
DEFAULT_DB = _os.environ.get(
    "NEXUS_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "nexus.db"))


# Routes that do not require a session token.
PUBLIC_ROUTES = {"/", "/index.html", "/api/login", "/healthz",
                 "/landing", "/landing/"}

# Dedicated marketing hostname (e.g. nexuscity.aindoori.com) — when a
# request arrives with this Host, the LANDING PAGE is served at "/" and
# the operator console is NOT reachable on that hostname at all. The
# landing page's console links point at NEXUS_CONSOLE_URL (the
# Access-gated console hostname).
LANDING_HOST = _os.environ.get("NEXUS_LANDING_HOST", "").strip().lower()
CONSOLE_URL = _os.environ.get("NEXUS_CONSOLE_URL", "/").strip() or "/"

# Multi-city adapter registry (Phase 4 — City Adapter SDK).
CITY_ADAPTERS = {
    "seattle": (SeattleLiveAdapter, "Seattle, WA"),
    "tacoma": (TacomaAdapter, "Tacoma, WA"),
}


class PlatformRuntime:
    """Holds the engine + background data-polling loop.

    By default uses the REAL-DATA ``SeattleLiveAdapter`` (live SDOT/WSDOT
    cameras, real King County Metro positions, real NWS weather) and the
    durable SQLite store. Pass ``live=False`` for the fully offline
    deterministic adapter; ``db_path=":memory:"`` for ephemeral state.
    """

    def __init__(self, adapter: Optional[CityAdapter] = None,
                 live: bool = True,
                 db_path: str = DEFAULT_DB,
                 use_llm: bool = True,
                 enable_vision: bool = True,
                 city: str = "seattle") -> None:
        if adapter is None and live:
            adapter_cls = CITY_ADAPTERS.get(
                city, CITY_ADAPTERS["seattle"])[0]
            adapter = adapter_cls()
        self.store = Store(db_path)
        # Cloudflare Access (Zero Trust). When configured (team domain + AUD)
        # this becomes the ONLY sign-in path: identity comes from the signed
        # Access JWT verified at the origin; the in-app password login is
        # disabled and the demo accounts are not seeded.
        self.cfaccess = CloudflareAccess.from_env()
        if self.cfaccess.enabled:
            _os.environ.setdefault("NEXUS_DISABLE_DEMO_ACCOUNTS", "1")
        self.auth = Authenticator(self.store)

        self.engine, self.edge, self.adapter = bootstrap(
            adapter, self.store, use_llm=use_llm)
        # Ground the copilot chat in the live 911 feed (Citizen-style).
        live = getattr(self.adapter, "live", None)
        if live is not None:
            def _emergency_context() -> str:
                rows = live.emergencies(max_age_s=3600.0)[:12]
                if not rows:
                    return "911 (SFD live): no dispatches in the last hour\n"
                lines = "; ".join(
                    f"{r['type']} at {r['address']}" for r in rows[:8])
                return (f"911 (SFD live, last hour, {len(rows)} "
                        f"dispatches): {lines}\n")
            self.engine.copilot.extra_context_fn = _emergency_context

            # Freeze a detection-time frame for edge/911 incidents (the AI
            # vision sweep already attaches its own). Maps the platform
            # camera_id → live_id and pulls the current jpeg once.
            cam_map_for_capture = getattr(
                self.adapter, "live_camera_map", {}) or {}

            def _capture_frame(camera_id: str):
                meta = cam_map_for_capture.get(camera_id)
                if not meta:
                    return None
                result = live.camera_image(meta["live_id"])
                return result[0] if result else None
            self.engine.frame_capture_fn = _capture_frame

            # Resolve the live camera identity for a platform camera_id so the
            # incident card can name the EXACT camera the frozen frame came
            # from (co-located cameras share an intersection name).
            def _camera_meta(camera_id: str):
                return cam_map_for_capture.get(camera_id)
            self.engine.camera_meta_fn = _camera_meta


        # Resolve real road paths for traffic-flow rendering (OSRM, cached
        # on disk; straight-line fallback until each path resolves).
        geom_cache = Path(db_path).parent / "road_geometry.json" \
            if db_path != ":memory:" else \
            Path(__file__).resolve().parent.parent / "data" / "road_geometry.json"
        self.roadgeom = RoadGeometry(geom_cache)
        graph = self.engine.graph
        jobs = []
        for seg in graph.segments.values():
            a = graph.intersections.get(seg.from_intersection)
            b = graph.intersections.get(seg.to_intersection)
            if a and b:
                jobs.append((seg.id, a.lat, a.lon, b.lat, b.lon))
        self.roadgeom.start_fill(jobs)
        # Real congestion estimation from live bus GPS (+ optional WSDOT
        # flow when WSDOT_ACCESS_CODE is set) — Phase 1.
        self.congestion = CongestionEstimator(self.engine.graph)
        self._last_flow_at = 0.0
        self._last_911_at = 0.0
        # AI vision sweep over live camera frames — Phase 2. Constructed
        # always (so /api/vision/status works); STARTED only when enabled,
        # the topology is live, and the LLM client is available.
        self.vision = VisionSweep(self.engine, self.adapter)
        self.analytics = Analytics(self.store)
        # Public-edge abuse protection (defense in depth behind Cloudflare):
        # per-IP token-bucket rate limiting + Turnstile on login.
        self.ratelimit = IPRateLimiter()

        self.vision_enabled = bool(
            enable_vision
            and getattr(self.adapter, "using_live_topology", False)
            and self.engine.copilot.use_llm
            and self.engine.copilot.llm is not None)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start_background(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        if self.vision_enabled:
            self.vision.start()

    def stop(self) -> None:
        self._stop.set()
        self.vision.stop()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:   # noqa: BLE001 — keep the loop alive
                traceback.print_exc()
            self._stop.wait(TICK_INTERVAL_S)

    def tick(self) -> Dict[str, Any]:
        """One platform cycle: edge capture, transit/weather poll, rollback
        monitoring."""
        engine = self.engine
        emitted = self.edge.tick()
        for vehicle in self.adapter.poll_transit():
            try:
                engine.graph.update_vehicle(
                    vehicle.id, vehicle.lat, vehicle.lon,
                    vehicle.speed_mph, vehicle.last_update)
            except KeyError:
                engine.graph.add_vehicle(vehicle)
        # Prune vehicles that have left the area (no update in > 2 min).
        cutoff = now_ts() - 120.0
        stale_ids = [vid for vid, v in engine.graph.vehicles.items()
                     if v.last_update < cutoff]
        for vid in stale_ids:
            engine.graph.vehicles.pop(vid, None)
        engine.touch_feed("transit_gps")
        # REAL congestion: every moving bus is a speed probe. Optional
        # WSDOT flow data joins as high-weight samples every 60 s.
        live = getattr(self.adapter, "live", None)
        if getattr(self.adapter, "using_live_topology", False):
            self.congestion.ingest_vehicles(
                list(engine.graph.vehicles.values()))
            if live is not None and now_ts() - self._last_flow_at >= 60.0:
                self._last_flow_at = now_ts()
                try:
                    self.congestion.ingest_flow(live.flow_speeds())
                except Exception:  # noqa: BLE001 — degrade, never crash
                    pass
                # Waze crowdsourced jams (when the CCP partner feed is
                # configured) join as weight-2 probe samples.
                try:
                    self.congestion.ingest_waze_jams(live.waze_jams())
                except Exception:  # noqa: BLE001 — degrade, never crash
                    pass
            self.congestion.compute()
            self.congestion.apply(engine.graph)
            # M2 — auto-correlate traffic-impacting 911 dispatches to the
            # nearest camera intersection (every 60 s; idempotent).
            if live is not None and now_ts() - self._last_911_at >= 60.0:
                self._last_911_at = now_ts()
                try:
                    engine.correlate_911(live.emergencies(max_age_s=1800.0))
                except Exception:  # noqa: BLE001 — never break the tick
                    pass
        engine.real_congestion_ids = self.congestion.fresh_ids()
        engine.record_history()
        engine.graph.set_weather(self.adapter.poll_weather())
        engine.touch_feed("weather")
        engine.touch_feed("closures")
        engine.expire_advisories()   # PRD §5: 15-min advisory expiration
        proposals = engine.check_rollback_monitors()
        engine.emit_event("tick")   # push grid refresh to SSE clients
        return {"telemetry_emitted": len(emitted),
                "revert_proposals": proposals,
                "real_congestion_intersections":
                    len(engine.real_congestion_ids)}


def make_handler(runtime: PlatformRuntime):
    engine: NexusEngine = runtime.engine

    class Handler(BaseHTTPRequestHandler):
        server_version = "NexusCityOS/1.1"

        def handle(self) -> None:
            """Wrap the stdlib request loop to silence client-disconnect
            noise (normal for SSE streams when a console tab closes)."""
            try:
                super().handle()
            except (BrokenPipeError, ConnectionAbortedError,
                    ConnectionResetError):
                pass

        # ---- helpers ---------------------------------------------------

        def _emit_security_headers(self) -> None:
            for key, value in security_headers().items():
                self.send_header(key, value)

        def _send_json(self, payload: Any, code: int = 200) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._emit_security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str, code: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._emit_security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _client_ip(self) -> str:
            peer = self.client_address[0] if self.client_address else "?"
            return client_ip(self.headers, peer)

        def _rate_ok(self, login: bool = False) -> bool:
            """Per-IP token-bucket gate. On 429, writes the response (with
            Retry-After) and returns False so the caller aborts."""
            allowed, retry = runtime.ratelimit.check(
                self._client_ip(), login=login)
            if allowed:
                return True
            body = json.dumps(
                {"error": "Rate limit exceeded. Slow down."}).encode("utf-8")
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", str(int(retry) + 1))
            self.send_header("Cache-Control", "no-store")
            self._emit_security_headers()
            self.end_headers()
            self.wfile.write(body)
            return False

        class _BodyTooLarge(Exception):
            pass

        def _body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            if length > MAX_BODY_BYTES:
                # Drain a bounded amount so the socket stays sane, then 413.
                self.rfile.read(min(length, MAX_BODY_BYTES))
                raise Handler._BodyTooLarge(
                    f"Request body exceeds {MAX_BODY_BYTES} bytes.")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}


        def _cf_access_jwt(self) -> str:
            """Extract the Cloudflare Access assertion: the
            ``Cf-Access-Jwt-Assertion`` header (preferred) or the
            ``CF_Authorization`` cookie that Cloudflare sets on the origin."""
            jwt = self.headers.get("Cf-Access-Jwt-Assertion", "").strip()
            if jwt:
                return jwt
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "CF_Authorization":
                    return value.strip()
            return ""

        def _principal(self, parsed) -> Dict[str, Any]:
            """Resolve the acting principal.

            In **Cloudflare Access mode** identity comes solely from the
            signed Access JWT (verified against the team JWKS) — there is no
            bearer token and no in-app login. Otherwise fall back to the
            HMAC session token (Authorization header or ?token=)."""
            if runtime.cfaccess.enabled:
                try:
                    principal = runtime.cfaccess.verify(self._cf_access_jwt())
                except AccessError as exc:
                    raise AuthError(str(exc)) from None
                # Register the Access-authenticated identity in the engine's
                # RBAC table so privileged actions (ack/resolve/approve/mode)
                # recognize the email subject (fixes "Unknown user <email>").
                try:
                    engine.users[principal["sub"]] = Role(principal["role"])
                except (ValueError, KeyError):
                    pass
                return principal
            auth_header = self.headers.get("Authorization", "")
            token = ""
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
            if not token:
                token = parse_qs(parsed.query).get("token", [""])[0]
            if not token:
                raise AuthError("Authentication required.")
            return runtime.auth.verify_token(token)


        def log_message(self, fmt: str, *args: Any) -> None:
            pass  # quiet by default

        # ---- GET -------------------------------------------------------

        def _is_landing_host(self) -> bool:
            if not LANDING_HOST:
                return False
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host == LANDING_HOST

        def _serve_landing(self) -> None:
            html = LANDING_PATH.read_text(encoding="utf-8")
            # On the dedicated landing host, console links point at the
            # (Access-gated) console hostname; on the console origin's
            # /landing route they stay same-origin.
            html = html.replace(
                "__CONSOLE_URL__",
                CONSOLE_URL if self._is_landing_host() else "/")
            self._send_html(html)

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            parsed = urlparse(self.path)
            route = parsed.path
            try:
                # Dedicated marketing hostname: serve ONLY the landing page
                # and its assets — the console/API are not exposed here.
                if self._is_landing_host():
                    if route == "/healthz":
                        self._send_json({"ok": True, "page": "landing"})
                        return
                    if route.startswith("/landing-assets/"):
                        pass   # fall through to the shared asset handler
                    else:
                        self._serve_landing()
                        return
                if route == "/healthz":
                    # Lightweight unauthenticated liveness probe for load
                    # balancers / uptime monitors — avoids rendering the
                    # full UI on every probe.
                    self._send_json({"ok": True,
                                     "mode": engine.mode.value,
                                     "city": engine.city_id})
                    return
                if route in ("/landing", "/landing/"):
                    # Public marketing page (no auth): explains the platform
                    # to prospective cities with real screenshots.
                    self._serve_landing()
                    return
                if route.startswith("/landing-assets/"):
                    # Static screenshot assets for the landing page.
                    name = route.rsplit("/", 1)[-1]
                    # Path-traversal guard: bare filename, png only.
                    if ("/" in name or "\\" in name or ".." in name
                            or not name.endswith(".png")):
                        self._send_json({"error": "not found"}, 404)
                        return
                    asset = LANDING_ASSETS / name
                    if not asset.is_file():
                        self._send_json({"error": "not found"}, 404)
                        return
                    payload = asset.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control",
                                     "public, max-age=3600")
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if route in ("/", "/index.html"):
                    html = UI_PATH.read_text(encoding="utf-8")
                    # Inject the Turnstile *site* key (public, safe to embed)
                    # so the login widget renders only when CAPTCHA is on.
                    import os as _os
                    html = html.replace(
                        "__TURNSTILE_SITE_KEY__",
                        _os.environ.get("TURNSTILE_SITE_KEY", ""))
                    # Demo credential pre-fill is OFF by default. It is only
                    # enabled when an operator explicitly opts in via
                    # NEXUS_DEMO_PREFILL=1 (handy for a local walkthrough) and
                    # never when the demo accounts are disabled. On a public
                    # deployment the login fields ship empty.
                    _prefill = (
                        _os.environ.get("NEXUS_DEMO_PREFILL", "0")
                        in ("1", "true", "True", "yes")
                        and _os.environ.get("NEXUS_DISABLE_DEMO_ACCOUNTS", "0")
                        in ("0", "false", "False", ""))
                    html = html.replace(
                        "__DEMO_PREFILL__", "1" if _prefill else "")
                    # Cloudflare Access mode: tell the UI to skip its own
                    # login overlay entirely (identity is the Access JWT) and
                    # where to send the "sign out" link.
                    cfa = runtime.cfaccess
                    html = html.replace(
                        "__CF_ACCESS__", "1" if cfa.enabled else "")
                    html = html.replace(
                        "__CF_ACCESS_LOGOUT__",
                        cfa.logout_url if cfa.enabled else "")
                    self._send_html(html)

                    return


                # Per-IP rate gate on API GETs (SSE is exempt: it's a single
                # long-lived stream, not a flood vector, and is auth-gated).
                if route != "/api/events" and not self._rate_ok():
                    return
                # All other GET API routes require a valid session.
                principal = self._principal(parsed)

                if route == "/api/status":
                    self._send_json(engine.status())
                elif route == "/api/events":
                    self._sse_stream()
                elif route == "/api/grid":
                    snapshot = engine.graph.snapshot()
                    # Attach real road geometry where resolved (else the UI
                    # draws a straight line as fallback).
                    for seg in snapshot["segments"]:
                        p = runtime.roadgeom.path(seg["id"])
                        if p:
                            seg["path"] = p
                    snapshot["road_geometry"] = runtime.roadgeom.stats()
                    cam_map = getattr(runtime.adapter, "live_camera_map", {})
                    if cam_map:
                        for cam in snapshot["cameras"]:
                            meta = cam_map.get(cam["id"])
                            if meta:
                                cam["live"] = True
                                cam["live_name"] = meta["name"]
                                cam["live_type"] = meta["type"]
                    snapshot["live_data"] = bool(getattr(
                        runtime.adapter, "using_live_topology", False))
                    # Per-intersection congestion provenance + confidence
                    # (M1): tell the operator HOW each estimate was derived
                    # (live bus probe / loop+probe) and how confident it is.
                    real_ids = engine.real_congestion_ids
                    for inter in snapshot["intersections"]:
                        if inter["id"] in real_ids:
                            m = runtime.congestion.meta(inter["id"])
                            if m:
                                inter["cong_source"] = m["kind"]
                                inter["cong_confidence"] = m["confidence"]
                            else:
                                inter["cong_source"] = "live"
                        else:
                            inter["cong_source"] = "simulated"
                    real_n = len(real_ids)
                    if real_n > 0:
                        src = f"live (bus GPS×{real_n}"
                        if runtime.congestion.flow_active:
                            src += " + WSDOT flow"
                        src += ")"
                    else:
                        src = "simulated"
                    snapshot["congestion_source"] = src
                    snapshot["city"] = runtime.adapter.city_id
                    self._send_json(snapshot)
                elif route == "/api/camera":
                    params = parse_qs(parsed.query)
                    cam_id = params.get("id", [""])[0]
                    force = params.get("refresh", ["0"])[0] == "1"
                    cam_map = getattr(runtime.adapter, "live_camera_map", {})
                    meta = cam_map.get(cam_id)
                    live = getattr(runtime.adapter, "live", None)
                    if meta is None or live is None:
                        self._send_json(
                            {"error": "no live feed for this camera"}, 404)
                        return
                    result = live.camera_image(meta["live_id"],
                                               force_refresh=force)
                    if result is None:
                        self._send_json(
                            {"error": "camera image unavailable"}, 503)
                        return
                    payload, content_type, fetched_at = result
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Captured-At", f"{fetched_at:.3f}")
                    self.end_headers()
                    self.wfile.write(payload)
                elif route == "/api/cities":
                    self._send_json({
                        "current": runtime.adapter.city_id,
                        "available": [
                            {"id": cid, "name": name,
                             "launch": f"python platform/run.py "
                                       f"--city {cid}"}
                            for cid, (_cls, name) in CITY_ADAPTERS.items()],
                    })
                elif route == "/api/analytics":
                    params = parse_qs(parsed.query)
                    hours = float(params.get("hours", ["24"])[0])

                    def _name(iid: str) -> str:
                        inter = engine.graph.intersections.get(iid)
                        return inter.name if inter else iid
                    self._send_json(runtime.analytics.summary(
                        hours=hours, name_lookup=_name))
                elif route == "/api/vision/status":
                    stats = runtime.vision.stats()
                    stats["enabled"] = runtime.vision_enabled
                    self._send_json(stats)
                elif route == "/api/livehealth":
                    live = getattr(runtime.adapter, "live", None)
                    self._send_json(live.health() if live else
                                    {"live_data": False})
                elif route == "/api/emergencies":
                    # Citizen-style live emergency layer: SFD Real-Time 911
                    # dispatches + NWS hazard alerts.
                    live = getattr(runtime.adapter, "live", None)
                    if live is None:
                        self._send_json({"available": False,
                                         "emergencies": [], "hazards": []})
                        return
                    params = parse_qs(parsed.query)
                    age = float(params.get("age", ["3600"])[0])
                    rows = live.emergencies(max_age_s=age)
                    self._send_json({

                        "available": True,
                        "emergencies": rows,
                        "hazards": live.hazard_alerts(),
                        # WSDOT highway alerts (collisions/closures) and the
                        # Waze CCP feed (jams + crowdsourced reports). Empty
                        # lists when the feeds aren't configured.
                        "highway_alerts": live.highway_alerts(),
                        "waze_alerts": live.waze_alerts(),
                        "waze_jams": live.waze_jams(),
                        "source": "Seattle Fire Dept Real-Time 911 "
                                  "(data.seattle.gov) + NWS alerts",
                    })
                elif route == "/api/traveltimes":
                    # WSDOT corridor travel times (current vs typical).
                    live = getattr(runtime.adapter, "live", None)
                    rows = live.travel_times() if live else []
                    self._send_json({
                        "available": bool(rows),
                        "travel_times": rows[:40],
                        "source": "WSDOT Traveler Information API",
                    })

                elif route == "/api/audit":
                    self._send_json({
                        "entries": engine.audit.entries(limit=100),
                        "chain_intact": engine.audit.verify_chain_cached(),
                        "total": len(engine.audit),
                    })
                elif route == "/api/audit/export":
                    # Machine-readable JSONL export of the full hash-chained
                    # audit trail (PRD §11.3 legal-discovery requirement).
                    # Admin/analyst-gated: the export contains before/after
                    # state for every governance action.
                    if principal["role"] not in ("admin", "analyst"):
                        self._send_json(
                            {"error": "Audit export requires the admin or "
                                      "analyst role."}, 403)
                        return
                    payload = engine.audit.export_jsonl().encode("utf-8")
                    engine.audit.record(
                        actor=principal["sub"], action="audit_exported",
                        detail=f"{len(engine.audit)} entries exported")
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "application/x-ndjson; charset=utf-8")
                    self.send_header("Content-Disposition",
                                     'attachment; filename="nexus-audit.jsonl"')
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self._emit_security_headers()
                    self.end_headers()
                    self.wfile.write(payload)
                elif route == "/api/cascade":
                    params = parse_qs(parsed.query)
                    iid = params.get("id", [""])[0]
                    self._send_json({
                        "blocked": iid,
                        "impacts": engine.graph.cascading_impact(iid),
                    })
                elif route == "/api/plan/instruction":
                    params = parse_qs(parsed.query)
                    pid = params.get("id", [""])[0]
                    self._send_json(engine.advisory_instruction(pid))
                elif route == "/api/whoami":
                    self._send_json({"user_id": principal["sub"],
                                     "role": principal["role"],
                                     "expires_at": principal["exp"]})
                elif route == "/api/incidents":
                    # Filtered / sorted / paginated Incident Queue feed.
                    # Query params (all optional):
                    #   since, until : epoch seconds (absolute time range)
                    #   window       : seconds back from now (relative range;
                    #                  ignored if `since` is given)
                    #   types        : comma-separated IncidentType values
                    #   sources      : comma-separated detection_source values
                    #   active       : "1" to hide resolved/closed
                    #   order        : "asc" | "desc" (by detection time)
                    #   limit, offset: paging
                    params = parse_qs(parsed.query)

                    def _f(name):
                        v = params.get(name, [""])[0]
                        return float(v) if v not in ("", None) else None

                    since = _f("since")
                    until = _f("until")
                    window = _f("window")
                    if since is None and window:
                        import time as _t
                        since = _t.time() - window
                    types = [t for t in params.get(
                        "types", [""])[0].split(",") if t] or None
                    sources = [s for s in params.get(
                        "sources", [""])[0].split(",") if s] or None
                    active_only = params.get("active", ["0"])[0] == "1"
                    order = params.get("order", ["desc"])[0]
                    limit = int(float(params.get("limit", ["50"])[0]))
                    offset = int(float(params.get("offset", ["0"])[0]))
                    self._send_json(engine.query_incidents(
                        since=since, until=until, types=types,
                        sources=sources, include_resolved=not active_only,
                        order=order, limit=limit, offset=offset))
                elif route == "/api/incident/report":
                    # Per-incident evidence report (JSON download): the full
                    # incident record + its complete action history + every
                    # related hash-chained audit entry. For handoff to
                    # partner agencies / legal discovery.
                    params = parse_qs(parsed.query)
                    inc_id = params.get("id", [""])[0]
                    inc = engine.graph.incidents.get(inc_id)
                    if inc is None:
                        self._send_json(
                            {"error": f"Unknown incident {inc_id}"}, 404)
                        return
                    record = engine._incident_dict(inc)
                    record["action_history"] = list(inc.action_history)
                    related = [
                        e for e in engine.audit.entries(limit=1000)
                        if (e.get("after_state") or {}).get(
                            "incident_id") == inc.id
                        or (inc.intersection_id in (e.get("targets") or [])
                            and e.get("timestamp", 0)
                            >= inc.detected_at - 1)]
                    engine.audit.record(
                        actor=principal["sub"], action="incident_exported",
                        targets=[inc.intersection_id],
                        detail=f"incident report {inc.id} exported")
                    payload = json.dumps({
                        "generated_at": now_ts(),
                        "generated_by": principal["sub"],
                        "incident": record,
                        "audit_entries": related,
                        "audit_chain_intact":
                            engine.audit.verify_chain_cached(),
                    }, indent=2, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "application/json; charset=utf-8")
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="{inc.id}-report.json"')
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self._emit_security_headers()
                    self.end_headers()
                    self.wfile.write(payload)
                elif route == "/api/handover":
                    # Shift-handover report: everything the incoming operator
                    # needs — open incidents (with notes), pending plans, and
                    # governance actions in the window. Default 8h window.
                    params = parse_qs(parsed.query)
                    hrs = float(params.get("hours", ["8"])[0])
                    hrs = max(0.5, min(72.0, hrs))
                    since_ts = now_ts() - hrs * 3600.0
                    open_inc = [engine._incident_dict(i)
                                for i in engine.active_incidents()]
                    with engine._lock:
                        pending = [p.to_dict() for p in
                                   engine.plans.values()
                                   if p.status.value == "pending_approval"]
                        executed = [p.to_dict() for p in
                                    engine.plans.values()
                                    if p.status.value == "executed"]
                    resolved = engine.query_incidents(
                        since=since_ts, order="desc", limit=50)
                    resolved_rows = [
                        i for i in resolved["incidents"]
                        if i["state"] in ("resolved", "closed")]
                    audit_window = [
                        e for e in engine.audit.entries(limit=500)
                        if e.get("timestamp", 0) >= since_ts
                        and e.get("action") not in ("tick",)]
                    self._send_json({
                        "generated_at": now_ts(),
                        "generated_by": principal["sub"],
                        "window_hours": hrs,
                        "mode": engine.mode.value,
                        "open_incidents": open_inc,
                        "pending_plans": pending,
                        "executed_changes": executed,
                        "resolved_in_window": resolved_rows,
                        "audit_actions_in_window": len(audit_window),
                        "audit_recent": audit_window[-40:],
                        "audit_chain_intact":
                            engine.audit.verify_chain_cached(),
                    })
                elif route == "/api/incident/frame":
                    # Serve the FROZEN detection-time camera frame for an
                    # incident (never the latest live image). 404 when the
                    # incident has no captured frame.
                    params = parse_qs(parsed.query)
                    inc_id = params.get("id", [""])[0]
                    frame = engine.incident_frame(inc_id)
                    if not frame:
                        self._send_json(
                            {"error": "no detection-time frame for this "
                                      "incident"}, 404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(frame)))
                    # Frozen evidence is immutable — cache it hard.
                    self.send_header("Cache-Control",
                                     "private, max-age=86400, immutable")
                    self.end_headers()
                    self.wfile.write(frame)
                else:

                    self._send_json({"error": "not found"}, 404)
            except AuthError as exc:
                self._send_json({"error": str(exc)}, 401)
            except KeyError as exc:
                self._send_json({"error": str(exc)}, 404)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            except (BrokenPipeError, ConnectionAbortedError,
                    ConnectionResetError):
                pass   # client disconnected (e.g. SSE stream closed)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._send_json({"error": f"internal: {exc}"}, 500)

        def _sse_stream(self) -> None:
            """Server-Sent-Events: pushes a 'changed' event whenever the
            engine state advances. The client refreshes on each event."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last_seq = engine.event_seq
            # Initial hello so the client knows the stream is live.
            self.wfile.write(b"event: hello\ndata: 0\n\n")
            self.wfile.flush()
            while True:
                seq = engine.wait_for_event(last_seq, timeout=20.0)
                if seq == last_seq:
                    self.wfile.write(b": keepalive\n\n")   # comment frame
                else:
                    last_seq = seq
                    self.wfile.write(
                        f"data: {seq}\n\n".encode("ascii"))
                self.wfile.flush()

        # ---- POST ------------------------------------------------------

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path
            # Per-IP rate gate BEFORE reading the body (cheap DoS guard);
            # login uses the stricter bucket (credential-stuffing defence).
            if not self._rate_ok(login=(route == "/api/login")):
                return
            try:
                body = self._body()
            except Handler._BodyTooLarge as exc:
                self._send_json({"error": str(exc)}, 413)
                return
            try:
                if route == "/api/login":
                    # Cloudflare Access mode: the in-app password login is
                    # disabled — sign-in is handled entirely at Cloudflare's
                    # edge (Zero Trust), so there is no local credential path.
                    if runtime.cfaccess.enabled:
                        self._send_json(
                            {"error": "Sign-in is handled by Cloudflare "
                                      "Access."}, 403)
                        return
                    user_id = str(body.get("user_id", ""))
                    # CAPTCHA: Cloudflare Turnstile on the public login path.

                    # Disabled (allowed) when TURNSTILE_SECRET is unset.
                    if not verify_turnstile(
                            str(body.get("turnstile_token", "")),
                            remote_ip=self._client_ip()):
                        engine.audit.record(
                            actor=user_id or "unknown",
                            action="login_failed", outcome="denied",
                            detail="turnstile verification failed")
                        self._send_json(
                            {"error": "CAPTCHA verification failed."}, 403)
                        return
                    try:
                        session = runtime.auth.login(
                            user_id, str(body.get("password", "")))
                    except AuthError as exc:

                        engine.audit.record(
                            actor=user_id or "unknown",
                            action="login_failed", outcome="denied",
                            detail=str(exc))
                        raise
                    engine.audit.record(actor=user_id, action="login",
                                        detail=f"role={session['role']}")
                    self._send_json(session)
                    return

                # Everything else requires a verified session; the acting
                # user is the token subject — never the request body.
                principal = self._principal(parsed)
                user_id = principal["sub"]

                if route == "/api/logout":
                    auth_header = self.headers.get("Authorization", "")
                    if auth_header.startswith("Bearer "):
                        runtime.auth.revoke_token(auth_header[7:].strip())
                    engine.audit.record(actor=user_id, action="logout")
                    self._send_json({"ok": True})
                elif route == "/api/tick":
                    self._send_json(runtime.tick())
                elif route == "/api/scenario":
                    iid = str(body.get("intersection_id", ""))
                    anomaly = IncidentType(str(body.get(
                        "anomaly", "collision")))
                    if not engine.graph.has_intersection(iid):
                        raise KeyError(f"Unknown intersection {iid}")
                    runtime.edge.inject_scenario(iid, anomaly)
                    result = runtime.tick()
                    self._send_json({"injected": iid,
                                     "anomaly": anomaly.value,
                                     **result})
                elif route == "/api/incident/ack":
                    inc = engine.acknowledge_incident(
                        user_id, str(body.get("incident_id", "")))
                    self._send_json({"incident_id": inc.id,
                                     "state": inc.state.value})
                elif route == "/api/incident/notes":
                    # Auto-saved operator notes (debounced from the UI).
                    inc = engine.update_incident_notes(
                        user_id,
                        str(body.get("incident_id", "")),
                        str(body.get("notes", "")))
                    self._send_json({"incident_id": inc.id,
                                     "notes_len": len(inc.operator_notes),
                                     "saved_at": now_ts()})
                elif route == "/api/incident/contact":
                    # Field-operator dispatch contact (fire/police/ems/
                    # traffic crew) — audit-logged governance record.
                    inc = engine.record_field_contact(
                        user_id,
                        str(body.get("incident_id", "")),
                        str(body.get("service", "")),
                        str(body.get("note", "")))
                    self._send_json({"incident_id": inc.id,
                                     "logged_at": now_ts(),
                                     "service": str(body.get("service", ""))})
                elif route == "/api/incident/resolve":
                    inc = engine.resolve_incident(
                        user_id,
                        str(body.get("incident_id", "")),
                        str(body.get("resolution", "Resolved")),
                        str(body.get("notes", "")))
                    self._send_json({"incident_id": inc.id,
                                     "state": inc.state.value})
                elif route == "/api/recommend":
                    plan = engine.recommend(str(body.get("incident_id", "")))
                    result = plan.to_dict()
                    result["generator"] = engine.copilot.last_generator
                    self._send_json(result)
                elif route == "/api/incident/analyze":
                    # AI VISION: triage the live camera frame at an
                    # intersection (Claude Sonnet 4.6 multimodal).
                    iid = str(body.get("intersection_id", ""))
                    if not engine.graph.has_intersection(iid):
                        raise KeyError(f"Unknown intersection {iid}")
                    inter = engine.graph.get_intersection(iid)
                    cam_map = getattr(runtime.adapter, "live_camera_map", {})
                    live = getattr(runtime.adapter, "live", None)
                    cam_meta = next(
                        (m for m in cam_map.values()
                         if m["intersection_id"] == iid), None)
                    if cam_meta is None or live is None:
                        self._send_json(
                            {"error": "no live camera at this intersection"},
                            404)
                        return
                    frame = live.camera_image(cam_meta["live_id"],
                                              force_refresh=True)
                    if frame is None:
                        self._send_json(
                            {"error": "camera frame unavailable"}, 503)
                        return
                    analysis = engine.copilot.analyze_frame(
                        frame[0],
                        f"Camera at {inter.name}, Seattle. Platform "
                        f"congestion index {inter.congestion:.0%}.")
                    engine.audit.record(
                        actor=user_id, action="vision_analysis",
                        targets=[iid],
                        detail=str(analysis.get("assessment", ""))[:200],
                        outcome="ok" if analysis.get("available")
                        else "degraded")
                    analysis["intersection_id"] = iid
                    analysis["intersection_name"] = inter.name
                    self._send_json(analysis)
                elif route == "/api/plan/approve":
                    plan = engine.approve(user_id,
                                          str(body.get("plan_id", "")))
                    self._send_json(plan.to_dict())
                elif route == "/api/plan/reject":
                    plan = engine.reject(user_id,
                                         str(body.get("plan_id", "")),
                                         str(body.get("reason", "")))
                    self._send_json(plan.to_dict())
                elif route == "/api/plan/rollback":
                    plan = engine.rollback(user_id,
                                           str(body.get("plan_id", "")))
                    self._send_json(plan.to_dict())
                elif route == "/api/mode":
                    engine.set_mode(user_id,
                                    OperatingMode(str(body.get("mode", ""))))
                    self._send_json({"mode": engine.mode.value})
                elif route == "/api/threshold":
                    # Governed confidence-threshold adjustment (PRD §4.3).
                    # Admin-only; range enforced by the SafetyGate.
                    value = float(body.get("value", 0))
                    try:
                        engine.set_confidence_threshold(user_id, value)
                    except PermissionError as exc:
                        self._send_json({"error": str(exc)}, 403)
                        return
                    self._send_json(
                        {"confidence_threshold":
                         engine.safety.confidence_threshold})
                elif route == "/api/copilot/query":
                    result = engine.copilot.query(
                        user_id, str(body.get("text", "")))
                    self._send_json(result)
                else:
                    self._send_json({"error": "not found"}, 404)
            except AuthError as exc:
                self._send_json({"error": str(exc)}, 401)
            except PermissionDenied as exc:
                self._send_json({"error": str(exc)}, 403)
            except (InjectionBlocked, RateLimitExceeded) as exc:
                self._send_json({"error": str(exc)}, 429)
            except KeyError as exc:
                self._send_json({"error": str(exc)}, 404)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._send_json({"error": f"internal: {exc}"}, 500)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8757,
          background_ticks: bool = True, live: bool = True,
          db_path: str = DEFAULT_DB, city: str = "seattle",
          enable_vision: bool = True) -> None:
    runtime = PlatformRuntime(live=live, db_path=db_path, city=city,
                              enable_vision=enable_vision)
    if background_ticks:
        runtime.start_background()
    httpd = ThreadingHTTPServer((host, port), make_handler(runtime))
    print(f"Nexus City OS — {runtime.adapter.display_name}")
    using_live = getattr(runtime.adapter, "using_live_topology", False)
    print(f"Data: {'LIVE (regional cameras, OneBusAway transit, NWS)' if using_live else 'offline simulation'}")
    print(f"Store: {db_path} (audit chain: "
          f"{runtime.store.audit_count()} entries restored)")
    print(f"Mode: {runtime.engine.mode.value.upper()}")
    print(f"AI vision sweep: "
          f"{'enabled' if runtime.vision_enabled else 'disabled'}")
    print(f"Auth: session tokens required (demo accounts: op-1, admin-1, "
          f"analyst-1, viewer-1)")
    print(f"Operator UI:  http://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        runtime.stop()
        httpd.shutdown()


def build_arg_parser():
    """CLI for direct module launch and platform/run.py (Phase 5)."""
    import argparse
    import os
    # Container hosts (Railway, Fly, Cloud Run, Heroku…) inject the bind
    # port via $PORT; honor it so the same image deploys unchanged. $HOST
    # lets a platform override the bind address (default 0.0.0.0 when $PORT
    # is set, since cloud hosts always need all-interfaces binding).
    env_port = os.environ.get("PORT")
    default_host = os.environ.get(
        "HOST", "0.0.0.0" if env_port else "127.0.0.1")
    default_port = int(env_port) if env_port else 8757
    parser = argparse.ArgumentParser(
        prog="nexus-city-os",
        description="Nexus City OS - smart-city traffic decision "
                    "intelligence platform")

    parser.add_argument("--host", default=default_host,
                        help="bind address (default 127.0.0.1, or 0.0.0.0 "
                             "when $PORT is set / $HOST override)")
    parser.add_argument("--port", type=int, default=default_port,
                        help="HTTP port (default 8757, or $PORT if set)")

    parser.add_argument("--city", choices=sorted(CITY_ADAPTERS),
                        default="seattle",
                        help="city adapter to launch (default seattle)")
    parser.add_argument("--sim", action="store_true",
                        help="fully offline deterministic simulation "
                             "(no live feeds, no LLM)")
    parser.add_argument("--no-vision", action="store_true",
                        help="disable the background AI vision sweep")
    return parser


if __name__ == "__main__":
    _args = build_arg_parser().parse_args()
    serve(host=_args.host, port=_args.port, live=not _args.sim,
          city=_args.city, enable_vision=not _args.no_vision)
