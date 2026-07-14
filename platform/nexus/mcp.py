"""
Nexus City OS — Read-Only MCP Endpoint (ADR-007, M1).

JSON-RPC 2.0 handler exposing four read-only tools over the existing
ThreadingHTTPServer. Zero mutation surface: attempt_action routes through
the SafetyGate and returns a structured refusal — it never calls approve,
apply, or execute. Principal always comes from the verified token passed in
by the server; never from the JSON body.

Tools:
  list_incidents  — current incidents (viewer+)
  get_plan        — plan by id (viewer+)
  get_audit       — audit entries (analyst/admin only)
  attempt_action  — SafetyGate dry-run; always returns structured refusal/result
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .engine import NexusEngine, PermissionDenied
from .models import (
    ActionPlan,
    ConfidenceBreakdown,
    Operation,
    Provenance,
    Role,
    new_id,
    now_ts,
)

# JSON-RPC 2.0 error codes
_PARSE_ERROR     = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS  = -32602
_INTERNAL_ERROR  = -32603
# Application-level
_PERMISSION_DENIED = -32001

# Tools advertised in the discovery response.
_TOOLS = [
    {
        "name": "list_incidents",
        "description": "Return the current active incidents in the city graph.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_plan",
        "description": "Return a single ActionPlan by plan_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID (e.g. PLAN-ABCD1234)"},
            },
            "required": ["plan_id"],
        },
    },
    {
        "name": "get_audit",
        "description": (
            "Return recent audit entries. Requires analyst or admin role."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (1–200, default 50).",
                    "default": 50,
                },
            },
        },
    },
    {
        "name": "attempt_action",
        "description": (
            "Propose a signal-timing action. ALWAYS routes through the SafetyGate "
            "and returns a structured verdict (passed/refused + rule violations). "
            "Never mutates any state regardless of verdict."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Intersection IDs the action targets.",
                },
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "intersection_id": {"type": "string"},
                            "phase_id": {"type": "integer"},
                            "delta_seconds": {"type": "number"},
                        },
                        "required": ["type", "intersection_id", "phase_id", "delta_seconds"],
                    },
                },
                "justification": {"type": "string"},
            },
            "required": ["incident_id", "targets", "operations", "justification"],
        },
    },
]


def _ok(result: Any, rpc_id: Any) -> Dict[str, Any]:
    """Wrap a successful result in a JSON-RPC 2.0 envelope."""
    return {"jsonrpc": "2.0", "result": result, "id": rpc_id}


def _err(code: int, message: str, rpc_id: Any) -> Dict[str, Any]:
    """Wrap an error in a JSON-RPC 2.0 error envelope."""
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": rpc_id}


def handle_mcp(engine: NexusEngine,
               principal: Dict[str, Any],
               payload: Any) -> Dict[str, Any]:
    """Dispatch a JSON-RPC 2.0 request. Never raises — all errors are
    returned as proper JSON-RPC error objects.

    ``principal`` is the verified token payload from server._principal();
    never trust anything in ``payload`` for identity.
    """
    # Validate the outer envelope first.
    if not isinstance(payload, dict):
        return _err(_INVALID_REQUEST, "Request must be a JSON object.", None)

    rpc_id = payload.get("id")  # may be None for notifications (we still respond)

    if payload.get("jsonrpc") != "2.0":
        return _err(_INVALID_REQUEST, "jsonrpc must be '2.0'.", rpc_id)

    method = payload.get("method", "")
    if not isinstance(method, str) or not method:
        return _err(_INVALID_REQUEST, "method must be a non-empty string.", rpc_id)

    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _err(_INVALID_PARAMS, "params must be an object.", rpc_id)

    # MCP discovery / init handshake — cheap no-op responses.
    if method == "initialize":
        return _ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "nexus-city-os", "version": "1.0"},
        }, rpc_id)

    if method == "tools/list":
        return _ok({"tools": _TOOLS}, rpc_id)

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        if not isinstance(tool_args, dict):
            return _err(_INVALID_PARAMS, "arguments must be an object.", rpc_id)
        return _dispatch_tool(engine, principal, rpc_id, tool_name, tool_args)

    return _err(_METHOD_NOT_FOUND, f"Unknown method: {method}", rpc_id)


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _dispatch_tool(engine: NexusEngine,
                   principal: Dict[str, Any],
                   rpc_id: Any,
                   tool_name: str,
                   args: Dict[str, Any]) -> Dict[str, Any]:
    user_id = principal.get("sub", "")
    role_str = principal.get("role", "")
    try:
        role = Role(role_str)
    except ValueError:
        return _err(_PERMISSION_DENIED, f"Unrecognised role: {role_str}", rpc_id)

    try:
        if tool_name == "list_incidents":
            return _ok(_tool_list_incidents(engine), rpc_id)

        if tool_name == "get_plan":
            plan_id = str(args.get("plan_id", ""))
            if not plan_id:
                return _err(_INVALID_PARAMS, "plan_id is required.", rpc_id)
            return _ok(_tool_get_plan(engine, plan_id), rpc_id)

        if tool_name == "get_audit":
            # analyst and admin only
            if role not in (Role.ANALYST, Role.ADMIN):
                return _err(
                    _PERMISSION_DENIED,
                    f"get_audit requires analyst or admin role (got {role_str}).",
                    rpc_id,
                )
            limit = int(args.get("limit", 50))
            limit = max(1, min(200, limit))
            return _ok(_tool_get_audit(engine, limit), rpc_id)

        if tool_name == "attempt_action":
            return _ok(_tool_attempt_action(engine, args), rpc_id)

        return _err(_METHOD_NOT_FOUND, f"Unknown tool: {tool_name}", rpc_id)

    except KeyError as exc:
        return _err(_INVALID_PARAMS, f"Not found: {exc}", rpc_id)
    except (TypeError, ValueError) as exc:
        return _err(_INVALID_PARAMS, str(exc), rpc_id)
    except Exception:  # noqa: BLE001
        # Fixed message — never echo raw exception text to callers.
        return _err(_INTERNAL_ERROR, "internal error", rpc_id)


# ---------------------------------------------------------------------------
# Tool implementations — read-only
# ---------------------------------------------------------------------------

def _tool_list_incidents(engine: NexusEngine) -> Dict[str, Any]:
    """Return all incidents currently in the city graph."""
    incidents = []
    with engine._lock:
        for inc in engine.graph.incidents.values():
            incidents.append({
                "id": inc.id,
                "type": inc.type.value,
                "intersection_id": inc.intersection_id,
                "severity": inc.severity,
                "state": inc.state.value,
                "detected_at": inc.detected_at,
                "description": inc.description,
                "detection_source": inc.detection_source,
            })
    return {"incidents": incidents, "count": len(incidents)}


def _tool_get_plan(engine: NexusEngine, plan_id: str) -> Dict[str, Any]:
    """Return a single plan by id; raises KeyError if not found."""
    with engine._lock:
        plan = engine.plans.get(plan_id)
    if plan is None:
        raise KeyError(repr(plan_id))
    d = plan.to_dict()
    # Strip detection_frame_jpeg bytes if present (not JSON-serialisable).
    d.pop("detection_frame_jpeg", None)
    return d


def _tool_get_audit(engine: NexusEngine, limit: int) -> Dict[str, Any]:
    """Return recent audit entries. Analyst/admin gate is in _dispatch_tool."""
    entries = engine.audit.entries(limit=limit)
    return {"entries": entries, "count": len(entries)}


def _tool_attempt_action(engine: NexusEngine,
                         args: Dict[str, Any]) -> Dict[str, Any]:
    """Route a proposed action through the SafetyGate; NEVER mutate state.

    Constructs a minimal ActionPlan from the caller-supplied args, evaluates
    it through SafetyGate.evaluate() (which only reads graph state), then
    returns the structured verdict. The plan is discarded immediately after.
    """
    incident_id = str(args.get("incident_id", ""))
    targets = [str(t) for t in (args.get("targets") or [])]
    raw_ops = args.get("operations") or []
    justification = str(args.get("justification", ""))

    if not incident_id:
        raise ValueError("incident_id is required")
    if not targets:
        raise ValueError("targets must be a non-empty list")
    if not isinstance(raw_ops, list):
        raise ValueError("operations must be a list")

    operations = []
    for op in raw_ops:
        if not isinstance(op, dict):
            raise ValueError("each operation must be an object")
        operations.append(Operation(
            type=str(op.get("type", "")),
            intersection_id=str(op.get("intersection_id", "")),
            phase_id=int(op.get("phase_id", 0)),
            delta_seconds=float(op.get("delta_seconds", 0.0)),
        ))

    # Minimal provenance that satisfies the provenance check so the gate
    # can reach the constraint verifier. We supply the current timestamp so
    # H3 (stale data) does not fire spuriously.
    ts = now_ts()
    provenance = Provenance(
        entities=targets,
        data_sources=[{"source": "mcp_caller", "timestamp": ts}],
        weather={"condition": "unknown"},
        rationale=justification or "MCP attempt_action probe",
    )
    confidence = ConfidenceBreakdown(
        model_certainty=100.0,
        data_freshness=100.0,
        coverage_completeness=100.0,
        historical_accuracy=100.0,
    )
    # Ephemeral plan — never stored, never approved, never applied.
    probe_plan = ActionPlan(
        plan_id=new_id("MCP"),
        created_at=ts,
        model_version="mcp-probe",
        incident_id=incident_id,
        targets=targets,
        operations=operations,
        justification=justification,
        provenance=provenance,
        confidence=confidence,
    )

    # SafetyGate.evaluate() is a pure read (it reads _active_changes and the
    # graph under its own lock; it never writes to engine.plans or the graph).
    # record_metrics=False: a probe must not skew the /api/status counters.
    evaluated = engine.safety.evaluate(probe_plan, record_metrics=False)

    passed = evaluated.status.value == "pending_approval"
    violations = []
    if evaluated.block_reason:
        # block_reason is a flat string; emit it as a single violation entry
        # so callers get structured data even without the rule-id detail.
        violations = [{"rule_id": "GATE", "message": evaluated.block_reason}]

    return {
        "passed": passed,
        "status": evaluated.status.value,
        "block_reason": evaluated.block_reason,
        "violations": violations,
        "note": (
            "attempt_action is a read-only safety probe. "
            "No state was mutated regardless of this verdict."
        ),
    }
