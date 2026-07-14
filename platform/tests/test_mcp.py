"""
MCP endpoint tests (ADR-007, M1).

Covers: list_incidents / get_plan / get_audit for permitted roles;
get_audit as viewer → permission error; attempt_action → structured
SafetyGate refusal + zero state mutation; malformed JSON-RPC → error object.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus import bootstrap
from nexus.adapters import SeattleAdapter
from nexus.engine import PermissionDenied
from nexus.mcp import handle_mcp
from nexus.models import IncidentType, Role


def make_platform():
    return bootstrap(SeattleAdapter(seed=42))


def _principal(role: str, user_id: str = None) -> dict:
    uid = user_id or f"{role}-1"
    return {"sub": uid, "role": role}


def _call(engine, role, method, params=None, *, user_id=None, rpc_id=1):
    payload = {"jsonrpc": "2.0", "method": method, "id": rpc_id}
    if params is not None:
        payload["params"] = params
    return handle_mcp(engine, _principal(role, user_id), payload)


def _tool_call(engine, role, tool_name, args=None, **kw):
    return _call(engine, role, "tools/call",
                 {"name": tool_name, "arguments": args or {}}, **kw)


def detect_incident(engine, edge):
    iid = next(iter(engine.graph.cameras.values())).intersection_id
    edge.inject_scenario(iid, IncidentType.COLLISION)
    edge.tick()
    return next(i for i in engine.graph.incidents.values()
                if i.intersection_id == iid)


class TestMcpDiscovery(unittest.TestCase):

    def setUp(self):
        self.engine, _, _ = make_platform()

    def test_initialize_returns_protocol_version(self):
        resp = _call(self.engine, "viewer", "initialize")
        self.assertIn("result", resp)
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")

    def test_tools_list_returns_four_tools(self):
        resp = _call(self.engine, "viewer", "tools/list")
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names,
                         {"list_incidents", "get_plan", "get_audit", "attempt_action"})

    def test_unknown_method_returns_method_not_found(self):
        resp = _call(self.engine, "viewer", "no_such_method")
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

    def test_missing_jsonrpc_field_returns_invalid_request(self):
        resp = handle_mcp(self.engine, _principal("viewer"),
                          {"method": "initialize", "id": 1})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32600)

    def test_non_dict_payload_returns_invalid_request(self):
        resp = handle_mcp(self.engine, _principal("viewer"), "not a dict")
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32600)

    def test_null_payload_returns_invalid_request(self):
        resp = handle_mcp(self.engine, _principal("viewer"), None)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32600)

    def test_non_string_method_returns_invalid_request(self):
        resp = handle_mcp(self.engine, _principal("viewer"),
                          {"jsonrpc": "2.0", "method": 42, "id": 1})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32600)


class TestListIncidents(unittest.TestCase):

    def setUp(self):
        self.engine, self.edge, _ = make_platform()

    def test_viewer_gets_well_formed_result(self):
        resp = _tool_call(self.engine, "viewer", "list_incidents")
        self.assertIn("result", resp)
        result = resp["result"]
        self.assertIn("incidents", result)
        self.assertIn("count", result)
        self.assertIsInstance(result["incidents"], list)

    def test_operator_gets_well_formed_result(self):
        resp = _tool_call(self.engine, "operator", "list_incidents", user_id="op-1")
        self.assertIn("result", resp)

    def test_incidents_reflect_detected_collision(self):
        inc = detect_incident(self.engine, self.edge)
        resp = _tool_call(self.engine, "viewer", "list_incidents")
        ids = [i["id"] for i in resp["result"]["incidents"]]
        self.assertIn(inc.id, ids)

    def test_incident_entry_has_required_fields(self):
        detect_incident(self.engine, self.edge)
        resp = _tool_call(self.engine, "viewer", "list_incidents")
        entry = resp["result"]["incidents"][0]
        for field in ("id", "type", "intersection_id", "severity",
                      "state", "detected_at", "description"):
            self.assertIn(field, entry, f"missing field: {field}")

    def test_count_matches_list_length(self):
        detect_incident(self.engine, self.edge)
        resp = _tool_call(self.engine, "viewer", "list_incidents")
        r = resp["result"]
        self.assertEqual(r["count"], len(r["incidents"]))


class TestGetPlan(unittest.TestCase):

    def setUp(self):
        self.engine, self.edge, _ = make_platform()

    def _make_plan(self):
        inc = detect_incident(self.engine, self.edge)
        self.engine.acknowledge_incident("op-1", inc.id)
        return self.engine.recommend(inc.id)

    def test_viewer_gets_plan_by_id(self):
        plan = self._make_plan()
        resp = _tool_call(self.engine, "viewer", "get_plan",
                          {"plan_id": plan.plan_id})
        self.assertIn("result", resp)
        self.assertEqual(resp["result"]["plan_id"], plan.plan_id)

    def test_missing_plan_id_returns_invalid_params(self):
        resp = _tool_call(self.engine, "viewer", "get_plan", {})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32602)

    def test_unknown_plan_id_returns_invalid_params(self):
        resp = _tool_call(self.engine, "viewer", "get_plan",
                          {"plan_id": "PLAN-DOESNOTEXIST"})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32602)

    def test_plan_result_has_expected_fields(self):
        plan = self._make_plan()
        resp = _tool_call(self.engine, "viewer", "get_plan",
                          {"plan_id": plan.plan_id})
        result = resp["result"]
        for field in ("plan_id", "status", "targets", "operations",
                      "justification", "confidence_score"):
            self.assertIn(field, result, f"missing field: {field}")


class TestGetAudit(unittest.TestCase):

    def setUp(self):
        self.engine, _, _ = make_platform()

    def test_analyst_gets_audit_entries(self):
        resp = _tool_call(self.engine, "analyst", "get_audit",
                          user_id="analyst-1")
        self.assertIn("result", resp)
        result = resp["result"]
        self.assertIn("entries", result)
        self.assertIsInstance(result["entries"], list)
        self.assertGreater(result["count"], 0)

    def test_admin_gets_audit_entries(self):
        resp = _tool_call(self.engine, "admin", "get_audit", user_id="admin-1")
        self.assertIn("result", resp)

    def test_viewer_gets_permission_error(self):
        resp = _tool_call(self.engine, "viewer", "get_audit")
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32001)

    def test_operator_gets_permission_error(self):
        resp = _tool_call(self.engine, "operator", "get_audit", user_id="op-1")
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32001)

    def test_limit_clamps_result_count(self):
        resp = _tool_call(self.engine, "analyst", "get_audit",
                          {"limit": 1}, user_id="analyst-1")
        self.assertLessEqual(len(resp["result"]["entries"]), 1)

    def test_permission_error_is_json_rpc_object_not_crash(self):
        resp = _tool_call(self.engine, "viewer", "get_audit")
        self.assertIn("jsonrpc", resp)
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertIn("error", resp)
        self.assertNotIn("result", resp)


class TestAttemptAction(unittest.TestCase):

    def setUp(self):
        self.engine, self.edge, _ = make_platform()

    def _first_intersection(self):
        return next(iter(self.engine.graph.intersections))

    def _unsafe_op(self, iid):
        timing = self.engine.graph.get_intersection(iid).timing_plan
        return {
            "type": "reduce_green",
            "intersection_id": iid,
            "phase_id": timing.phases[0].phase_id,
            "delta_seconds": 60.0,
        }

    def test_attempt_action_returns_structured_result(self):
        iid = self._first_intersection()
        resp = _tool_call(self.engine, "operator", "attempt_action", {
            "incident_id": "INC-TEST",
            "targets": [iid],
            "operations": [self._unsafe_op(iid)],
            "justification": "test probe",
        }, user_id="op-1")
        self.assertIn("result", resp)
        result = resp["result"]
        self.assertIn("passed", result)
        self.assertIn("status", result)
        self.assertIn("violations", result)
        self.assertIn("note", result)

    def test_attempt_action_no_state_mutation(self):
        iid = self._first_intersection()
        before_cycle = (self.engine.graph.get_intersection(iid)
                        .timing_plan.cycle_seconds)
        before_plan_count = len(self.engine.plans)
        before_incident_count = len(self.engine.graph.incidents)

        _tool_call(self.engine, "operator", "attempt_action", {
            "incident_id": "INC-TEST",
            "targets": [iid],
            "operations": [self._unsafe_op(iid)],
            "justification": "mutation guard test",
        }, user_id="op-1")

        after_cycle = (self.engine.graph.get_intersection(iid)
                       .timing_plan.cycle_seconds)
        self.assertEqual(before_cycle, after_cycle,
                         "attempt_action must not mutate timing plans")
        self.assertEqual(len(self.engine.plans), before_plan_count,
                         "attempt_action must not add plans to engine.plans")
        self.assertEqual(len(self.engine.graph.incidents), before_incident_count,
                         "attempt_action must not create incidents")

    def test_attempt_action_unsafe_returns_refused(self):
        iid = self._first_intersection()
        resp = _tool_call(self.engine, "operator", "attempt_action", {
            "incident_id": "INC-TEST",
            "targets": [iid],
            "operations": [self._unsafe_op(iid)],
            "justification": "intentionally unsafe",
        }, user_id="op-1")
        result = resp["result"]
        self.assertFalse(result["passed"])
        self.assertIsNotNone(result["block_reason"])

    def test_attempt_action_safe_op_may_pass(self):
        iid = self._first_intersection()
        timing = self.engine.graph.get_intersection(iid).timing_plan
        safe_op = {
            "type": "extend_green",
            "intersection_id": iid,
            "phase_id": timing.phases[0].phase_id,
            "delta_seconds": 2.0,
        }
        resp = _tool_call(self.engine, "operator", "attempt_action", {
            "incident_id": "INC-TEST",
            "targets": [iid],
            "operations": [safe_op],
            "justification": "safe extension probe",
        }, user_id="op-1")
        self.assertIn("result", resp)
        result = resp["result"]
        self.assertIn("passed", result)
        self.assertIn("status", result)

    def test_attempt_action_missing_incident_id_returns_error(self):
        iid = self._first_intersection()
        resp = _tool_call(self.engine, "operator", "attempt_action", {
            "targets": [iid],
            "operations": [self._unsafe_op(iid)],
            "justification": "x",
        }, user_id="op-1")
        self.assertIn("error", resp)

    def test_attempt_action_empty_targets_returns_error(self):
        resp = _tool_call(self.engine, "operator", "attempt_action", {
            "incident_id": "INC-TEST",
            "targets": [],
            "operations": [],
            "justification": "x",
        }, user_id="op-1")
        self.assertIn("error", resp)

    def test_attempt_action_note_field_always_present(self):
        iid = self._first_intersection()
        resp = _tool_call(self.engine, "operator", "attempt_action", {
            "incident_id": "INC-TEST",
            "targets": [iid],
            "operations": [self._unsafe_op(iid)],
            "justification": "test",
        }, user_id="op-1")
        self.assertIn("note", resp["result"])

    def test_attempt_action_does_not_change_safety_metrics(self):
        iid = self._first_intersection()
        before = dict(self.engine.safety.metrics.as_dict())
        _tool_call(self.engine, "operator", "attempt_action", {
            "incident_id": "INC-TEST",
            "targets": [iid],
            "operations": [self._unsafe_op(iid)],
            "justification": "metrics guard",
        }, user_id="op-1")
        self.assertEqual(self.engine.safety.metrics.as_dict(), before,
                         "attempt_action probes must not skew SafetyMetrics")

    def test_active_change_count_unchanged_after_attempt(self):
        iid = self._first_intersection()
        before = self.engine.safety.verifier.active_change_count()
        _tool_call(self.engine, "operator", "attempt_action", {
            "incident_id": "INC-TEST",
            "targets": [iid],
            "operations": [self._unsafe_op(iid)],
            "justification": "change count guard",
        }, user_id="op-1")
        self.assertEqual(self.engine.safety.verifier.active_change_count(), before)


class TestMalformedRequests(unittest.TestCase):

    def setUp(self):
        self.engine, _, _ = make_platform()

    def test_list_type_payload_returns_error(self):
        resp = handle_mcp(self.engine, _principal("viewer"), [1, 2, 3])
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32600)

    def test_params_as_list_returns_invalid_params(self):
        resp = handle_mcp(self.engine, _principal("viewer"), {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": [1, 2],
            "id": 1,
        })
        self.assertIn("error", resp)

    def test_unknown_tool_returns_method_not_found(self):
        resp = _tool_call(self.engine, "viewer", "no_such_tool")
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

    def test_error_response_never_raises(self):
        for payload in [None, "string", 42, [], {}, {"jsonrpc": "2.0", "id": 99}]:
            resp = handle_mcp(self.engine, _principal("viewer"), payload)
            self.assertIn("error", resp)

    def test_all_error_responses_are_valid_jsonrpc_objects(self):
        payloads = [
            None,
            "bad",
            {"jsonrpc": "1.0", "method": "x", "id": 1},
            {"jsonrpc": "2.0", "method": 99, "id": 1},
        ]
        for payload in payloads:
            resp = handle_mcp(self.engine, _principal("viewer"), payload)
            self.assertEqual(resp.get("jsonrpc"), "2.0")
            self.assertIn("error", resp)
            self.assertIsInstance(resp["error"]["code"], int)
            self.assertIsInstance(resp["error"]["message"], str)


if __name__ == "__main__":
    unittest.main()
