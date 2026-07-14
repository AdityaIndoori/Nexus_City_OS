# ADR-006 — NTCIP 1202 Read-Only Controller Bridge (D1)

**Status**: Accepted · **Scope**: new `platform/nexus/ntcip.py` · **Wave**: 1

## Decision

~300 lines of pure-stdlib socket code: BER encoder + SNMPv2c GET over UDP
polling controller phase/timing OIDs, plus
`verify_observed(expected_plan, observed) -> report` comparing observed vs
expected timing — "verification before actuation". Observation only:
`controller_bridge()` still returns `None` for mutation; no Live-mode mutation
path is added. Honest framing in the module docstring: prototype NTCIP read
bridge for a joint city-DOT pilot (vendor-specific MIB OIDs, SNMPv2c/v3, needs
city network access — not a v1 product feature). Tests mock at the socket
layer, same pattern as `livedata._fetch_json`.

## Why (money / investors / completion)

- **Closes the #1 diligence hole**: today every `controller_bridge()` returns
  `None` and Live mode mutates only the in-memory graph. "Verified AI
  actuation" is falsifiable in one DD question — "show me the controller
  path". A working read bridge is the credible first rung of that ladder
  without taking on actuation liability.
- **Field-time moat**: hardware field experience is the moat incumbents have
  and software-only entrants lack; a read bridge earns it at minimal risk.

## Rejected alternatives

- Write-path NTCIP actuation: violates the no-mutation-outside-Live invariant
  and takes on liability with zero deployed calibration history.
- Framing as production feature: MIB OIDs are vendor-specific
  (Econolite/Siemens/McCain/Intelight differ) and access needs city network
  whitelisting — overselling it invites the exact DD attack it exists to
  deflect.

## Acceptance

`test_ntcip.py`: BER encoder round-trips a hand-verified SNMP GET fixture;
mocked socket with canned phase states yields correct observed-vs-expected
verdict; unreachable socket degrades gracefully (no raise). Network-free.
