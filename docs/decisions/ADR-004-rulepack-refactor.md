# ADR-004 — Rulepack Refactor of ConstraintVerifier (TJ-N2)

**Status**: Accepted · **Scope**: `platform/nexus/safety.py` core + new rulepack modules · **Wave**: 2 (after ADR-001)

## Decision

Factor `ConstraintVerifier` into a domain-agnostic core
(`verify(plan, context, rulepack) -> VerdictReport`) plus declarative rulepacks
expressed as dataclasses — explicitly NO DSL. MUTCD 4D/4E (R1–R5, with
ADR-001's corrected kinematics) becomes rulepack #1. A second rulepack
(MUTCD Ch.6 work zones preferred; WSDOT ramp-meter policy fallback) proves the
core is genuinely general. Public `ConstraintVerifier` behavior unchanged.

## Why (money / investors / completion)

- **Platform valuation unlock**: "verification engine with pluggable
  regulatory rulepacks" is the platform story that survives diligence —
  demonstrated by two working rulepacks, not two identical city adapters.
- **Moat clarification**: the verifier-over-LLM *pattern* is prior art (OPA,
  NeMo Guardrails); the defensible asset is the encoded regulatory corpus as
  executable, tested rules. Rulepacks make that corpus a first-class,
  extensible product surface.
- **Prerequisite**: ADR-005's verification API serves rulepacks; it cannot
  ship without this refactor.

## Rejected alternatives

- Rule DSL / config language: unnecessary abstraction; dataclass rules keep
  type safety and match repo conventions.
- Full stateful verification-as-a-service: R6 reads `_active_changes`, R7 our
  911 feed, H1–H3 our graph/feeds — third-party statefulness is an ontology
  project, not a refactor. Killed for v1.

## Acceptance

`test_rulepacks.py`: identical verdicts pre/post refactor for the same plans;
2nd rulepack rejects an invalid work-zone plan and passes a valid one. All
safety + kinematics tests stay green. No DSL introduced.
