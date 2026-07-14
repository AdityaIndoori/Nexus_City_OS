# ADR-007 — Read-Only MCP Server Endpoint (M1)

**Status**: Accepted · **Scope**: new `platform/nexus/mcp.py` + `/mcp` dispatch in `server.py` · **Wave**: 1

## Decision

JSON-RPC 2.0 handler (stdlib `json`) on the existing `ThreadingHTTPServer` at
`/mcp`, exposing read-only tools — `list_incidents`, `get_plan`, `get_audit` —
plus `attempt_action`, which ALWAYS routes through SafetyGate and returns a
structured refusal for unsafe requests. Zero mutation surface. Also folded in:
capture operator approve/reject decisions as labels now (calibration itself is
a Series-A feature, out of scope).

## Why (money / investors / completion)

- **The demo moment**: an investor's own Claude session queries live incidents
  at nexus.aindoori.com and gets REFUSED on an unsafe action — the moat made
  visible in 30 seconds.
- **Category timing**: Flow Labs launched FlowMCP (June 2026, confirmed via
  ITS International) — LLM-agent interfaces to traffic platforms are a live
  category. Differentiation: "the only traffic-agent interface where every
  action passes a safety gate."
- **Policy fit**: maps directly onto EU AI Act Art 14 human-oversight
  requirements and YC Spring 2026 RFS "AI for Government".

## Rejected alternatives

- Write-capable MCP tools: exposing mutation of safety-critical municipal
  infrastructure to arbitrary agents is a threat surface no city attorney or
  CISO accepts; reads + gated refusal demonstrate the same moat safely.
- Separate MCP server process/framework: unnecessary — stdlib JSON-RPC shim
  over the existing HTTP server.

## Acceptance

`test_mcp.py`: read tools return well-formed JSON-RPC results; `attempt_action`
on an unsafe plan returns a structured SafetyGate refusal and never mutates
state; malformed JSON-RPC → proper error object, no crash. Network-free.
