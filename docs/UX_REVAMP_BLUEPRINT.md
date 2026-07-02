# NEXUS CITY OS — UX/UI REVAMP BLUEPRINT
**Design authority:** Principal Product Design / Systems Architecture — mission-critical TMC/TOC interface standard.
**Engineering constraint:** zero-dependency Python stdlib + SQLite backend; single-file vanilla-JS UI; no build step; no asset-heavy animation.
**Grid law:** every dimension is a multiple of 4px. Corner radius: 0. No translucency over data. No glow effects.

---

## 1. INFORMATION ARCHITECTURE & DUAL-STATE WORKSPACE

### 1.1 State model

The operator console has exactly two mutually exclusive right-rail states, driven by a single client variable `selKey`:

| State | Trigger | Content |
|---|---|---|
| **TRIAGE INBOX** (default) | `selKey === null` | Deduplicated, severity-ranked event inbox across all sources (platform incidents, SFD 911, WSDOT/Waze road alerts) |
| **FOCUSED INCIDENT WORKSPACE** | Row click / **Enter** on cursor row | Pinned incident context + AI Copilot Governance block. Inbox is fully replaced — zero scroll competition |

Exit from workspace: **Esc** or **`◀ TRIAGE`**. Selection survives SSE re-renders (keyed by incident id); if the incident is resolved server-side, the console deterministically falls back to TRIAGE.

### 1.2 Alert hierarchy (deduplicated, severity-ranked)

| Tier | Label | Membership rule | Chrome |
|---|---|---|---|
| 1 | **`CRITICAL ACTION`** | Platform incidents in state `detected` (ack required) OR with a `pending_approval` plan OR severity ≥ 0.70; 911 dispatches flagged `traffic_impacting` | 3px International Orange left rule; tier header orange |
| 2 | **`MONITORED ANOMALIES`** | Remaining active platform incidents; non-impacting 911; WSDOT high-priority + Waze reports | 3px Traffic Yellow left rule |
| 3 | **`SYSTEM LOGS`** | Audit chain, platform alerts, feed-health state | Permanently docked in the bottom strip — never competes with the inbox |

Deduplication: each event renders exactly once, in its highest qualifying tier. Sort inside a tier: severity desc, then the operator's active sort selector (`Newest/Oldest`). Time-range / category / page-size controls gate all tiers identically.

### 1.3 Workspace pinning

On selection the right rail re-renders top-down with **no scrolling required for the decision**: identity strip (type / state / severity) → location + camera evidence (frozen detection frame) → AI classification rationale → **Governance block (proposal, signature, actions)**. The action row is placed inside the first viewport of the rail.

---

## 2. VISUAL DIRECTION, COLOR PSYCHOLOGY & TYPOGRAPHY

### 2.1 Functional palette (ANSI Z535 / ISO 3864 aligned, matte)

| Token | Hex | Role — and ONLY this role |
|---|---|---|
| `--bg` | `#121417` | Console charcoal base (12-hour-shift luminance) |
| `--panel` | `#191C20` | Panel surface |
| `--panel2` | `#20242A` | Inset surface / rows |
| `--line` | `#2E333A` | 1px structural rules |
| `--ink` | `#E8EAED` | Primary text |
| `--dim` | `#98A1AB` | Secondary text |
| `--orange` | `#FF4F00` | **International Orange** — critical / danger / unacknowledged / destructive |
| `--yellow` | `#F7B500` | **Traffic Yellow** — caution / degraded / pending |
| `--green` | `#00843D` | **Highway Sign Green** (fill) — safe / approve / nominal |
| `--green-t` | `#3BB273` | Green legibility variant for text on charcoal |
| `--blue` | `#7FA6C9` | Neutral informational (never used for status) |

No purple, no cyan, no gradients, no shadows over data. Status is never conveyed by hue alone — every state carries a text label.

### 2.2 Typography

| Layer | Face | Usage |
|---|---|---|
| Structural | `"Segoe UI", system-ui, sans-serif` | Labels, headers, prose |
| Data | `Consolas, "Cascadia Mono", Menlo, monospace` + `tabular-nums` | **ENFORCED** for all real-time variables: timestamps, ages, coordinates, signal/plan/camera IDs, SHA hashes, percentages, counts, deltas — zero layout shift on tick |

Scale (4px grid): panel headers 12px/700/+1px tracking uppercase; body 13px; triage severity figure 18px mono; KPI figures 20px mono; wall-readable severity and mode badges sized for 15-ft legibility (high-contrast, ≥18px numerals, uppercase labels).

---

## 3. SCREEN SPECIFICATION — "ACTIVE INCIDENT & COPILOT GOVERNANCE" (DESKTOP)

Console grid: `header 48px` / `main: grid-template-columns: minmax(0,1fr) 484px; grid-template-rows: minmax(0,1fr) 232px; gap 8px; padding 8px`.

| Region | Grid cell | Content |
|---|---|---|
| Header | full width | Identity, `MODE` badge, data-source badge, feed chips, push state, audio toggle |
| Map | col 1, row 1 | Live grid (Leaflet, desaturated industrial basemap), layer legend |
| Right rail | col 2, rows 1–2 | TRIAGE ⇄ WORKSPACE dual state |
| Bottom strip | col 1, row 2 | `SYSTEM STATUS` (KPI/analytics/alerts tabs) · `COPILOT QUERY` · `SYSTEM LOG — AUDIT CHAIN` |

