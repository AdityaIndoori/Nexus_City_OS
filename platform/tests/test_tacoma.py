"""
Phase 4 tests — TacomaAdapter and the RegistryLiveAdapter SDK refactor.
Network-independent: constructors never require network; topology falls
back to the deterministic offline grid when the registry is unreachable
(mirroring how test_livedata.py exercises SeattleLiveAdapter).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus import bootstrap
from nexus.adapters import (
    RegistryLiveAdapter,
    SeattleLiveAdapter,
    TacomaAdapter,
)
from nexus.livedata import SFD_911_URL, TACOMA_BBOX, SeattleLiveData


class _NoNetwork:
    """Patch the registry cache so cameras() returns [] without network."""

    def __init__(self, adapter):
        self.adapter = adapter

    def __enter__(self):
        live = self.adapter.live
        live._registry._value = []
        live._registry._fetched_at = float("inf")
        live._vehicles._value = []
        live._vehicles._fetched_at = float("inf")
        live._weather._value = None        # poll_weather → fallback
        live._weather._fetch = lambda: None
        live._weather._ttl = float("inf")
        return self.adapter

    def __exit__(self, *exc):
        return False


class TestTacomaAdapter(unittest.TestCase):
    def test_identity_and_params(self):
        a = TacomaAdapter()
        self.assertEqual(a.city_id, "tacoma")
        self.assertIn("Tacoma", a.display_name)
        self.assertEqual(a.bbox, TACOMA_BBOX)
        self.assertEqual(a.live.oba_agency, "3")          # Pierce Transit
        self.assertEqual(a.live.nws_station, "KTIW")
        self.assertIsNone(a.live.socrata_911_url)         # no Tacoma feed
        self.assertEqual(a.transit_label, "Pierce Transit")

    def test_offline_fallback_topology(self):
        with _NoNetwork(TacomaAdapter()) as a:
            topo = a.load_topology()
            self.assertFalse(a.using_live_topology)
            self.assertGreater(len(topo["intersections"]), 0)
            self.assertGreater(len(topo["segments"]), 0)
            # Platform bootstraps fine on the fallback topology.
            engine, edge, adapter = bootstrap(a)
            self.assertEqual(engine.city_id, "tacoma")
            self.assertGreater(len(engine.graph.intersections), 0)

    def test_911_disabled_gracefully(self):
        a = TacomaAdapter()
        self.assertEqual(a.live.emergencies(), [])
        health = a.live.health()
        self.assertEqual(health["sfd_911"]["state"], "disabled")
        self.assertTrue(health["sfd_911"]["ok"])

    def test_sparse_registry_falls_back(self):
        # < min_live_cameras Tacoma cameras → deterministic fallback.
        a = TacomaAdapter()
        a.live._registry._value = [
            {"id": "1", "name": "Lone cam", "lat": 47.25, "lon": -122.44,
             "type": "wsdot", "image_url": ""}]
        a.live._registry._fetched_at = float("inf")
        topo = a.load_topology()
        self.assertFalse(a.using_live_topology)
        self.assertGreater(len(topo["intersections"]), 5)


class TestRegistryRefactor(unittest.TestCase):
    """SeattleLiveAdapter must remain a working subclass of the shared
    RegistryLiveAdapter base (backward-compatibility regression guard)."""

    def test_seattle_is_registry_subclass(self):
        a = SeattleLiveAdapter()
        self.assertIsInstance(a, RegistryLiveAdapter)
        self.assertEqual(a.city_id, "seattle")
        self.assertEqual(a.live.oba_agency, "1")
        self.assertEqual(a.live.nws_station, "KBFI")
        self.assertEqual(a.live.socrata_911_url, SFD_911_URL)

    def test_shared_topology_builder_identical(self):
        """Both subclasses build identical topology from the same registry
        snapshot — the build logic lives in ONE place."""
        cams = [
            {"id": str(i), "name": f"Cam {i}",
             "lat": 47.60 + i * 0.005, "lon": -122.33,
             "type": "sdot", "image_url": f"u{i}"}
            for i in range(6)
        ]
        results = []
        for cls in (SeattleLiveAdapter, TacomaAdapter):
            a = cls(bbox=(47.0, 48.0, -123.0, -122.0))
            a.live._registry._value = cams
            a.live._registry._fetched_at = float("inf")
            topo = a.load_topology()
            self.assertTrue(a.using_live_topology)
            results.append([
                (i.id, i.name, i.lat, i.lon)
                for i in topo["intersections"]])
        self.assertEqual(results[0], results[1])

    def test_parametrized_livedata_urls(self):
        ld = SeattleLiveData(oba_agency="3", nws_station="KTIW")
        self.assertIn("vehicles-for-agency/3.json", ld._oba_url)
        self.assertIn("/stations/KTIW/", ld._nws_url)


if __name__ == "__main__":
    unittest.main()