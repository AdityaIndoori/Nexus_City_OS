"""
Phase 3 tests — historical analytics over the Store. Network-free:
``Store(":memory:")`` seeded with synthetic congestion/incident/plan rows.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.analytics import Analytics
from nexus.store import Store


NOW = time.time()


def seed_congestion(store, now=NOW):
    """3 hours of samples across two intersections (INT-B is the hotspot)."""
    rows = []
    for h in range(3):                       # 2h ago, 1h ago, this hour
        at = now - (2 - h) * 3600.0
        rows.append(("INT-A", 0.2 + 0.1 * h, at))       # 0.2, 0.3, 0.4
        rows.append(("INT-B", 0.7 + 0.1 * h, at))       # 0.7, 0.8, 0.9
    store.add_congestion_samples(rows)


class TestAnalytics(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.analytics = Analytics(self.store)

    def test_empty_store_no_crash(self):
        s = self.analytics.summary(hours=24, now=NOW)
        self.assertTrue(s["available"])
        self.assertEqual(s["congestion_by_hour"], [])
        self.assertEqual(s["hotspots"], [])
        self.assertEqual(s["incident_counts"], {})
        self.assertEqual(s["total_samples"], 0)

    def test_hourly_buckets(self):
        seed_congestion(self.store)
        s = self.analytics.summary(hours=24, now=NOW)
        self.assertEqual(s["total_samples"], 6)
        self.assertEqual(len(s["congestion_by_hour"]), 3)
        # Each bucket holds one INT-A + one INT-B sample.
        first = s["congestion_by_hour"][0]
        self.assertEqual(first["samples"], 2)
        self.assertAlmostEqual(first["avg"], (0.2 + 0.7) / 2, places=3)
        self.assertAlmostEqual(first["max"], 0.7, places=3)

    def test_hotspot_ordering_and_name_lookup(self):
        seed_congestion(self.store)
        names = {"INT-A": "4th & Pike", "INT-B": "I-5 @ Mercer"}
        s = self.analytics.summary(
            hours=24, now=NOW, name_lookup=lambda iid: names.get(iid, iid))
        self.assertEqual(s["hotspots"][0]["intersection_id"], "INT-B")
        self.assertEqual(s["hotspots"][0]["name"], "I-5 @ Mercer")
        self.assertAlmostEqual(s["hotspots"][0]["avg"], 0.8, places=3)
        self.assertEqual(s["hotspots"][1]["intersection_id"], "INT-A")

    def test_window_filters_old_samples(self):
        # one sample far outside the window, one inside
        self.store.add_congestion_samples([
            ("INT-A", 0.5, NOW - 48 * 3600.0),
            ("INT-A", 0.5, NOW - 600.0),
        ])
        s = self.analytics.summary(hours=24, now=NOW)
        self.assertEqual(s["total_samples"], 1)

    def test_incident_counts_and_vision(self):
        self.store.upsert_incident("INC-1", "resolved", {
            "id": "INC-1", "type": "collision",
            "detection_source": "edge_simulator"}, NOW - 100)
        self.store.upsert_incident("INC-2", "detected", {
            "id": "INC-2", "type": "collision",
            "detection_source": "ai_vision"}, NOW - 50)
        self.store.upsert_incident("INC-3", "detected", {
            "id": "INC-3", "type": "congestion",
            "detection_source": "edge_simulator"}, NOW - 20)
        s = self.analytics.summary(hours=24, now=NOW)
        self.assertEqual(s["incident_counts"],
                         {"collision": 2, "congestion": 1})
        self.assertEqual(s["vision_sweep"]["incidents_confirmed"], 1)

    def test_plan_outcomes_bucketed(self):
        plans = [("P1", "shadow_logged"), ("P2", "rejected"),
                 ("P3", "blocked_constraint"), ("P4", "reverted"),
                 ("P5", "executed"), ("P6", "pending_approval")]
        for pid, status in plans:
            self.store.upsert_plan(pid, status, "INC-1", {}, NOW - 10)
        s = self.analytics.summary(hours=24, now=NOW)
        self.assertEqual(s["plan_outcomes"]["approved"], 2)   # shadow+executed
        self.assertEqual(s["plan_outcomes"]["rejected"], 1)
        self.assertEqual(s["plan_outcomes"]["blocked"], 1)
        self.assertEqual(s["plan_outcomes"]["reverted"], 1)
        self.assertEqual(s["plan_outcomes"]["pending"], 1)

    def test_prune_history_deletes_old_rows(self):
        self.store.add_congestion_samples([
            ("INT-A", 0.5, NOW - 10 * 86400.0),       # 10 days old
            ("INT-A", 0.5, NOW - 60.0),
        ])
        deleted = self.store.prune_history(NOW - 7 * 86400.0)
        self.assertEqual(deleted, 1)
        remaining = self.store.congestion_history(0.0)
        self.assertEqual(len(remaining), 1)

    def test_hours_clamped(self):
        s = self.analytics.summary(hours=10000, now=NOW)
        self.assertLessEqual(s["window_hours"], 168.0)
        s = self.analytics.summary(hours=0, now=NOW)
        self.assertGreaterEqual(s["window_hours"], 0.5)


if __name__ == "__main__":
    unittest.main()