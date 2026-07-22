# AGENTS.md

Generated: 2026-07-13 | Commit: f0600a1 | Branch: main

## OVERVIEW

Nexus City OS — decision-intelligence platform for real-time smart-city traffic management (Seattle-first, multi-city via adapter SDK). **Zero external dependencies: Python 3.10+ stdlib only.**

## STRUCTURE

```
platform/
  nexus/        # all runtime code (22 modules) — see platform/nexus/AGENTS.md
  tests/        # unittest suite, network-free — see platform/tests/AGENTS.md
  scripts/      # manual probes/verifiers — NEVER in CI (need live server or network)
  ui/           # index.html (operator console) + landing.html — no build step, served raw
  run.py        # launcher: --host --port --city {seattle,tacoma} --sim --no-vision
docs/           # GitHub Pages mirror (regenerate: python platform/scripts/build_pages_mirror.py)
PRD_v2.md       # AUTHORITATIVE spec (v2.1) — wins over MASTER_PROMPT.md; PRD.md is v1, ignore
llm_config.json # gitignored {base_url, api_key}; env vars override; missing → deterministic mode
models.json     # gateway snapshot only — NOT loaded at runtime (model IDs hardcoded in llm.py)
.env.example    # canonical env var doc; docker compose reads .env; run.py does NOT auto-load it
```

## WHERE TO LOOK

| Task | Location | Notes |
|---|---|---|
| Add/modify HTTP endpoint | platform/nexus/server.py | flat if/elif dispatch in do_GET/do_POST |
| Safety rules (MUTCD R1–R7, H1–H4) | platform/nexus/safety.py | THE MOAT — never bypass |
| Incident lifecycle, HITL, modes | platform/nexus/engine.py | orchestrator |
| New city support | platform/nexus/adapters.py | subclass ONLY; no city branches in core |
| Live data feeds (OBA/NWS/SDOT/WSDOT/911) | platform/nexus/livedata.py | TTL cache + stale fallback |
| LLM plan/vision/chat | platform/nexus/copilot.py + llm.py | output schema-validated, never trusted |
| Auth / identity — Cloudflare Access JWT is the ONLY identity layer | platform/nexus/cfaccess.py | pure-stdlib RS256 verify; role map via NEXUS_CF_ACCESS_*; NEXUS_DEV_IDENTITY for offline dev |
| Operator UI | platform/ui/index.html | single 143KB file, string-substituted at serve time |
| Env vars | .env.example | NEXUS_ prefix (exceptions: PORT, WSDOT_ACCESS_CODE) |

## CONVENTIONS (deviations from standard)

- `from __future__ import annotations` + module docstring `"Nexus City OS — <Module> (<phase>)."` citing PRD §N, in every module.
- Old-style typing generics (`Dict`, `List`, `Optional`) — NOT `dict[...]`/`X | None`, despite 3.10+. Match this.
- `@dataclass` everywhere; never TypedDict/NamedTuple/Pydantic. State machines: `class Foo(str, Enum)`.
- No method docstrings — inline comments instead. Constants UPPERCASE with trailing PRD/MUTCD citation comments; units in names (`_s`, `_mph`, `_pct`).
- Every stateful class holds `self._lock` (threading.Lock/RLock); all mutation under `with self._lock:`.
- IDs via `new_id(prefix)` → `PREFIX-8HEXUPPER`; timestamps via `now_ts()` epoch floats, never datetime.
- **No logging module anywhere.** Observability = AuditTrail (hash-chained). Only print() is the startup banner.
- No lint/format/type configs exist. No pyproject.toml. Don't add them.

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER add a pip dependency.** Stdlib only — CI enforces zero-install.
- **NEVER bypass SafetyGate** — it runs after plan generation AND re-verifies at approval.
- **NEVER create a physical-mutation path outside Live mode** (Shadow/Advisory must remain non-executing).
- **NEVER touch `requires_human_approval`** (models.py, constant True); execution only via `approve_plan()`.
- **NEVER trust LLM output** — schema validation (candidate IDs only, deltas 1–25s, ≤3 ops) + SafetyGate.
- **NEVER take acting principal from request body** — only from verified token/JWT.
- **NEVER trust the CF Access email header alone** — full JWT verification (sig+issuer+aud+expiry).
- **NEVER let unredacted telemetry past the privacy gate** (`redacted=False` → DLQ); raw video never enters the platform.
- **NEVER add city-specific branches** to engine/safety/graph/bus/audit/store — adapters only.
- **NEVER show below-threshold plans with a warning** — abstention withholds them entirely.
- **NEVER let camera proxy fetch user-supplied URLs** — fixed server-side allowlist (no SSRF).
- **NEVER crash a consumer on malformed payloads** — route to DLQ topic; daemons never raise out of loops.
- New operator text inputs must pass copilot `_sanitize()` (prompt-injection guard, blocked AND logged).

## COMMANDS

```bash
python -m unittest discover -s platform/tests -t platform -v   # full suite (163 tests, no network)
python platform/run.py --sim                                    # offline deterministic run
docker compose build && docker compose up -d                    # deploy loop (:8757)
python platform/scripts/build_pages_mirror.py                   # regenerate docs/ Pages mirror
```

## NOTES

- Deployed behind Cloudflare tunnel run as a Windows service — **never** start the compose `cloudflared` sidecar (one-connector rule; duplicates break SSH/HTTP).
- Env booleans parsed via `in ("1","true","yes")`; read with `os.environ.get(...).strip()`.
- CI: Python 3.10 + 3.12 matrix + Docker build; every push.
