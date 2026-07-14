# MCP Connect — Nexus City OS Read-Only Endpoint

**Endpoint:** `POST /mcp`  
**Protocol:** JSON-RPC 2.0 (stdlib `json`, no framework)  
**Transport:** HTTPS via Cloudflare Tunnel (nexus.aindoori.com) or local HTTP

## Authentication

**Bearer token in the `Authorization` header — no exceptions.**

```
Authorization: Bearer <token>
```

**Never** use `?token=` query parameters. Query strings land in access logs,
proxy logs, and browser history. The server rejects unauthenticated requests
with HTTP 401 before the MCP handler is reached.

Tokens are issued by `POST /api/login` (8-hour TTL, in-memory revocation on
logout). For investor demos, use a scoped viewer token; full-trust analyst
tokens unlock `get_audit`.

External investor access: Cloudflare Access config (separate Access app scoped
to `/mcp` + per-investor short-lived service token) — no code change required.

## Claude / MCP client config

```json
{
  "mcpServers": {
    "nexus-city-os": {
      "url": "https://nexus.aindoori.com/mcp",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}
```

Replace `<your-token>` with a token obtained from `POST /api/login`.

## Tools

| Tool | Role required | Description |
|---|---|---|
| `list_incidents` | viewer+ | Current active incidents in the city graph |
| `get_plan` | viewer+ | Single ActionPlan by `plan_id` |
| `get_audit` | **analyst / admin only** | Recent tamper-evident audit entries |
| `attempt_action` | viewer+ | Safety-gate dry-run; structured refusal always returned |

### `list_incidents`

No parameters. Returns `{ incidents: [...], count: N }`.

### `get_plan`

```json
{ "plan_id": "PLAN-ABCD1234" }
```

Returns the full plan dict including `status`, `targets`, `operations`,
`confidence_score`, `block_reason`.

### `get_audit`

```json
{ "limit": 50 }
```

`limit` clamps to 1–200 (default 50). Viewer and operator callers receive a
JSON-RPC permission error (code `-32001`), not a crash.

### `attempt_action`

```json
{
  "incident_id": "INC-ABCD1234",
  "targets": ["INT-001"],
  "operations": [
    { "type": "reduce_green", "intersection_id": "INT-001",
      "phase_id": 1, "delta_seconds": 30.0 }
  ],
  "justification": "Clear lane for emergency vehicle"
}
```

**This tool never mutates any state.** The action is routed through the
independent SafetyGate (MUTCD R1–R7, hallucination monitor H1–H4, provenance,
confidence abstention). Unsafe actions return a structured refusal:

```json
{
  "passed": false,
  "status": "blocked_constraint",
  "block_reason": "[R1] ...",
  "violations": [{ "rule_id": "GATE", "message": "..." }],
  "note": "attempt_action is a read-only safety probe. No state was mutated regardless of this verdict."
}
```

A `passed: true` result means the action would have reached the operator
approval queue — it does NOT mean it was approved or executed. Every real
action still requires human approval (`requires_human_approval` is constant
`True`).

## Token notes

- TTL: 8 hours (fixed, no config knob)
- Revocation: in-memory on logout; lost on server restart
- Rotation: re-login to get a fresh token
- For production / investor CI: Cloudflare Access service tokens are preferred
  (issued by Zero Trust dashboard, short-lived, scoped to `/mcp`)
