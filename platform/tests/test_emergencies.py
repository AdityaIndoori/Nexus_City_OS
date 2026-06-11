"""
Emergency feed tests (network-independent).

Proves the Citizen-style 911 layer invariants: category mapping,
traffic-impact flags, geo/time filtering, and graceful degradation.
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.livedata import SeattleLiveData


def _row(rtype, lat=47.61, lon=-122.33, minutes_ago=5, num="F123"):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S",
                       time.localtime(time.time() - minutes_ago * 60))
    return {"type": rtype, "latitude": str(lat), "longitude": str(lon),
            "datetime": ts, "address": "123 Test St",
            "incident_number": num}


class TestEmergencyFeed(unittest.TestCase):
    def _client_with(self, rows):
        live = SeattleLiveData()
        with mock.patch("nexus.livedata._fetch_json", return_value=rows):
            live._emergencies.get()   # prime the cache
        return live

    def test_category_and_traffic_mapping(self):
        live = self._client_with([
            _row("Motor Vehicle Accident", num="1"),
            _row("Aid Response", num="2"),
            _row("Fire in Building", num="3"),
            _row("Totally Unknown Type", num="4"),
        ])
        rows = {r["id"]: r for r in live.emergencies()}
        self.assertEqual(rows["SFD-1"]["category"], "mva")
        self.assertTrue(rows["SFD-1"]["traffic_impacting"])
        self.assertEqual(rows["SFD-2"]["category"], "medical")
        self.assertFalse(rows["SFD-2"]["traffic_impacting"])
        self.assertEqual(rows["SFD-3"]["category"], "fire")
        self.assertTrue(rows["SFD-3"]["traffic_impacting"])
        self.assertEqual(rows["SFD-4"]["category"], "other")

    def test_age_filter(self):
        live = self._client_with([
            _row("Fire", minutes_ago=5, num="new"),
            _row("Fire", minutes_ago=120, num="old"),
        ])
        ids = {r["id"] for r in live.emergencies(max_age_s=3600)}
        self.assertIn("SFD-new", ids)
        self.assertNotIn("SFD-old", ids)

    def test_out_of_region_rows_dropped(self):
        live = self._client_with([
            _row("Fire", lat=40.0, lon=-100.0, num="far"),
            _row("Fire", num="near"),
        ])
        ids = {r["id"] for r in live.emergencies()}
        self.assertEqual(ids, {"SFD-near"})

    def test_network_failure_degrades_to_empty(self):
        live = SeattleLiveData()
        with mock.patch("nexus.livedata._fetch_json",
                        side_effect=OSError("down")):
            self.assertEqual(live.emergencies(), [])
            self.assertFalse(live.health()["sfd_911"]["ok"])

    def test_hazard_alerts_parse(self):
        live = SeattleLiveData()
        payload = {"features": [{"properties": {
            "id": "x", "event": "Wind Advisory", "severity": "Moderate",
            "headline": "Wind Advisory until 6 PM", "expires": "2026"}}]}
        with mock.patch("nexus.livedata._fetch_json", return_value=payload):
            alerts = live.hazard_alerts()
        self.assertEqual(alerts[0]["event"], "Wind Advisory")
        self.assertEqual(alerts[0]["severity"], "Moderate")


if __name__ == "__main__":
    unittest.main()