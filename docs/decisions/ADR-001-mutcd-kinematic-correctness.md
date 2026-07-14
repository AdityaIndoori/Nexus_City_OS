# ADR-001 — MUTCD Kinematic Engineering-Correctness (E1)

**Status**: Accepted · **Scope**: `platform/nexus/safety.py`, `platform/nexus/models.py` · **Wave**: 1

## Decision

Replace the fixed 3–6s yellow-interval band check with the ITE kinematic
yellow-change formula `y = t + v / (2a ± 2Gg)` (t = 1.0s reaction, a = 3.05 m/s²,
g = 9.81 m/s², grade G default 0). Compute a true flashing-don't-walk (FDW)
pedestrian change interval from crosswalk length at 3.5 ft/s walk speed
(MUTCD 4E). Replace the degenerate R4 conflict check with a ring-and-barrier
phase model over `conflicts_with`. Add optional `grade_pct: float = 0.0` to
`SignalPhase` (backward compatible, no city branch).

## Why (money / investors / completion)

- **Diligence survival**: any traffic P.E. hired for VC technical DD checks
  yellow-clearance math first. A fixed 4.0s yellow that ignores approach speed
  and grade converts "safety-verified AI" into an unsubstantiated slide. This
  is the single load-bearing prerequisite for the entire "verified" story.
- **Category pricing**: NoTraffic raised $90M Series C (PSG, Mar 2026; $165M
  total) on demonstrable safety outcomes (Phoenix −70% red-light violations,
  OKC −24% delays). To sit in that category the verifier must be
  engineering-correct, not plausible.
- **Compounding**: ADR-002 (certificates) and ADR-005 (verification API) both
  certify/serve these rules — their value is zero if the rules fail expert review.

## Rejected alternatives

- Keep the fixed band and disclaim it: dies in the first technical meeting.
- Full HCM capacity modeling: out of scope; verification, not optimization.

## Acceptance

`test_safety_kinematics.py`: yellow at 45 mph on +3% grade ≥ yellow at 25 mph
flat; a 4.0s yellow at 45 mph FAILS; FDW for 60 ft crosswalk ≥ ceil(60/3.5)s;
ring-and-barrier rejects conflicting simultaneous greens. All 163 existing
tests stay green. Stdlib only, network-free.
