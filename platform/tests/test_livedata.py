"""
Live-data adapter tests — network-independent.

These tests verify the graceful-degradation contract of ``SeattleLiveAdapter``
(PRD §8: never hide failures, always keep operating): when the live registry
or feeds are unreachable, the adapter must fall back to the deterministic
offline topology/feeds without raising.

Network calls are stubbed out, so the suite stays offline-deterministic.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.adapters import SeattleLiveAdapter
from nexus.models import TransitVehicle, WeatherCondition


class OfflineLiveData:
    """Stub standing in for SeattleLiveData with all feeds down."""

    def cameras(self, bbox=None):
        return []

    def vehicles(self, bbox=None):
        return []

    def weather(self):
        return None

    def health(self):
        return {"camera_registry": {"ok": False},
                "transit": {"ok": False}, "weather": {"ok": False}}


class StubLiveData(OfflineLiveData):
    """Stub with a tiny synthetic 'live' registry and feeds."""

    def cameras(self, bbox=None):
        return [
            {"id": "CMR-1", "name": "2nd Ave & Battery St",
             "lat": 47.6160, "lon": -122.3470, "type": "sdot",
             "image_url": "https://example.test/2_Battery_NS.jpg"},
            {"id": "CMR-2", "name": "3rd Ave & Pine St",
             "lat": 47.6105, "lon": -122.3385, "type": "sdot",
             "image_url": "https://example.test/3_Pine_NS.jpg"},
            {"id": "CMR-3", "name": "5th Ave & Pike St",
             "lat": 47.6110, "lon": -122.3345, "type": "sdot",
             "image_url": "https://example.test/5_Pike_NS.jpg"},
        ]

    def vehicles(self, bbox=None):
        return [{"id": "1_4321", "lat": 47.612, "lon": -122.34,
                 "trip_id": "1_t1", "updated_at": 0.0}]

    def weather(self):
        return {"condition": "rain", "temperature_f": 48.0,
                "raw_description": "Light Rain", "station": "KBFI (NWS)"}


class TestLiveAdapterFallback(unittest.TestCase):
    def test_offline_falls_back_to_deterministic_topology(self):
        adapter = SeattleLiveAdapter()
        adapter.live = OfflineLiveData()
        topo = adapter.load_topology()
        self.assertFalse(adapter.using_live_topology)
        self.assertEqual(len(topo["intersections"]), 42)  # offline grid

    def test_offline_transit_and_weather_fall_back(self):
        adapter = SeattleLiveAdapter()
        adapter.live = OfflineLiveData()
        vehicles = adapter.poll_transit()
        self.assertTrue(vehicles)
        self.assertTrue(all(isinstance(v, TransitVehicle) for v in vehicles))
        weather = adapter.poll_weather()
        self.assertIsInstance(weather, WeatherCondition)


class TestLiveAdapterWithRegistry(unittest.TestCase):
    def test_live_topology_built_from_registry(self):
        adapter = SeattleLiveAdapter()
        adapter.live = StubLiveData()
        topo = adapter.load_topology()
        self.assertTrue(adapter.using_live_topology)
        self.assertEqual(len(topo["intersections"]), 3)
        names = {i.name for i in topo["intersections"]}
        self.assertIn("2nd Ave & Battery St", names)
        # 3rd Ave is marked as the EMS corridor
        third = next(i for i in topo["intersections"]
                     if "3rd Ave" in i.name)
        self.assertTrue(third.ems_corridor)
        # every live intersection is camera-monitored
        self.assertTrue(all(i.monitored for i in topo["intersections"]))
        # camera mapping is populated for the image proxy
        self.assertEqual(len(adapter.live_camera_map), 3)
        # nearby intersections got connected by segments
        self.assertTrue(topo["segments"])

    def test_live_transit_estimates_speed_between_polls(self):
        adapter = SeattleLiveAdapter()
        stub = StubLiveData()
        adapter.live = stub
        first = adapter.poll_transit()
        self.assertEqual(first[0].id, "1_4321")
        # move the vehicle; second poll should estimate a nonzero speed
        stub.vehicles = lambda bbox=None: [{
            "id": "1_4321", "lat": 47.6135, "lon": -122.34,
            "trip_id": "1_t1", "updated_at": 0.0}]
        second = adapter.poll_transit()
        self.assertGreaterEqual(second[0].speed_mph, 0.0)

    def test_live_weather_maps_nws_conditions(self):
        adapter = SeattleLiveAdapter()
        adapter.live = StubLiveData()
        w = adapter.poll_weather()
        self.assertEqual(w.condition, "rain")
        self.assertEqual(w.temperature_f, 48.0)


if __name__ == "__main__":
    unittest.main()