"""
WSDOT TravelTimes / HighwayAlerts + Waze for Cities feed integration.

Covers:
  * parse_travel_times: normalization, no-data rows dropped, slowest-first.
  * parse_highway_alerts: normalization, bbox filter, priority ordering.
  * parse_waze_feed: jams (speedKMH + legacy m/s), alerts, malformed rows.
  * CongestionEstimator.ingest_waze_jams: jam vertices near an intersection
    become weight-2 samples; blocked jams read as a crawl; congestion meta
    reports the waze source kind.
"""
from __future__ import annotations

import unittest

from nexus.congestion import CongestionEstimator
from nexus.graph import CityGraph
from nexus.livedata import (
    parse_highway_alerts,
    parse_travel_times,
    parse_waze_feed,
)
from nexus.models import Intersection, SignalPhase, SignalTimingPlan


def _timing(iid: str) -> SignalTimingPlan:
    return SignalTimingPlan(
        plan_id=f"STP-{iid}", intersection_id=iid, cycle_seconds=90.0,
        phases=[SignalPhase(1, "through", 40.0, 4.0, 2.0, 25.0)],
        pedestrian_walk_seconds=7.0, crosswalk_length_ft=60.0)


class TravelTimesParserTest(unittest.TestCase):
    def test_normalizes_and_sorts_slowest_first(self):
        rows = [
            {"TravelTimeID": 1, "Description": "Seattle to Bellevue",
             "Distance": 10.5, "CurrentTime": 22, "AverageTime": 11,
             "TimeUpdated": "/Date(1700000000000-0800)/"},
            {"TravelTimeID": 2, "Description": "Seattle to Everett",
             "Distance": 24.0, "CurrentTime": 26, "AverageTime": 25},
        ]
        out = parse_travel_times(rows)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["id"], "1")             # ratio 2.0 first
        self.assertEqual(out[0]["ratio"], 2.0)
        self.assertAlmostEqual(out[0]["updated_at"], 1700000000.0)
        self.assertEqual(out[1]["ratio"], 1.04)

    def test_no_data_rows_dropped(self):
        rows = [{"TravelTimeID": 3, "CurrentTime": 0, "AverageTime": 10},
                {"TravelTimeID": 4, "CurrentTime": -1, "AverageTime": 10},
                {"TravelTimeID": 5, "CurrentTime": "bad"}]
        self.assertEqual(parse_travel_times(rows), [])

    def test_empty_input(self):
        self.assertEqual(parse_travel_times([]), [])
        self.assertEqual(parse_travel_times(None), [])


class HighwayAlertsParserTest(unittest.TestCase):
    def test_normalizes_filters_and_sorts_by_priority(self):
        rows = [
            {"AlertID": 10, "EventCategory": "Construction",
             "Priority": "Low", "HeadlineDescription": "Lane closed",
             "StartRoadwayLocation": {"RoadName": "I-5",
                                      "Latitude": 47.6, "Longitude": -122.3}},
            {"AlertID": 11, "EventCategory": "Collision",
             "Priority": "Highest", "HeadlineDescription": "Blocking crash",
             "StartRoadwayLocation": {"RoadName": "I-90",
                                      "Latitude": 47.59, "Longitude": -122.31}},
            # Outside the bbox → dropped.
            {"AlertID": 12, "EventCategory": "Collision",
             "Priority": "Highest", "HeadlineDescription": "Spokane crash",
             "StartRoadwayLocation": {"RoadName": "I-90",
                                      "Latitude": 47.66, "Longitude": -117.4}},
        ]
        out = parse_highway_alerts(rows, bbox=(47.0, 48.2, -123.0, -121.5))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["id"], "11")     # Highest priority first
        self.assertEqual(out[0]["category"], "Collision")
        self.assertEqual(out[1]["id"], "10")

    def test_missing_location_kept_without_bbox_filter(self):
        rows = [{"AlertID": 13, "EventCategory": "Closure",
                 "Priority": "High", "HeadlineDescription": "Ramp closed"}]
        out = parse_highway_alerts(rows, bbox=(47.0, 48.2, -123.0, -121.5))
        # No coordinates → cannot be excluded by bbox; still surfaced.
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["lat"], 0.0)


