"""
Phase 2 tests — AI vision sweep over live camera frames. Network-free:
``analyze_fn`` and ``frame_fn`` are injected fakes; ``sweep_once()`` is
driven directly (no thread).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus import bootstrap
from nexus.adapters import SeattleAdapter
from nexus.vision import VisionSweep, map_anomaly


class FakeAdapter:
    """Minimal stand-in exposing live_camera_map like SeattleLiveAdapter."""

    def __init__(self, engine, n=3):
        self.live_camera_map = {}
        for i, inter in enumerate(list(
                engine.graph.intersections.values())[:n]):
            cam_id = f"CAMX-{i:02d}"
            self.live_camera_map[cam_id] = {
                "live_id": f"live-{i}", "name": inter.name,
                "type": "sdot", "image_url": "",
                "intersection_id": inter.id,
            }


def detection_result(confidence=90, assessment="collision visible — "
                                               "two vehicles blocking lanes"):
    return {"available": True, "incident_visible": True,
            "congestion_visible": "high", "visibility": "good",
            "confidence_pct": confidence, "assessment": assessment}


def clear_result():
    return {"available": True, "incident_visible": False,
            "congestion_visible": "low", "visibility": "good",
            "confidence_pct": 92, "assessment": "traffic flowing normally"}


class TestMapAnomaly(unittest.TestCase):
    def test_keywords(self):
        self.assertEqual(map_anomaly("multi-vehicle collision"), "collision")
        self.assertEqual(map_anomaly("a crash in the left lane"), "collision")
        self.assertEqual(map_anomaly("stalled vehicle on shoulder"),
                         "stopped_vehicle")
        self.assertEqual(map_anomaly("vehicle stopped mid-block"),
                         "stopped_vehicle")
        self.assertEqual(map_anomaly("heavy backup"), "congestion")


class TestVisionSweep(unittest.TestCase):
    def setUp(self):
        self.engine, _, _ = bootstrap(SeattleAdapter(seed=42))
        self.adapter = FakeAdapter(self.engine)

    def make_sweep(self, analyze_fn, frame_fn=None, per_sweep=3):
        return VisionSweep(self.engine, self.adapter, per_sweep=per_sweep,
                           analyze_fn=analyze_fn,
                           frame_fn=frame_fn or (lambda lid: b"jpegbytes"))

    def test_detection_raises_ai_vision_incident(self):
        sweep = self.make_sweep(lambda f, c: detection_result())
        result = sweep.sweep_once()
        self.assertEqual(result["analyzed"], 3)
        self.assertGreaterEqual(result["incidents_raised"], 1)
        ai_incidents = [i for i in self.engine.graph.incidents.values()
                        if i.detection_source == "ai_vision"]
        self.assertTrue(ai_incidents)
        self.assertEqual(ai_incidents[0].type.value, "collision")
        # Audit trail records the automated detection.
        actions = [e["action"] for e in self.engine.audit.entries(limit=50)]
        self.assertIn("vision_detection", actions)

    def test_low_confidence_raises_nothing(self):
        sweep = self.make_sweep(lambda f, c: detection_result(confidence=50))
        sweep.sweep_once()
        self.assertEqual(sweep.incidents_raised, 0)
        self.assertFalse(any(i.detection_source == "ai_vision"
                             for i in self.engine.graph.incidents.values()))

    def test_llm_failure_degrades_without_exception(self):
        def boom(f, c):
            raise RuntimeError("LLM gateway down")
        sweep = self.make_sweep(boom)
        result = sweep.sweep_once()   # must not raise
        self.assertEqual(result["analyzed"], 0)
        self.assertTrue(sweep.stats()["degraded"])
        self.assertIn("LLM gateway down", sweep.last_error)

    def test_unavailable_analysis_degrades(self):
        sweep = self.make_sweep(
            lambda f, c: {"available": False, "error": "no llm configured"})
        sweep.sweep_once()
        self.assertEqual(sweep.frames_analyzed, 0)
        self.assertTrue(sweep.stats()["degraded"])

    def test_missing_frame_degrades(self):
        sweep = self.make_sweep(lambda f, c: detection_result(),
                                frame_fn=lambda lid: None)
        result = sweep.sweep_once()
        self.assertEqual(result["analyzed"], 0)
        self.assertTrue(sweep.stats()["degraded"])

    def test_congestion_mapping_applied(self):
        # "high" → 0.8 congestion at the swept intersection (no anomaly).
        iid = next(iter(self.adapter.live_camera_map.values()))[
            "intersection_id"]
        sweep = self.make_sweep(lambda f, c: {
            "available": True, "incident_visible": False,
            "congestion_visible": "high", "visibility": "good",
            "confidence_pct": 88, "assessment": "heavy traffic"})
        sweep.sweep_once()
        inter = self.engine.graph.get_intersection(iid)
        # telemetry: speed = 25*(1-0.8)=5, stopped = 4
        # engine: 0.9*(1-5/25) + 0.04*4 = 0.72 + 0.16 = 0.88
        self.assertGreater(inter.congestion, 0.7)

    def test_prioritizes_congested_intersections(self):
        # Mark one camera's intersection as highly congested; it must be
        # in the picked set even with per_sweep=1.
        metas = list(self.adapter.live_camera_map.values())
        hot_iid = metas[-1]["intersection_id"]
        self.engine.graph.update_congestion(hot_iid, 0.9)
        for m in metas[:-1]:
            self.engine.graph.update_congestion(m["intersection_id"], 0.1)
        analyzed_iids = []

        def spy(frame, context):
            return clear_result()
        sweep = VisionSweep(self.engine, self.adapter, per_sweep=1,
                            analyze_fn=spy, frame_fn=lambda lid: b"x")
        picked = sweep._pick_cameras()
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["intersection_id"], hot_iid)

    def test_stats_shape(self):
        sweep = self.make_sweep(lambda f, c: clear_result())
        sweep.sweep_once()
        s = sweep.stats()
        for key in ("running", "frames_analyzed", "incidents_raised",
                    "last_sweep_at", "degraded"):
            self.assertIn(key, s)
        self.assertFalse(s["running"])     # thread never started in tests
        self.assertEqual(s["frames_analyzed"], 3)


if __name__ == "__main__":
    unittest.main()