"""
LLM-layer tests (network-independent).

Proves the AI-integration invariants:
  * extract_json handles fenced / prefixed / malformed LLM output.
  * LLM plan refinement rejects hallucinated intersection IDs and
    out-of-bound deltas (the strict validation gate).
  * On LLM failure, the deterministic plan stands (graceful degradation).
  * SafetyGate still evaluates LLM-refined plans (defense in depth).
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus import bootstrap
from nexus.adapters import SeattleAdapter
from nexus.llm import LLMUnavailable, extract_json
from nexus.models import IncidentType, PlanStatus


class TestExtractJson(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(
            extract_json('Here:\n```json\n{"a": 1}\n```\nend'), {"a": 1})

    def test_prefixed_text(self):
        self.assertEqual(
            extract_json('Sure! {"ops": [1, 2]} hope that helps'),
            {"ops": [1, 2]})

    def test_garbage_returns_none(self):
        self.assertIsNone(extract_json("no json here"))


class _EngineHarness:
    """Bootstrap an offline engine with LLM enabled, then stub the client."""

    def __init__(self):
        self.engine, self.edge, _ = bootstrap(SeattleAdapter(), use_llm=True)

    def incident(self):
        # must be a camera-monitored intersection for edge detection
        cam = next(iter(self.engine.graph.cameras.values()))
        iid = cam.intersection_id
        self.edge.inject_scenario(iid, IncidentType.COLLISION)
        self.edge.tick()
        return next(i for i in self.engine.graph.incidents.values())


class TestLLMRefinement(unittest.TestCase):
    def test_llm_failure_falls_back_to_deterministic(self):
        h = _EngineHarness()
        inc = h.incident()
        with mock.patch.object(h.engine.copilot.llm, "chat",
                               side_effect=LLMUnavailable("down")):
            plan = h.engine.recommend(inc.id)
        self.assertIn("deterministic", h.engine.copilot.last_generator)
        self.assertEqual(plan.status, PlanStatus.PENDING_APPROVAL)
        self.assertTrue(plan.operations)   # deterministic ops intact

    def test_hallucinated_ids_rejected(self):
        h = _EngineHarness()
        inc = h.incident()
        bad = ('{"operations": [{"intersection_id": "INT-9999", '
               '"delta_seconds": 10}], "rationale": "fake", '
               '"model_certainty": 95}')
        with mock.patch.object(h.engine.copilot.llm, "chat",
                               return_value=bad):
            h.engine.recommend(inc.id)
        # all LLM ops rejected ⇒ deterministic plan kept
        self.assertIn("rejected", h.engine.copilot.last_generator)

    def test_out_of_bound_delta_rejected(self):
        h = _EngineHarness()
        inc = h.incident()
        plan_probe = h.engine.copilot.generate_plan.__wrapped__ \
            if hasattr(h.engine.copilot.generate_plan, "__wrapped__") else None
        # find a real candidate id via cascading impact
        cands = [i["intersection_id"] for i in
                 h.engine.graph.cascading_impact(inc.intersection_id,
                                                 max_hops=2)][:3]
        bad = ('{"operations": [{"intersection_id": "%s", '
               '"delta_seconds": 500}], "rationale": "way too long", '
               '"model_certainty": 90}' % (cands[0] if cands else "INT-0001"))
        with mock.patch.object(h.engine.copilot.llm, "chat",
                               return_value=bad):
            h.engine.recommend(inc.id)
        self.assertIn("rejected", h.engine.copilot.last_generator)

    def test_valid_llm_output_accepted_and_safety_gated(self):
        h = _EngineHarness()
        inc = h.incident()
        cands = [i["intersection_id"] for i in
                 h.engine.graph.cascading_impact(inc.intersection_id,
                                                 max_hops=2)][:3]
        if not cands:
            self.skipTest("no neighbors in topology")
        good = ('{"operations": [{"intersection_id": "%s", '
                '"delta_seconds": 12}], "rationale": "Extend green on the '
                'parallel arterial to drain the queue.", '
                '"model_certainty": 88}' % cands[0])
        with mock.patch.object(h.engine.copilot.llm, "chat",
                               return_value=good):
            plan = h.engine.recommend(inc.id)
        self.assertEqual(h.engine.copilot.last_generator,
                         "llm (claude-sonnet-4.6)")
        self.assertIn("sonnet", plan.model_version)
        # the LLM-refined plan still went through the SafetyGate
        self.assertEqual(plan.status, PlanStatus.PENDING_APPROVAL)
        self.assertEqual(plan.targets, [cands[0]])
        self.assertEqual(plan.operations[0].delta_seconds, 12.0)

    def test_chat_falls_back_when_llm_down(self):
        h = _EngineHarness()
        with mock.patch.object(h.engine.copilot.llm, "chat",
                               side_effect=LLMUnavailable("down")):
            r = h.engine.copilot.query("op-1", "how is congestion?")
        self.assertIn("answer", r)   # deterministic answer produced

    def test_vision_degrades_gracefully(self):
        h = _EngineHarness()
        with mock.patch.object(h.engine.copilot.llm, "chat",
                               side_effect=LLMUnavailable("down")):
            r = h.engine.copilot.analyze_frame(b"\xff\xd8fake", "ctx")
        self.assertFalse(r["available"])
        self.assertIn("unreachable", r["error"])


if __name__ == "__main__":
    unittest.main()