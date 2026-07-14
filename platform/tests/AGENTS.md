# platform/tests/AGENTS.md

## OVERVIEW

unittest-only suite (163 tests, 14 files), zero network, zero pip; run `python -m unittest discover -s platform/tests -t platform -v`.

## WHERE TO LOOK

| Concern | File |
|---|---|
| Mode ladder, HITL, rollback, RBAC | test_engine.py |
| MUTCD R1–R7, H1–H4, abstention | test_safety.py |
| Speed probes / congestion math | test_congestion.py |
| CF Access JWT (pure-Python RSA keygen, seeded) | test_cfaccess.py |
| Store durability, auth, lockout | test_production.py |
| Feed parsers (pure data, no sys.path) | test_datafeeds.py |
| Rate limit, body cap, Turnstile | test_security.py |
| Adapter SDK proof | test_tacoma.py |

## CONVENTIONS

- Import boilerplate (all except test_datafeeds/test_security): `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))` then `from nexus import ...`.
- Canonical factory: `bootstrap(SeattleAdapter(seed=42))` → (engine, edge, adapter), quiescent. Drive manually: `edge.inject_scenario()` / `edge.tick()` / `engine.recommend()`. **Never start the background tick loop.** LLM tests: `use_llm=True` then mock.
- Helpers are module-level factories (`make_platform()`, `detect_incident()`, `make_plan()`, ...), not setUp. setUp only for per-class shared resources (`Store(":memory:")` + Authenticator).
- Network isolation, pick one: stub class on `adapter.live`; `mock.patch("nexus.livedata._fetch_json")`; `mock.patch.object(engine.copilot.llm, "chat", side_effect=LLMUnavailable)`; direct attr monkey-patch with finally-restore.
- Store: `:memory:` default; file-based only for durability tests — prefix `_t_`, clean up `-wal`/`-shm` in finally.
- Naming: `test_<domain>.py` / `Test<Concept>` / `test_<what_and_outcome>` (e.g. `test_shadow_mode_logs_but_never_executes`); fakes PascalCase.

## ANTI-PATTERNS

- No real HTTP, no API keys, no live endpoints — CI runs fully offline.
- platform/scripts/ test_*/verify_*/probe_* need a live server or network → never wire into CI or unittest discovery.
