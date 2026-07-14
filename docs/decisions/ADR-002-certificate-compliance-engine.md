# ADR-002 — Certificate / Compliance Engine (E2)

**Status**: Accepted · **Scope**: new `platform/nexus/certs.py` over `audit.py` · **Wave**: 2 (after ADR-001)

## Decision

One generator over the existing hash-chained AuditTrail emits a per-plan
HMAC-signed safety certificate (rules run, rulepack version hash, input
snapshot hashes via `hashlib`, verdict, `now_ts()`), appended into the audit
chain. Four print-optimized HTML templates (stdlib string substitution, no
build step): ops after-action report | regulator conformity pack — NIST AI RMF
critical-infrastructure profile (Apr 2026) PRIMARY, EU AI Act Annex IV as
second template | risk-legal provenance export | investor safety case. Plus an
adversarial fuzz harness (stdlib `random`, ~50k mutated plans in CI /
10^6 nightly-optional) asserting 0 unsafe pass-through, wired into CI.

## Why (money / investors / completion)

- **Statutory demand**: EU AI Act Annex III(2) names road traffic explicitly
  as high-risk; binding 2 Dec 2027 (public authorities Aug 2030). Art 9/12/14/15
  requirements map one-to-one onto SafetyGate, AuditTrail, HITL, hallucination
  monitor. Every EU traffic-AI vendor must buy or build this.
- **Funded category**: AI assurance ≈ $560M across 20 deals in 12 months;
  AIUC's $15M seed (Nat Friedman + Anthropic co-founder Ben Mann) prices
  insurance off exactly the audit artifacts this engine exports.
- **Competitive gap**: Miovision ships "Mateo" GenAI with no verification moat;
  no competitor markets a signed per-decision conformity artifact.
- **US buyer today**: city risk managers/attorneys defending tort claims over
  signal timing have no defensible record of AI-influenced decisions.

## Rejected alternatives

- EU-first framing: wrong jurisdiction for a US-only company — EU template is
  one extra output flag, NIST profile is the primary SKU.
- Insurance-led go-to-market (insurable certificates as product): two-sided
  bootstrap, 3+ year cycle; kept as an optionality slide only.
- PDF output: stdlib has no PDF generation; print-optimized HTML matches the
  repo's no-build-step convention.

## Acceptance

`test_certs.py`: HMAC verifies; one flipped byte fails verification; audit
chain still validates; all 4 templates render non-empty HTML with verdict +
rulepack hash. `test_fuzz_harness.py`: 0 unsafe plans pass. New CI job green.
