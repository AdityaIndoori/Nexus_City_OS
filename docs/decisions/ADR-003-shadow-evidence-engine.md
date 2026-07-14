# ADR-003 — Shadow-Evidence Engine (E3)

**Status**: Accepted · **Scope**: new `platform/nexus/evidence.py` reading `store.py` SQLite · **Wave**: 1

## Decision

Counterfactual scorecard: score logged shadow-mode would-be plans post-hoc
against actual congestion trajectories (bus-probe history already in SQLite,
7-day retention). Emits three print-optimized HTML artifacts: a 60-day
"Decision Audit" pilot report, an SS4A/SMART grant application packet, and a
standardized KPI benchmark. Strictly read-only — no mutation path, no
Live-mode involvement. Abstains (no score) when data is insufficient.

## Why (money / investors / completion)

- **The traction machine**: manufactures the exact metric class that raised
  NoTraffic's $90M (Phoenix −70% violations, OKC −24% delays) without touching
  a single controller — the honest bridge from "demo" to "paying pilots",
  which is the number YC asks for.
- **Procurement bypass**: a 60-day Decision Audit priced in the $10–100K
  micro-purchase / informal-quote band lets a city buy on a P-card, skipping
  the 6–24 month RFP cycle that kills smart-city startups (pilot purgatory).
- **Grant channel**: the vendor who fills out SS4A/SMART grant paperwork wins
  the deal (Miovision runs an entire "Grant Guide" program); Flow Labs sells
  Seattle DOT on "defensible evidence to strengthen funding applications" —
  evidence generation is itself a paid use case.

## Rejected alternatives

- Standalone abstention dashboard: dashboard-ware; folded into reports as
  "explainable refusal" counts.
- Multi-city public benchmarking as headline: N=2 cities, both ours —
  "benchmark against whom?"; kept as one output template.
- Probe-data ingestion (NPMRDS/TomTom/HERE): licensed APIs break the
  zero-key/zero-dep demo story; future adapter-SDK slide.

## Acceptance

`test_evidence.py`: seeded temp SQLite yields the expected scorecard delta;
abstains on thin data; all 3 templates render with computed KPIs. Network-free.
