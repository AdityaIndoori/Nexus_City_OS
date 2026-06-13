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
from nexus.congestion import (
    BUS_FREEFLOW_FACTOR,
    CongestionEstimator,
    ratio_to_congestion,
    speed_limit_estimate,
)
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


class TestRatioMapping(unittest.TestCase):
    """Research-calibrated speed-ratio → congestion normalization."""

    def test_free_flow_is_zero(self):
        self.assertEqual(ratio_to_congestion(1.0), 0.0)
        self.assertEqual(ratio_to_congestion(1.3), 0.0)   # faster than FF

    def test_jam_is_one(self):
        self.assertEqual(ratio_to_congestion(0.12), 1.0)
        self.assertEqual(ratio_to_congestion(0.0), 1.0)

    def test_midpoint_linear(self):
        # halfway between free flow (1.0) and jam (0.12) → 0.5
        self.assertAlmostEqual(ratio_to_congestion(0.56), 0.5, places=2)


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

    def test_bus_freeflow_factor_no_phantom_congestion(self):
        """Buses cruise at ~75% of the limit even in free flow (probe
        literature). 19 mph on a 25 mph arterial must read FREE FLOW,
        not ~25% congested (the bias the old 1-speed/limit model had)."""
        self.est.ingest_vehicles([bus("V1", 47.60, -122.34, 19.0),
                                  bus("V2", 47.601, -122.341, 19.0)])
        results = self.est.compute()
        self.assertLessEqual(results["INT-A"], 0.05)

    def test_dwell_bias_keep_max_per_vehicle(self):
        """A bus decelerating into a stop (3 mph fix) right after cruising
        past at 18 mph must NOT flip the estimate to jammed — the max
        speed in the window is the evidence of what traffic allowed."""
        now = time.time()
        self.est.ingest_vehicles([bus("V1", 47.60, -122.34, 18.0),
                                  bus("V2", 47.601, -122.341, 18.0)],
                                 now=now)
        # Same vehicles report slow fixes near the stop moments later.
        self.est.ingest_vehicles([bus("V1", 47.60, -122.34, 3.0),
                                  bus("V2", 47.601, -122.341, 2.0)],
                                 now=now + 10)
        results = self.est.compute(now=now + 10)
        self.assertLessEqual(results["INT-A"], 0.1)   # still free flow

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
        # ratio = 5/(25*0.75) = 0.267 → (1-0.267)/0.88 ≈ 0.833
        expected = ratio_to_congestion(5.0 / (25.0 * BUS_FREEFLOW_FACTOR))
        self.assertAlmostEqual(
            self.graph.get_intersection("INT-A").congestion,
            expected, places=2)

    def test_highway_uses_55mph_baseline(self):
        # A bus at 25 mph at a highway camera is congested traffic
        # (bus free flow there ≈ 41 mph), not free flow.
        self.est.ingest_vehicles([bus("V1", 47.70, -122.30, 25.0),
                                  bus("V2", 47.70, -122.30, 25.0)])
        results = self.est.compute()
        expected = ratio_to_congestion(25.0 / (55.0 * BUS_FREEFLOW_FACTOR))
        self.assertAlmostEqual(results["INT-B"], expected, places=2)
        self.assertGreater(results["INT-B"], 0.3)

    def test_flow_samples_high_weight_raw_limit(self):
        # A single WSDOT flow record (weight 3) satisfies min_samples=2.
        # Loop detectors measure general traffic → scored vs the RAW limit
        # (no bus free-flow discount).
        n = self.est.ingest_flow([{"id": "F1", "lat": 47.60, "lon": -122.34,
                                   "speed_mph": 10.0}])
        self.assertEqual(n, 1)
        self.assertTrue(self.est.flow_active)
        results = self.est.compute()
        self.assertIn("INT-A", results)
        self.assertAlmostEqual(results["INT-A"],
                               ratio_to_congestion(10.0 / 25.0), places=2)

    def test_median_robust_to_outlier(self):
        self.est.ingest_vehicles([bus("V1", 47.60, -122.34, 20.0),
                                  bus("V2", 47.60, -122.34, 21.0),
                                  bus("V3", 47.60, -122.34, 1.0)])  # outlier
        results = self.est.compute()
        # Median ratio is from the 20 mph bus (≥ free flow) → ~0 congestion,
        # not dominated by the 1 mph outlier.
        self.assertLessEqual(results["INT-A"], 0.05)


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