# MCP Connect — Nexus City OS Read-Only Endpoint

**Endpoint:** `POST /mcp`  
**Protocol:** JSON-RPC 2.0 (stdlib `json`, no framework)  
**Transport:** HTTPS via Cloudflare Tunnel (nexus.aindoori.com) or local HTTP

## Authentication

Identity comes solely from **Cloudflare Access** — there is no
`/api/login`, no bearer tokens issued by the origin.

- **Browser clients** (a human who already signed in at Cloudflare's edge):
  the `CF_Authorization` cookie set by Access is sent automatically on every
  request, including `/mcp`.
- **Machine / MCP clients** (no browser session): authenticate with a
  Cloudflare Access **service token** instead — send the
  `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers on every
  request. The edge validates the token and forwards a signed Access JWT to
  the origin exactly as it would for a human login; `nexus/cfaccess.py`
  verifies it the same way either path.

Service-token principals appear as `svc:<client-id>` and get the role from
`NEXUS_CF_ACCESS_SERVICE_ROLES` (default `viewer`; never `citizen`).
`get_audit` still requires `analyst` or `admin` — map the service token's
client ID to one of those roles if the MCP client needs audit access.

**Never** use `?token=` query parameters — the origin has no such fallback.
Image (`/api/camera`) and SSE (`/api/events`) URLs are **cookie-auth only**:
they take the `CF_Authorization` cookie from a browser session, not the
`CF-Access-Client-Id`/`Secret` headers, so they are not reachable by
header-only machine clients.

External investor / read-only access: a separate Access application scoped
to `/mcp` with its own policy (per-investor short-lived service token, or an
email-OTP Allow policy) — no code change required. See
`CLOUDFLARE_ACCESS_SETUP.md`.

## Claude / MCP client config

For a browser-based client that has already completed the Access login,
no extra config is needed beyond the cookie jar. For a machine client
using a service token:

```json
{
  "mcpServers": {
    "nexus-city-os": {
      "url": "https://nexus.aindoori.com/mcp",
      "headers": {
        "CF-Access-Client-Id": "<client-id>.access",
        "CF-Access-Client-Secret": "<client-secret>"
      }
    }
  }
}
```

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

## Notes

- TTL: whatever the Access application's session duration is set to
  (browser cookie) or the service token's expiry (Zero Trust dashboard) —
  not a Nexus-origin concept.
- Revocation: revoke the service token or remove the user from the Access
  policy in the Zero Trust dashboard; the origin trusts the JWT until it
  expires.
- Rotation: re-authenticate (browser) or rotate the service token's secret
  (Zero Trust dashboard); no origin-side rotation step.
- Service tokens are the standard machine-client path for production / CI /
  investor access — see `CLOUDFLARE_ACCESS_SETUP.md` for creating one and
  mapping it to a role.
