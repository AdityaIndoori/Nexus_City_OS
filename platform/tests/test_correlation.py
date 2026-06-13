"""
M2 — 911 ↔ incident auto-correlation tests (network-free).

Proves traffic-impacting SFD dispatches near a camera intersection raise a
real platform incident tagged detection_source="sfd_911", that the mapping
is idempotent, distance-bounded, and that non-traffic dispatches are ignored.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus import bootstrap
from nexus.adapters import SeattleAdapter
from nexus.models import IncidentType, now_ts


def dispatch(did, lat, lon, traffic=True, rtype="Motor Vehicle Accident"):
    return {"id": did, "type": rtype, "category": "mva",
            "traffic_impacting": traffic, "address": f"{did} Test St",
            "lat": lat, "lon": lon, "at": now_ts()}


class TestNineOneOneCorrelation(unittest.TestCase):
    def setUp(self):
        self.engine, _, _ = bootstrap(SeattleAdapter(seed=42))
        # Anchor on a camera-monitored intersection (so the corroboration
        # test can publish edge telemetry through its camera).
        cam = next(iter(self.engine.graph.cameras.values()))
        self.inter = self.engine.graph.get_intersection(cam.intersection_id)

    def test_traffic_dispatch_raises_incident(self):
        n = self.engine.correlate_911(
            [dispatch("D1", self.inter.lat, self.inter.lon)])
        self.assertEqual(n, 1)
        inc = next(i for i in self.engine.graph.incidents.values()
                   if i.detection_source == "sfd_911")
        self.assertEqual(inc.type, IncidentType.COLLISION)
        self.assertEqual(inc.intersection_id, self.inter.id)

    def test_non_traffic_dispatch_ignored(self):
        n = self.engine.correlate_911([dispatch(
            "D2", self.inter.lat, self.inter.lon,
            traffic=False, rtype="Aid Response")])
        self.assertEqual(n, 0)

    def test_idempotent_same_dispatch(self):
        d = dispatch("D3", self.inter.lat, self.inter.lon)
        self.assertEqual(self.engine.correlate_911([d]), 1)
        # Re-feeding the same dispatch id raises nothing new.
        self.assertEqual(self.engine.correlate_911([d]), 0)

    def test_distance_bounded(self):
        # ~1° latitude away (~111 km) is far outside the 150 m radius.
        n = self.engine.correlate_911(
            [dispatch("D4", self.inter.lat + 1.0, self.inter.lon)])
        self.assertEqual(n, 0)

    def test_corroborates_existing_collision(self):
        # An edge-detected collision already exists at the intersection.
        from nexus.models import EdgeTelemetry
        cam = next(c for c in self.engine.graph.cameras.values()
                   if c.intersection_id == self.inter.id)
        self.engine.bus.publish(
            self.engine.telemetry_topic,
            EdgeTelemetry(
                camera_id=cam.id, intersection_id=self.inter.id,
                captured_at=now_ts(), vehicle_count=10, avg_speed_mph=1.0,
                stopped_vehicles=8, anomaly="collision",
                redacted=True).to_json())
        before = len(self.engine.graph.incidents)
        n = self.engine.correlate_911(
            [dispatch("D5", self.inter.lat, self.inter.lon)])
        # No NEW incident — the dispatch corroborates the existing one.
        self.assertEqual(n, 0)
        self.assertEqual(len(self.engine.graph.incidents), before)
        inc = next(i for i in self.engine.graph.incidents.values()
                   if i.intersection_id == self.inter.id)
        self.assertTrue(any("corroborated" in h.get("action", "")
                            for h in inc.action_history))


if __name__ == "__main__":
    unittest.main()