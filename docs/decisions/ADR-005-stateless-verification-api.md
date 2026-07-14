# ADR-005 — Staged Verification API v1: Stateless MUTCD Lint (C1)

**Status**: Accepted · **Scope**: `platform/nexus/server.py` (`POST /api/v1/verify`) · **Wave**: 3 (after ADR-004)

## Decision

A stateless endpoint running ONLY R1–R5 (timing math) via the ADR-004
core+rulepack against a client-supplied plan. Per-rule explanation JSON;
selectable `rulepack` parameter; malformed body → 400, never crashes. No reads
of `_active_changes`, the 911 feed, or the city graph — honest statelessness.
Staged roadmap: explainability endpoint → report export → third-party
plan-scoring API.

## Why (money / investors / completion)

- **Tax the optimizers, don't fight them**: Google Green Light (20 cities,
  47M rides/mo, advisory-only — Bangkok confirms engineers manually vet every
  suggestion), NoTraffic, Miovision, and Flow Labs all *generate* timing
  changes; none carry an independent safety-verification layer. A neutral
  verification API positions Nexus as infrastructure every optimizer calls —
  classic infra-layer economics, per-verification recurring revenue, no city
  procurement cycle (B2B2G).
- **YC shape**: "AI infrastructure for a regulated physical-world domain" is
  the differentiated pitch vs. YC's existing traffic-optimizer comps
  (XTraffic S24, Roundabout F24).
- **Honesty gate**: full stateful VaaS was killed in adversarial review; a
  stateless MUTCD lint API is what we can truthfully demo today.

## Rejected alternatives

- Full stateful third-party VaaS: requires customers to ship complete
  intersection inventory/timing/EMS state in a schema that doesn't exist;
  R6 is silently unsound without visibility into ALL concurrent changes.
- Competing optimizer (green-wave orchestration): walks into the kill zone of
  $165M-funded incumbents' core product and destroys the neutral-referee
  positioning; also blocked by our own R6 concurrency cap.

## Acceptance

`test_verify_api.py`: valid plan → PASS with per-rule detail; 4.0s yellow at
45 mph → FAIL with explanation; malformed body → 400; statelessness
assertable (no server-state reads). Network-free in-process handler tests.
