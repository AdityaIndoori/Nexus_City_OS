# platform/nexus/AGENTS.md

## OVERVIEW

All runtime code: 22 stdlib-only modules; engine.py orchestrates, safety.py gates, server.py exposes.

## WHERE TO LOOK

| Task | Module | Notes |
|---|---|---|
| Domain types, ActionPlan, confidence weights | models.py | dataclasses; `requires_human_approval` constant |
| City graph + 3-hop BFS cascade | graph.py | thread-safe |
| Pub/sub bus + DLQ | bus.py | malformed → topic "dlq" |
| Edge CV sim + PII redaction + scenarios | edge.py | redaction always on |
| Plan generation, vision triage, chat, injection guard | copilot.py | `_sanitize()` L~508; INJECTION_PATTERNS L~61 |
| LLM client (OpenAI-compat, stdlib) | llm.py | MODEL_PLANNER=Sonnet, vision/chat=Haiku; config from env > llm_config.json |
| MUTCD R1–R7, hallucination H1–H4, abstention | safety.py | threshold DEFAULT 70, MIN 50, MAX 95, Admin-only |
| CTM dry-run simulation | simulation.py | weather-aware |
| Mode ladder, HITL, rollback, RBAC | engine.py | privacy gate L~244; `_execute` mode branch L~850 |
| Hash-chained audit | audit.py | `verify_chain()` |
| City adapters (Seattle offline/live, Tacoma) | adapters.py | new city = subclass here only |
| Live feed clients (OBA/NWS/SDOT/911/WSDOT) | livedata.py | `_fetch_json` is the mock point |
| Congestion estimator (bus-GPS probes) | congestion.py | weighted median; bus free-flow = limit×0.75 |
| VisionSweep daemon | vision.py | Haiku on camera frames ~2min; ≥70% conf; source="ai_vision" |
| Hourly analytics from SQLite | analytics.py | 7-day retention |
| SQLite persistence | store.py | `Store(":memory:")` in tests |
| Passwords/tokens/lockout | auth.py | PBKDF2 210k iter |
| Rate limiting, body cap, headers, Turnstile | security.py | trusts CF-Connecting-IP only if NEXUS_TRUST_PROXY |
| CF Access JWT (RS256 vs JWKS) | cfaccess.py | role map via NEXUS_CF_ACCESS_* env |
| HTTP routes, SSE, camera proxy | server.py | see below |

## server.py SPECIFICS

- `make_handler(runtime)` closure → Handler class; ThreadingHTTPServer. Routes = flat if/elif in do_GET (~L436) / do_POST (~L884). Add new routes into those chains.
- Auth: `_principal()` (~L384) — CF Access JWT (header/cookie) or HMAC bearer (`Authorization` / `?token=`). PUBLIC_ROUTES ~L88.
- SSE `/api/events`: rate-limit exempt; blocks in `engine.wait_for_event(last_seq, timeout=20)`; keepalive comment frame every 20s.
- UI served by reading HTML from disk each request + string substitutions (`__TURNSTILE_SITE_KEY__`, `__DEMO_PREFILL__`, `__CF_ACCESS__`).
- Landing assets: PNG-only with traversal guard.

## ANTI-PATTERNS

- RBAC lives in engine (raises PermissionDenied) — don't duplicate role checks in server routes beyond what exists (audit export, mode/threshold admin gates).
- livedata clients must cache last-good, fall back stale, and surface staleness — never hide a degraded feed.