### Workspace rail, row-by-row (484px wide, 8px gutters)

| Row | Height | Content |
|---|---|---|
| W0 | 32px | **`◀ TRIAGE`** (Esc) · incident id `mono` right-aligned |
| W1 | 40px | `INCIDENT TYPE` 15px/700 uppercase · state tag · detection source tag |
| W2 | 32px | Severity: 64px mono figure-block + linear meter · detected timestamp `mono` |
| W3 | 28px | 📍 location line · **`FOCUS MAP (SPACE)`** |
| W4 | auto | Frozen detection frame (camera evidence) + `RE-CHECK CAMERA LIVE` |
| W5 | auto | AI classification rationale (collapsed `details`) |
| W6 | auto | **GOVERNANCE BLOCK** (below) |
| W7 | auto | Hash-chained incident timeline (collapsed) |

### Governance block — cold binary engineering comparison

```
AI MITIGATION PROPOSAL            CONF 87%   claude-sonnet-4-6
┌──────────────────────────────────────────────────────────┐
│ SIGNAL              PHASE   CURRENT CYCLE   Δ GREEN      │
│ INT-004 5th & Pine  P2      90 s            +12 s        │
│ INT-007 6th & Pine  P2      90 s            +8 s         │
└──────────────────────────────────────────────────────────┘
DRY-RUN  3↑ improved  0↓ worsened  clear ~11 min  0 transit
─ CRYPTOGRAPHIC SIGNATURE ─────────────────────────────────
 PLAN    plan-2026-000482
 SHA256  a3f91c0e6b…  (full hash, mono, selectable)
 MODEL   heuristic-v2+sonnet-4.6
───────────────────────────────────────────────────────────
[ ✓ APPROVE & BROADCAST  CTRL+ENTER ]  [ ✕ REJECT & DISMISS  CTRL+BKSP ]
```

Action targets: 40px tall, full-width pair, physical-switch styling — solid Highway Green fill (`#00843D`, white text) vs charcoal field with International Orange border/text. Blocked plans render the `⛔ BLOCKED` state with the SafetyGate reason in place of the action row.

---

## 4. MOBILE & BREAKPOINT OPTIMIZATION (→ 380px)

| Breakpoint | Behavior |
|---|---|
| ≤ 1100px | Rail narrows to 420px; bottom strip 2-col |
| ≤ 900px | Vertical stack; map fixed 46vh; strip stacks |
| ≤ 640px — **FIELD ACTION MODE** | Map, legend, bottom strip, copilot **removed from flow**. The rail becomes a full-viewport, text-and-button-only **`APPROVAL / REJECTION ACTION QUEUE`**: tier headers + rows + inline `[APPROVE]` / `[REJECT]` 44px touch targets on rows carrying pending plans. Workspace remains reachable for evidence review |
| ≤ 380px | Single-column rows; controls wrap; 44px targets preserved; no horizontal scroll |

Degradation is structural (CSS `display:none` on secondary regions) — zero additional JS, zero additional requests.

---

## 5. CONTROL ROOM AFFORDANCES & STATE FEEDBACK

### 5.1 Keyboard acceleration

| Key | Action |
|---|---|
| **J / K** | Cursor down / up through the triage inbox (auto-scroll, visible cursor bar) |
| **Enter** | Open cursor row in the Focused Workspace |
| **Space** | Focus map camera/marker for cursor or selected item |
| **Esc** | Workspace → Triage (or exit map fullscreen) |
| **Ctrl+Enter** | `APPROVE & BROADCAST` (selected incident's plan, else first pending) |
| **Ctrl+Backspace** | `REJECT & DISMISS` |
| **Ctrl+Shift+R** | Revert executed plan |
| **Ctrl+Shift+K** | Acknowledge |

Keys are inert while focus is in a text field.

### 5.2 Acknowledge-required alerting

Any `detected` incident with severity ≥ 0.70 arms the **critical channel**: a fixed, pointer-transparent viewport frame (`4px` International Orange inset border) pulses at 1Hz via CSS opacity animation, and a **440Hz sine double-pulse** (Web Audio oscillator, 120ms, −12dB, 4s period — zero assets) sounds until acknowledged. Audio arms on first user gesture (browser policy) and carries a header mute toggle persisted per station.

### 5.3 Deterministic layout states

Every panel loads as a **grid-preserving skeleton** — static charcoal blocks matching the final row geometry with a subtle opacity pulse. No spinners, no reflow on data arrival.

### 5.4 Write-Lock transition

Approve / Reject / Rollback / Ack enter **WRITE-LOCK**: the governance action row is replaced in-place by a locked banner — `⛿ WRITE-LOCK · COMMITTING TO HASH-CHAINED AUDIT TRAIL…` (mono, yellow rule) — all inputs in the block disabled until the API settles and the SSE-driven re-render confirms the new state. Failure restores the actions and raises a toast with the server reason.