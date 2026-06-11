"""
Phase 1 tests — real congestion estimation from live bus GPS (+ optional
WSDOT flow) and the engine guard that stops the edge simulator from
overwriting fresh real estimates. Network-free.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus import bootstrap
from nexus.adapters import SeattleAdapter, default_timing_plan
from nexus.congestion import CongestionEstimator, speed_limit_estimate
from nexus.graph import CityGraph
from nexus.models import EdgeTelemetry, Intersection, TransitVehicle, now_ts


def make_graph(*specs):
    """Build a graph from (id, name, lat, lon) tuples spaced far enough
    apart that the estimator radius never overlaps two intersections."""
    graph = CityGraph()
    for iid, name, lat, lon in specs:
        graph.add_intersection(Intersection(
            id=iid, name=name, lat=lat, lon=lon, monitored=True,
            timing_plan=default_timing_plan(iid), congestion=0.2))
    return graph


def bus(vid, lat, lon, speed_mph, age_s=0.0):
    return TransitVehicle(id=vid, route="KC Metro", lat=lat, lon=lon,
                          speed_mph=speed_mph,
                          last_update=time.time() - age_s)


class TestSpeedLimitHeuristic(unittest.TestCase):
    def test_surface_street_default(self):
        self.assertEqual(speed_limit_estimate("4th Ave & Pike St"), 25.0)

    def test_highway_markers(self):
        for name in ("I-5 @ NE 195th St", "I-90 EB", "SR-99 Tunnel",
                     "I-405 @ SE 8th St"):
            self.assertEqual(speed_limit_estimate(name), 55.0, name)


class TestCongestionEstimator(unittest.TestCase):
    def setUp(self):
        self.graph = make_graph(
            ("INT-A", "4th Ave & Pike St", 47.60, -122.34),
            ("INT-B", "I-5 @ NE 195th St", 47.70, -122.30),
        )
        self.est = CongestionEstimator(self.graph)

    def test_free_flow_buses_near_zero_congestion(self):
        vehicles = [bus("V1", 47.60, -122.34, 25.0),
                    bus("V2", 47.601, -122.341, 24.0)]
        n = self.est.ingest_vehicles(vehicles)
        self.assertGreaterEqual(n, 2)
        results = self.est.compute()
        self.assertIn("INT-A", results)
        self.assertLessEqual(results["INT-A"], 0.1)

    def test_crawling_buses_high_congestion(self):
        vehicles = [bus("V1", 47.60, -122.34, 2.0),
                    bus("V2", 47.601, -122.341, 3.0)]
        self.est.ingest_vehicles(vehicles)
        results = self.est.compute()
        self.assertGreaterEqual(results["INT-A"], 0.85)

    def test_min_samples_required(self):
        self.est.ingest_vehicles([bus("V1", 47.60, -122.34, 10.0)])
        results = self.est.compute()
        self.assertNotIn("INT-A", results)

    def test_dwelling_and_stale_fixes_skipped(self):
        vehicles = [
            bus("V1", 47.60, -122.34, 0.0),          # dwelling at a stop
            bus("V2", 47.60, -122.34, 0.4),          # below 0.5 mph
            bus("V3", 47.60, -122.34, 20.0, age_s=300.0),  # stale fix
        ]
        self.assertEqual(self.est.ingest_vehicles(vehicles), 0)

    def test_freshness_window_expiry(self):
        now = time.time()
        self.est.ingest_vehicles(
            [bus("V1", 47.60, -122.34, 20.0),
             bus("V2", 47.60, -122.34, 18.0)], now=now)
        self.est.compute(now=now)
        self.assertIn("INT-A", self.est.fresh_ids(now=now))
        # Past the freshness window the estimate expires.
        self.assertEqual(self.est.fresh_ids(now=now + 400.0), set())
        self.assertEqual(self.est.compute(now=now + 400.0), {})

    def test_apply_writes_through_to_graph(self):
        self.est.ingest_vehicles([bus("V1", 47.60, -122.34, 5.0),
                                  bus("V2", 47.60, -122.34, 5.0)])
        self.est.compute()
        applied = self.est.apply(self.graph)
        self.assertEqual(applied, 1)
        self.assertAlmostEqual(
            self.graph.get_intersection("INT-A").congestion, 0.8, places=2)

    def test_highway_uses_55mph_baseline(self):
        # 25 mph at a highway camera is HEAVY traffic (1 - 25/55 ≈ 0.55),
        # not free flow.
        self.est.ingest_vehicles([bus("V1", 47.70, -122.30, 25.0),
                                  bus("V2", 47.70, -122.30, 25.0)])
        results = self.est.compute()
        self.assertAlmostEqual(results["INT-B"], 1.0 - 25.0 / 55.0, places=2)

    def test_flow_samples_high_weight(self):
        # A single WSDOT flow record (weight 3) satisfies min_samples=2.
        n = self.est.ingest_flow([{"id": "F1", "lat": 47.60, "lon": -122.34,
                                   "speed_mph": 10.0}])
        self.assertEqual(n, 1)
        self.assertTrue(self.est.flow_active)
        results = self.est.compute()
        self.assertIn("INT-A", results)
        self.assertAlmostEqual(results["INT-A"], 1.0 - 10.0 / 25.0, places=2)

    def test_median_robust_to_outlier(self):
        self.est.ingest_vehicles([bus("V1", 47.60, -122.34, 20.0),
                                  bus("V2", 47.60, -122.34, 21.0),
                                  bus("V3", 47.60, -122.34, 1.0)])  # outlier
        results = self.est.compute()
        # Median = 20 → congestion = 0.2, not dominated by the 1 mph outlier.
        self.assertAlmostEqual(results["INT-A"], 0.2, places=2)


class TestEngineGuard(unittest.TestCase):
    """Simulator telemetry must not overwrite fresh real estimates, but
    anomalies always drive congestion (injected scenarios work anywhere)."""

    def setUp(self):
        self.engine, self.edge, _ = bootstrap(SeattleAdapter(seed=42))
        self.iid = next(iter(self.engine.graph.cameras.values())).intersection_id
        self.cam_id = next(c.id for c in self.engine.graph.cameras.values()
                           if c.intersection_id == self.iid)

    def _publish(self, avg_speed, anomaly=None):
        telemetry = EdgeTelemetry(
            camera_id=self.cam_id, intersection_id=self.iid,
            captured_at=now_ts(), vehicle_count=10,
            avg_speed_mph=avg_speed, stopped_vehicles=5,
            anomaly=anomaly, redacted=True)
        self.engine.bus.publish(self.engine.telemetry_topic,
                                telemetry.to_json())

    def test_simulator_does_not_overwrite_real_estimate(self):
        self.engine.graph.update_congestion(self.iid, 0.77)
        self.engine.real_congestion_ids = {self.iid}
        self._publish(avg_speed=2.0)  # would normally drive congestion high
        self.assertAlmostEqual(
            self.engine.graph.get_intersection(self.iid).congestion, 0.77)

    def test_anomaly_bypasses_guard(self):
        self.engine.graph.update_congestion(self.iid, 0.1)
        self.engine.real_congestion_ids = {self.iid}
        self._publish(avg_speed=1.0, anomaly="collision")
        inter = self.engine.graph.get_intersection(self.iid)
        self.assertGreater(inter.congestion, 0.7)
        # And the incident pipeline still fires.
        self.assertTrue(any(
            i.intersection_id == self.iid
            for i in self.engine.graph.incidents.values()))

    def test_default_guard_empty_keeps_existing_behavior(self):
        self.assertEqual(self.engine.real_congestion_ids, set())
        self._publish(avg_speed=2.0)
        inter = self.engine.graph.get_intersection(self.iid)
        self.assertGreater(inter.congestion, 0.7)


if __name__ == "__main__":
    unittest.main()