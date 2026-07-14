# Decision Records — Investor Verification Suite

Motivation-first records for each feature shipped in `feat/investor-verification-suite`.
Each ADR documents WHY (market/money/investor case), WHAT (scope), and WHAT WE REJECTED.

Provenance: features selected via adversarial 5-agent planning (market-scout,
biz-realist, tech-judge, vision-artist, deep-diligence; 3 rounds of cross-attack),
July 2026. Market facts verified against primary sources at that date.

| ADR | Feature | Type |
|---|---|---|
| [ADR-001](ADR-001-mutcd-kinematic-correctness.md) | E1 MUTCD kinematic correctness | Enhancement |
| [ADR-002](ADR-002-certificate-compliance-engine.md) | E2 Certificate/Compliance Engine | Enhancement |
| [ADR-003](ADR-003-shadow-evidence-engine.md) | E3 Shadow-Evidence Engine | Enhancement |
| [ADR-004](ADR-004-rulepack-refactor.md) | TJ-N2 Rulepack verifier core | Prerequisite refactor |
| [ADR-005](ADR-005-stateless-verification-api.md) | C1 Stateless Verification API | New feature |
| [ADR-006](ADR-006-ntcip-readonly-bridge.md) | D1 NTCIP 1202 read-only bridge | New feature |
| [ADR-007](ADR-007-readonly-mcp-endpoint.md) | M1 Read-only MCP endpoint | New feature |

## Locked narrative

> "Nexus City OS — the only traffic AI that knows when to stay quiet.
> Every decision verified, certified, and refusable."

Abstention + verification + provenance is the honest, demoable moat today.
"Verified AI actuation" is the roadmap slide (falsifiable in one diligence
question until a controller path exists); D1 is the credible first step.

## Explicitly rejected (do not build)

- Green-wave corridor orchestration — no offset field in SignalTimingPlan; R6 caps
  5 concurrent changes (blocked by our own safety moat); competes head-on with the
  core product of NoTraffic/Miovision/Flow Labs ($165M+ funded incumbents).
- Predictive incident staging — violates the no-mutation-outside-Live invariant;
  no predictive substance behind it.
- Adapter marketplace / registry — platform-before-product (N=2 adapters, both ours).
- Sourcewell listing (as written) — competitive scored RFP, not opt-in; salvage path
  is a reseller partnership (Carahsoft→NASPO, as Flow Labs did Mar 2025).
- Cross-city data network effects — 18+ months early; incident anonymization
  near-impossible (lat/lon+timestamp is identifying).
- Full stateful Verification-as-a-Service — R6 reads our _active_changes, R7 our
  911 feed, H1–H3 our graph/feeds; third-party statefulness is an ontology project.
  Stateless R1–R5 lint survives (ADR-005).
- Probe-data ingestion (TomTom/HERE/NPMRDS) — licensed APIs break the zero-key,
  zero-dependency demo story.
- Confidence calibration/drift monitoring — needs decision volume that doesn't exist
  pre-customers; Series-A feature. (We DO start capturing operator approve/reject
  labels now — near-zero cost, feeds it later.)
- Standalone abstention dashboard — dashboard-ware; folded into explainable refusal
  (ADR-002, ADR-007).
