"""
Nexus City OS — Road geometry resolver.

Camera-derived topology connects intersections with straight lines, which
reads poorly as a traffic-flow map. This module resolves each segment's
ACTUAL road path using the public OSRM routing API
(router.project-osrm.org), with a persistent disk cache so the network is
hit at most once per segment, ever.

Design constraints:
  * Zero dependencies (urllib only).
  * Background fill thread, throttled (~6 req/s) to be a polite client of
    the public OSRM demo server.
  * Disk cache (JSON) — geometry is static, so subsequent boots are
    instant and offline-safe.
  * Failures are skipped silently (the UI falls back to a straight line);
    they are retried on the next process start.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

OSRM_URL = ("https://router.project-osrm.org/route/v1/driving/"
            "{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
            "?overview=full&geometries=geojson")
USER_AGENT = "NexusCityOS/1.0 (road geometry resolver; cached)"
THROTTLE_S = 0.17          # ~6 requests/second
SAVE_EVERY = 25            # persist cache every N new geometries

# Job tuple: (segment_id, lat1, lon1, lat2, lon2)
Job = Tuple[str, float, float, float, float]


class RoadGeometry:
    """Resolves and caches real road paths for graph segments."""

    def __init__(self, cache_path: str | Path) -> None:
        self._path = Path(cache_path)
        self._lock = threading.Lock()
        self._cache: Dict[str, List[List[float]]] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.fetched = 0
        self.failed = 0
        if self._path.exists():
            try:
                self._cache = json.loads(self._path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    # -- public API --------------------------------------------------------

    def path(self, segment_id: str) -> Optional[List[List[float]]]:
        """[[lat, lon], ...] along the real road, or None if unresolved."""
        with self._lock:
            return self._cache.get(segment_id)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"cached": len(self._cache),
                    "fetched_this_run": self.fetched,
                    "failed_this_run": self.failed}

    def start_fill(self, jobs: Sequence[Job]) -> None:
        """Resolve missing geometries in a background daemon thread."""
        with self._lock:
            pending = [j for j in jobs if j[0] not in self._cache]
        if not pending or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._fill, args=(pending,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- internals ---------------------------------------------------------

    def _fill(self, pending: Sequence[Job]) -> None:
        since_save = 0
        for seg_id, lat1, lon1, lat2, lon2 in pending:
            if self._stop.is_set():
                break
            geom = self._fetch(lat1, lon1, lat2, lon2)
            if geom is not None:
                with self._lock:
                    self._cache[seg_id] = geom
                    self.fetched += 1
                since_save += 1
                if since_save >= SAVE_EVERY:
                    self._save()
                    since_save = 0
            else:
                with self._lock:
                    self.failed += 1
            time.sleep(THROTTLE_S)
        self._save()

    def _fetch(self, lat1: float, lon1: float,
               lat2: float, lon2: float) -> Optional[List[List[float]]]:
        url = OSRM_URL.format(lon1=lon1, lat1=lat1, lon2=lon2, lat2=lat2)
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            routes = data.get("routes") or []
            coords = routes[0]["geometry"]["coordinates"]
            # GeoJSON is [lon, lat] — flip for Leaflet, round to ~1 m.
            return [[round(lat, 5), round(lon, 5)] for lon, lat in coords]
        except Exception:  # noqa: BLE001 — straight-line fallback is fine
            return None

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = json.dumps(self._cache, separators=(",", ":"))
            self._path.write_text(payload, "utf-8")
        except OSError:
            pass