class WazeFeedParserTest(unittest.TestCase):
    def test_jams_and_alerts_normalized(self):
        data = {
            "jams": [
                {"uuid": "j1", "street": "Denny Way", "speedKMH": 8.0,
                 "level": 4, "delay": 300,
                 "line": [{"x": -122.33, "y": 47.618},
                          {"x": -122.331, "y": 47.6185}]},
                # legacy m/s speed field
                {"uuid": "j2", "street": "Mercer St", "speed": 2.0,
                 "level": 3, "delay": 120,
                 "line": [{"x": -122.34, "y": 47.624},
                          {"x": -122.341, "y": 47.6245}]},
                # no polyline → dropped
                {"uuid": "j3", "street": "Nowhere", "speedKMH": 5.0},
            ],
            "alerts": [
                {"uuid": "a1", "type": "ACCIDENT", "subtype": "ACCIDENT_MAJOR",
                 "street": "1st Ave", "reliability": 8,
                 "pubMillis": 1700000000000,
                 "location": {"x": -122.335, "y": 47.605}},
                {"uuid": "a2", "type": "HAZARD",
                 "location": {"x": "bad", "y": "bad"}},   # dropped
            ],
        }
        out = parse_waze_feed(data)
        self.assertEqual(len(out["jams"]), 2)
        j1 = out["jams"][0]
        self.assertEqual(j1["id"], "j1")
        self.assertAlmostEqual(j1["speed_mph"], 5.0, places=1)   # 8 km/h
        self.assertEqual(j1["line"][0], (47.618, -122.33))       # (lat, lon)
        j2 = out["jams"][1]
        self.assertAlmostEqual(j2["speed_mph"], 4.5, places=1)   # 2 m/s
        self.assertEqual(len(out["alerts"]), 1)
        a1 = out["alerts"][0]
        self.assertEqual(a1["type"], "ACCIDENT")
        self.assertEqual(a1["reliability"], 8)
        self.assertAlmostEqual(a1["at"], 1700000000.0)

    def test_empty_feed(self):
        out = parse_waze_feed({})
        self.assertEqual(out, {"jams": [], "alerts": []})


class WazeCongestionIngestTest(unittest.TestCase):
    def setUp(self):
        self.graph = CityGraph()
        self.graph.add_intersection(Intersection(
            id="INT-A", name="Denny Way & Fairview Ave",
            lat=47.618, lon=-122.33, monitored=True,
            timing_plan=_timing("INT-A")))
        self.est = CongestionEstimator(self.graph, min_samples=1)

    def test_jam_near_intersection_becomes_sample(self):
        jams = [{"id": "j1", "street": "Denny Way", "speed_mph": 4.0,
                 "level": 3, "delay_s": 240,
                 "line": [(47.618, -122.33), (47.6185, -122.331)]}]
        n = self.est.ingest_waze_jams(jams, now=1000.0)
        self.assertEqual(n, 1)   # de-duped per jam per intersection
        results = self.est.compute(now=1000.0)
        # 4 mph on a 25 mph arterial → heavily congested.
        self.assertIn("INT-A", results)
        self.assertGreater(results["INT-A"], 0.8)
        meta = self.est._meta["INT-A"]
        self.assertEqual(meta["kind"], "waze+probe")

    def test_blocked_jam_reads_as_crawl(self):
        jams = [{"id": "j2", "street": "Denny Way", "speed_mph": None,
                 "level": 5, "delay_s": 900,
                 "line": [(47.618, -122.33)]}]
        self.assertEqual(self.est.ingest_waze_jams(jams, now=1000.0), 1)
        results = self.est.compute(now=1000.0)
        self.assertGreater(results["INT-A"], 0.9)

    def test_no_speed_low_level_skipped(self):
        jams = [{"id": "j3", "street": "Denny Way", "speed_mph": None,
                 "level": 2, "line": [(47.618, -122.33)]}]
        self.assertEqual(self.est.ingest_waze_jams(jams, now=1000.0), 0)

    def test_far_jam_ignored(self):
        jams = [{"id": "j4", "street": "Elsewhere", "speed_mph": 3.0,
                 "level": 3, "line": [(47.9, -122.9)]}]
        self.assertEqual(self.est.ingest_waze_jams(jams, now=1000.0), 0)


if __name__ == "__main__":
    unittest.main()