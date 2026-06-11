# Product Requirements Document: Nexus City OS (v2)

> **Revision Note:** This is v2 of the PRD, revised to address 74 issues identified in the formal PRD review (see `PRD_REVIEW.md`). All changes from v1 are marked with `[v2]` inline. Sections that are entirely new are marked `[NEW SECTION]`.

## Product Summary
* **Product Name:** Nexus City OS
* **One-liner:** A unified platform that connects fragmented municipal transit data, computer vision, and AI agents to drive real-time, high-stakes traffic mitigation decisions.
* **MVP Scope:** Support one high-value operational workflow end-to-end: "Detect a major arterial blockage (e.g., multi-vehicle collision) and — upon operator approval — recommend and execute city-wide traffic signal timing mitigations." `[v2: Removed "autonomously" to align with HITL non-goal. Removed "transit rerouting" — deferred to Phase 2. Clarified "signal timing mitigations" as the specific capability.]`

### MVP Scope Variants `[v2]`
The MVP deliverable depends on the outcome of the signal controller investigation (Appendix A):
* **Live Mode MVP:** The platform detects incidents, generates AI recommendations, and — upon operator approval — pushes signal timing changes directly to traffic signal controllers.
* **Advisory Mode MVP:** If direct signal controller integration is infeasible, the platform generates recommendations and displays formatted instructions that operators relay to field technicians or enter into existing signal management systems manually.

Both variants must deliver: unified situational awareness, AI-powered analysis, decision workflows, and full audit capability. Success metrics are defined separately for each variant (see Success Metrics).

### MVP Definition of Done `[NEW SECTION]`
The MVP is considered complete when ALL of the following are satisfied:
- [ ] All MVP features (Sections 1-13) are deployed and operational in Shadow Mode
- [ ] Penetration test and load test passed prior to Shadow Mode start (Section 7.4 — required for all variants)
- [ ] Shadow Mode burn-in period (minimum 30 calendar days) is completed
- [ ] Shadow Mode acceptance criteria (Section 7.1) are met
- [ ] Legal and liability review (Section 11.4) is completed with written sign-off
- [ ] Operator training and certification (Section 11.5) is completed for all TOC staff
- [ ] Signal controller investigation (Appendix A) is resolved — Live Mode or Advisory Mode decision is finalized
- [ ] If Live Mode: TOC Manager and Admin sign-off obtained for Shadow-to-Live transition
- [ ] If Advisory Mode: instruction format validated with field technicians; TOC Manager sign-off obtained

## Goals and Non-Goals

### Goals
* Enable a single "mission thread" where operators can view live camera feeds, run AI-powered traffic flow analysis, and alter signal timings in one environment.
* Demonstrate the safe deployment of AI models directly connected to live municipal infrastructure, with humans always in the approval loop.
* Provide a governed city model (a live "digital twin" of the city grid) that all municipal departments can eventually share.

### Non-Goals
* Replace all existing city IT infrastructure or proprietary traffic light controllers.
* Support utilities (water, power, waste management) from day one.
* Fully automate traffic control without a human-in-the-loop (HITL). `[v2: Confirmed — consistent with MVP scope language.]`
* Serve Emergency Dispatcher workflows in MVP (see Phase 2 Preview).
* Transit rerouting (bus detours, rail service adjustments) — deferred to Phase 2. `[v2: Explicitly added to non-goals to prevent scope confusion.]`

## Users and Use Cases

### Primary Users (MVP)
* **Traffic Operations Center (TOC) Operators:** Analysts who monitor the city grid, manage congestion, and respond to incidents. This is the primary persona for the MVP.

### Supporting Users (MVP) `[v2: Reclassified from "Primary" to "Supporting"]`
* **Data Engineers (Municipal IT):** Engineers who maintain the data pipelines connecting edge sensors to the core city model. Served by the staging environment (Section 7.3) and pipeline monitoring dashboard (Section 1.2).
* **TOC Manager:** `[v2: NEW persona]` Oversees TOC operations, authorizes Shadow-to-Live transition, reviews system performance reports, and makes escalation decisions. Served by performance dashboards and the Shadow Mode acceptance workflow.
* **Field Technicians (Signal Maintenance):** `[v2: NEW persona]` Execute manual signal timing changes when directed by TOC Operators (in Advisory Mode or during degraded operations). Receive formatted instructions from the platform. Confirm execution status back to operators via radio/phone.
* **Platform Administrator / DevOps:** `[v2: NEW persona]` Manages platform deployment, infrastructure, monitoring, and incident response. Operates monitoring dashboards, manages upgrades, and responds to platform outages.

### Phase 2 Users
* **Emergency Dispatchers (911/Fire/EMS):** Operators who need immediate, clear routing paths for emergency vehicles through gridlocked areas. Dispatchers are deferred to Phase 2 to maintain MVP focus on the TOC operator workflow (see Phase 2 Preview at the end of this document).

### Representative MVP Use Case `[v2: Replaced I-5 with city-managed arterials]`
"Within seconds of a severe multi-vehicle collision at the intersection of 4th Ave and Pike St, the platform surfaces the regional impact on the Downtown Seattle grid, proposes alternate signal timing plans for parallel arterials (like 2nd Ave and 6th Ave), and tracks the clearing of the bottleneck through execution. If I-5 camera feeds are available through WSDOT data sharing agreements, the platform can detect highway incidents for situational awareness, but signal control is limited to city-managed infrastructure."

## Problem Statement
Municipal operations teams currently face:
* **Fragmented Data:** Live traffic camera feeds, public transit data, and road sensor metrics live in isolated silos and cannot be analyzed coherently under time pressure.
* **Reactive, Not Proactive Systems:** Current tools report that a traffic jam *has* happened, rather than dynamically re-routing a city *while* it is happening.
* **Lack of Unified Simulation:** There is no single environment that combines live geospatial mapping, relationship-based routing, and AI analysis to simulate the cascading effects of a closed intersection.
* **No Rollback Safety Net:** When a manual signal timing change worsens congestion, there is no systematic way to quickly revert to the prior state and measure the impact.

## Product Principles
* **Operations-First:** Build backwards from the field workflow of a traffic controller managing a crisis.
* **Model-Driven:** The city is a living graph. Everything is defined on a shared model of nodes (Intersections, Cameras, Buses) and edges (Roads, Speed Limits, Current Flow). The model schema must be extensible to support future entity types (utilities, public works) without breaking changes. `[v2: Added extensibility requirement]`
* **Secure by Default:** Fine-grained access controls with mandatory multi-factor authentication, ensuring only authorized personnel can alter physical city infrastructure. `[v2: Added MFA reference]`
* **AI in the Loop:** Computer vision extracts the data; AI suggests the routing changes; Humans remain strictly responsible for final approvals. The system recommends; humans approve; the system executes approved actions. `[v2: Reinforced consistent language]`
* **Graceful Degradation:** The platform must remain useful even when individual subsystems are unavailable. Operators must never be left without situational awareness.
* **Provable Safety:** Every AI recommendation must be traceable to specific data, bounded by physical constraints (including MUTCD compliance), and independently verifiable before reaching an operator. `[v2: Added MUTCD reference]`

---

## MVP Feature Set

### 1. Data Integration and City Model

#### 1.1 Data Sources `[v2: Fixed count from "3-5" to explicit list of 4]`
Ingest the following 4 municipal data sources in real time:
* **Live traffic camera feeds** from the City of Seattle Department of Transportation (SDOT). Access requires existing municipal data sharing agreement (not publicly available — agreement status must be confirmed before Sprint 1). `[v2: Clarified access requirement]`
* **Public transit GPS telemetry** via GTFS-RT (General Transit Feed Specification — Real Time) feeds from King County Metro and Sound Transit. `[v2: Specified standard and providers]`
* **Roadwork and street closure schedules** from SDOT open data portal.
* **Weather conditions and alerts** from the National Weather Service (NWS) API. Weather data informs AI confidence scoring and is displayed as a context layer on the Live Grid map. `[v2: NEW data source]`

#### 1.2 City Model
* Maintain a unified city model that maps the physical world into a relationship graph. Core entities include: `Intersection`, `RoadSegment`, `TransitVehicle`, `Incident`, `SignalTimingPlan`, and `WeatherCondition`. `[v2: Added WeatherCondition entity]`
* This model acts as the real-time engine calculating the interconnected dependencies of every moving piece of transit across the city grid.
* **Extensibility requirement:** `[v2]` The entity/relationship schema must support the addition of new entity types (utilities, public works assets) in Phase 2 without schema-breaking changes.
* **Coverage tracking:** `[v2]` Each intersection in the city model is classified as "monitored" (has camera coverage) or "unmonitored" (inferred data only). AI confidence scoring is adjusted downward for recommendations involving unmonitored intersections.

#### 1.3 Pipeline Monitoring Dashboard `[v2: NEW — serves Data Engineer persona]`
* A dedicated view for Data Engineers showing: data pipeline health (up/down/degraded), ingestion rates per source, latency metrics vs. thresholds, error rates, and recent pipeline events.
* Alerts Data Engineers when any pipeline exceeds freshness thresholds or encounters errors.

#### Data Freshness Requirements
Each data source must meet the following latency thresholds from point of capture to availability in the platform:
* **Camera feeds:** < 5 seconds end-to-end. Minimum frame rate: 5 FPS per camera. Minimum resolution: 720p. `[v2: Added throughput requirements]`
* **Transit vehicle GPS:** < 15 seconds.
* **Roadwork and closure schedules:** Updated at minimum every 15 minutes.
* **Weather data:** Updated at minimum every 10 minutes; severe weather alerts within 60 seconds of NWS publication. `[v2: NEW]`
* Feeds exceeding these thresholds are automatically flagged with a visual staleness indicator (amber for approaching threshold, red for exceeded). Stale data is excluded from AI recommendations.

#### Data Throughput Requirements `[v2: NEW]`
* **Camera feeds:** 50 concurrent feeds × 5 FPS × 720p = architecture must support ~250 frames/second aggregate ingestion and processing.
* **City model updates:** The graph database must support ≥ 1,000 entity updates per second and ≥ 500 concurrent read queries with P95 latency < 100ms.
* **Backup and recovery:** City model data is replicated with RPO ≤ 5 minutes and RTO ≤ 15 minutes. Automated daily backups with 30-day retention. `[v2: NEW DR requirements]`

#### Data Retention Policies `[v2: NEW]`
| Data Type | Hot Storage | Warm Storage | Cold/Archive | Total Retention |
|---|---|---|---|---|
| Processed camera frames (redacted) | 7 days | 30 days | 1 year | 1 year |
| Transit GPS telemetry | 30 days | 90 days | 2 years | 2 years |
| Congestion metrics | 30 days | 1 year | 7 years | 7 years |
| City model snapshots | 7 days (hourly) | 90 days (daily) | 2 years (weekly) | 2 years |
| Audit logs | 1 year | 7 years | — | 7 years (see Section 11.3) |

### 2. Situational Awareness and Visualization
* Unified geospatial search and visualization over the integrated dataset.
* Key views for operators:
  * **The "Live Grid" Map:** A unified UI showing active camera feeds, live transit vehicle locations, current speed telemetry, and weather conditions overlaid on the street map. Intersections are visually distinguished as "monitored" vs. "unmonitored." `[v2: Added weather layer and coverage indicators]`
  * **Dependency Graph:** A visual web showing cascading impacts (e.g., "If the intersection at 4th & Pike is blocked, these 14 connected intersections will reach gridlock in 8 minutes"). `[v2: Fixed example to use city-managed intersection]`

#### Incident Mode UX
When a significant incident is detected (automatically or flagged by an operator), the UI transitions into **Incident Mode**.

**Incident significance threshold:** `[v2: NEW — defines "significant"]` An incident triggers Incident Mode when ANY of the following criteria are met:
* Anomaly detection flags a multi-vehicle collision, wrong-way driver, or pedestrian on highway
* Estimated delay impact exceeds 5 minutes for ≥ 3 connected intersections
* Operator manually flags an incident as "significant"
* A workflow rule with Incident Mode trigger fires

**Incident Mode behavior:**
* The map automatically centers on the affected area and zooms to show the impact radius.
* Relevant camera feeds are surfaced in a priority panel — no manual searching required.
* The AI-recommended mitigation plan is presented prominently alongside the projected ripple effects.
* Normal monitoring clutter (non-critical alerts, routine status updates) is suppressed to reduce cognitive load.
* **Countdown timers** display estimated time-to-gridlock for each affected intersection.
* **Action history sidebar** shows a running, timestamped log of all actions taken during the current incident for shared situational awareness across the TOC team.

**Incident Mode exit:** `[v2: NEW]`
* **Manual exit:** Operator clicks "Close Incident" which prompts for a resolution status (Resolved, False Alarm, Handed Off) and optional notes. This is logged in the audit trail.
* **Auto-suggestion:** When congestion metrics for all affected intersections return to within 10% of pre-incident baseline for ≥ 10 minutes, the system suggests closing the incident but does not auto-close.
* **Split view:** While in Incident Mode, operators can access a mini-map of the full grid in a corner panel to maintain broader situational awareness.

**Multi-incident handling:** `[v2: NEW]`
* When multiple incidents are active simultaneously, an **Incident Queue** panel displays all active incidents ranked by severity (estimated delay impact).
* Operators can switch between incident contexts. Each incident maintains its own action history, mitigation state, and countdown timers.
* The Dependency Graph highlights overlapping impact zones when two incidents affect shared intersections.
* The concurrent change limit (Section 6.3) applies system-wide across all active incidents. `[v2: Clarified scope of change limit]`

#### Incident Lifecycle `[v2: NEW SECTION]`
Every incident follows a formal lifecycle with the following states:
1. **Detected** — Anomaly detection or workflow rule fires. Incident is created with auto-assigned severity.
2. **Acknowledged** — Operator acknowledges the incident and takes ownership. Required within 2 minutes of detection (escalation alert if unacknowledged).
3. **Mitigating** — Active signal timing changes are in progress. AI recommendations are being generated and reviewed.
4. **Monitoring** — Mitigation actions have been executed. System is monitoring impact and watching for conditions to stabilize.
5. **Resolved** — Operator closes the incident with resolution status. All active timing changes are either confirmed as permanent or reverted.
6. **Closed** — Post-incident review is complete. Incident is archived with full action history.

State transitions, timestamps, and responsible operators are logged in the audit trail. Incidents that remain in "Detected" for > 2 minutes trigger escalation to the TOC Manager.

#### Shift Handoff `[v2: NEW]`
For incidents spanning shift changes:
* The outgoing operator initiates a **Shift Handoff** action, which generates an auto-summary of: incident state, active mitigations, pending recommendations, and key decisions made.
* The incoming operator must **Acknowledge Handoff** to take ownership. Until acknowledged, the outgoing operator remains the responsible party.
* The handoff event is logged in the audit trail with both operator identities.

### 3. AI-Powered Analysis and Recommendations
* **Automated Anomaly Detection:** Continuously parse live traffic camera feeds to automatically flag anomalies (stopped vehicles, pedestrians on highways, wrong-way drivers).
  * **Model requirements:** `[v2: NEW]`
    * Target detection accuracy: ≥ 95% for multi-vehicle collisions, ≥ 90% for stopped vehicles, ≥ 85% for wrong-way drivers.
    * Maximum false positive rate: ≤ 5% across all anomaly types. False positive rate is measured weekly; if exceeded, model is flagged for retraining.
    * Validation must cover edge cases: low-light conditions, rain/snow, camera angle variations, and partial occlusion.
    * Training dataset requirements: minimum 10,000 labeled frames per anomaly type, sourced from or representative of the Downtown Seattle deployment area.
* **AI Copilot:** Anchored strictly to the municipal city model (see AI Grounding and Safety Architecture below).
  * Can answer complex queries: *"Which rapid ride bus routes are currently delayed by the Mercer street closure, and what is the nearest viable detour?"*
  * Generates candidate mitigation plans as structured actions (e.g., "Increase green-light duration on 4th Ave by 15 seconds").
  * **Weather awareness:** `[v2]` AI recommendations factor in current weather conditions. During adverse weather (rain, ice, snow), the AI adjusts recommendations to account for increased stopping distances and reduced throughput capacity.

### 4. AI Grounding and Safety Architecture

This section defines how the AI Copilot is constrained to prevent hallucinated, unsafe, or untraceable recommendations. Given that the platform connects AI directly to physical city infrastructure, this architecture is a safety-critical requirement.

#### 4.1 Tool-Calling Agent Pattern
The AI Copilot operates exclusively through a **structured tool-calling interface**. It does not generate free-form infrastructure commands. Instead, it can only invoke pre-validated, schema-checked action functions. Examples:
* `adjust_signal_timing(intersection_id, phase, duration_delta_seconds)` — with hard min/max bounds enforced per MUTCD Chapter 4D requirements (see Section 4.4). `[v2: Referenced MUTCD]`
* `query_city_model(entity_type, filters)` — read-only queries against the city model.
* `simulate_impact(proposed_changes)` — run a simulation of proposed changes before recommending (see Section 7.2 for simulation engine specification).

Any AI output that does not conform to a validated action schema is **blocked** and logged as an anomaly.

**Adversarial input protections:** `[v2: NEW]`
* All operator queries to the AI Copilot are sanitized before processing. Known prompt injection patterns are detected and blocked.
* AI Copilot interactions are rate-limited to 30 queries per operator per 5-minute window.
* Anomalous query patterns (repeated injection attempts, queries referencing entities outside the city model) are logged and flagged for security review.
* The AI interface is included in annual penetration testing scope (see Section 7.4).

#### 4.2 Mandatory Provenance and Citation
Every AI recommendation must include:
* The specific city model entities (intersections, road segments, incidents) that informed the recommendation.
* The data sources and their timestamps used in the analysis.
* A human-readable rationale explaining *why* this action is suggested.
* Current weather conditions at time of recommendation. `[v2]`

Recommendations without complete provenance are **automatically suppressed** and never shown to operators.

#### 4.3 Confidence Scoring and Abstention
* The AI Copilot outputs a confidence score (0–100%) with every recommendation.

**Confidence score calculation:** `[v2: NEW — previously undefined]`
The confidence score is a weighted composite of:
* **Model certainty (40%):** The AI model's internal probability estimate for the recommended action.
* **Data freshness (25%):** Percentage of relevant data sources within their freshness thresholds. Stale data reduces this component proportionally.
* **Coverage completeness (20%):** Percentage of affected intersections that have direct sensor coverage (cameras, GPS). Unmonitored intersections reduce this component.
* **Historical accuracy (15%):** Rolling 30-day accuracy rate for similar recommendation types (same anomaly type, similar time of day, similar weather conditions).

The composite score must be **calibrated** during Shadow Mode: a 70% confidence score should be correct approximately 70% of the time (±5%). Calibration is validated monthly.

* Recommendations below a configurable confidence threshold (default: 70%) are **withheld** from the operator. Instead, the system displays: *"Insufficient data confidence to recommend an action. Manual assessment recommended."*
* **Governed threshold range:** `[v2: Previously undefined]` Operators can adjust the confidence threshold within the range of **50% to 95%**. Only the Admin role can adjust the threshold. All threshold changes are logged in the audit trail with justification.

#### 4.4 Physical Constraint Verification (MUTCD Compliance) `[v2: Added MUTCD specifics]`
Before any AI-generated recommendation reaches the operator's screen, a **secondary validation layer** independently verifies the proposed action against hard physical constraints derived from the **Manual on Uniform Traffic Control Devices (MUTCD), Chapter 4D** and applicable SDOT standards:

* **Minimum green intervals:** Per MUTCD Section 4D.26, minimum green time based on approach speed and intersection geometry. Minimum 7 seconds for through movements; 4 seconds for left-turn phases.
* **Pedestrian intervals:** Per MUTCD Section 4E.06, pedestrian walk interval ≥ 7 seconds; pedestrian clearance interval calculated from crosswalk length and 3.5 ft/s walking speed (adjusted to 3.0 ft/s at intersections near senior centers or schools).
* **Yellow change intervals:** Per MUTCD Section 4D.26 and ITE standards, calculated from approach speed (minimum 3.0 seconds, maximum 6.0 seconds).
* **Red clearance intervals:** Per MUTCD Section 4D.26, calculated from intersection width and approach speed.
* **Conflicting signal phases:** Green on two conflicting approaches simultaneously is detected and blocked.
* **Maximum cycle lengths and phase durations:** Cycle length 60–180 seconds; individual phase duration 10–120 seconds.
* **Concurrent change limit:** No single intersection receives more than one concurrent timing change.

Any recommendation that fails physical constraint verification is **blocked**, logged, and reported as a safety violation.

#### 4.5 Hallucination Monitoring
* The system continuously monitors for patterns that indicate hallucination: recommendations citing non-existent intersections, referencing data outside the valid time window, or proposing actions on entities not in the city model.
* **Hallucination block rate** (recommendations blocked for hallucination / total recommendations generated) is tracked separately from the physical constraint block rate. `[v2: Separated metrics]`
  * Target: hallucination block rate < 1%.
  * Target: physical constraint block rate < 1%.
  * Combined AI safety block rate < 2% (this is the Success Metric — see below).
* The 1% individual targets will be validated during Shadow Mode. If either target is not met, the remediation plan is: model retraining with expanded training data, confidence threshold increase, or scope reduction to fewer intersection types.

#### 4.6 Model Lifecycle Management `[v2: NEW SECTION]`
* **Model selection:** The AI Copilot LLM and computer vision models will be selected based on: accuracy on domain-specific benchmarks, latency requirements (< 3 seconds for recommendation generation), and municipal data residency compliance.
* **Versioning:** All models are versioned with semantic versioning (MAJOR.MINOR.PATCH). The active model version is recorded in every audit log entry.
* **Update process:** Model updates follow a staged rollout:
  1. New model version is deployed to the staging environment for regression testing.
  2. If regression tests pass, the new model runs in **Shadow Mode** alongside the production model for ≥ 7 days, with recommendation quality compared.
  3. If the new model meets or exceeds production model quality, it is promoted to production with Admin approval.
  4. The previous model version is retained for ≥ 30 days for rollback capability.
* **Rollback:** If a production model degrades (block rate increases, confidence scores drop, operator acceptance rate decreases), the Admin can roll back to the previous version with one action.

### 5. Decision Workflows and Approvals
* **Workflow Canvas:** For defining automated triggers (e.g., `IF CollisionDetected AND Severity > High → Alert Operator + Propose Signal Change`). `[v2: Changed "Propose Reroute" to "Propose Signal Change" for scope consistency]`
  * **Rule authoring permissions:** Analysts can create and test draft rules. Only Operators (or Admins) can promote rules to "live" status.
  * **Testing requirement:** All rules must be validated in Shadow Mode (see Testing & Simulation) before going live in production.
  * **Versioning:** Every rule change is version-controlled with full change history. Any rule version can be rolled back.
  * **Rule language specification:** `[v2: NEW]` Rules are authored via a visual drag-and-drop interface (not code). Available trigger conditions: `CollisionDetected`, `StoppedVehicle`, `WrongWayDriver`, `PedestrianOnHighway`, `CongestionThresholdExceeded(intersection, threshold%)`, `DataFeedStale(source)`, `WeatherAlert(severity)`. Available actions: `AlertOperator`, `ProposeSignalChange(intersection, parameters)`, `EnterIncidentMode`, `NotifyTOCManager`, `LogEvent`.
  * **Default rule library:** The platform ships with the following pre-configured rules: `[v2: Enumerated]`
    1. Multi-vehicle collision detected → Alert Operator + Enter Incident Mode + Propose Signal Changes for adjacent intersections
    2. Wrong-way driver detected → Alert Operator + Enter Incident Mode + Alert TOC Manager
    3. Congestion exceeds 80% at ≥ 3 connected intersections → Alert Operator + Propose Signal Changes
    4. Highway on-ramp closure detected → Alert Operator + Propose Signal Changes for parallel arterials
    5. Transit vehicle breakdown blocking lane → Alert Operator + Propose Signal Changes for affected intersection
    6. Severe weather alert received → Alert all Operators + Increase confidence threshold by 10%
    7. Data feed stale for > 2× threshold → Alert Data Engineer + Notify Operator of reduced coverage
* **Approval Flows (Human-in-the-Loop):**
  1. AI generates a draft action to change traffic light timings (as a structured, schema-validated proposal with provenance and confidence score).
  2. TOC Operator reviews the proposed ripple effects via the Dependency Graph visualization, edits parameters if necessary, and approves via "Approve" button or keyboard shortcut (`Ctrl+Enter`). `[v2: Added keyboard shortcut]`
  3. The platform pushes the approved action downstream to the physical traffic signal controllers (Live Mode) or displays a formatted instruction (Advisory Mode). `[v2: Added Advisory Mode path]`
  4. Post-approval, the system monitors the impact in real time and alerts the operator if conditions worsen (see Rollback and Reversion).

#### Advisory Mode Instruction Format `[v2: NEW]`
When operating in Advisory Mode, approved actions are displayed as formatted instructions containing:
* **Intersection ID and name** (e.g., "INT-0142: 4th Ave & Pike St")
* **Current timing plan** (phase durations, cycle length)
* **Requested change** (specific parameter adjustments, e.g., "Increase Phase 2 green from 30s to 45s")
* **Priority level** (Urgent / Standard)
* **Expiration time** (instruction is valid for 15 minutes; after that, conditions may have changed)
* **Confirmation protocol:** Operator marks instruction as "Relayed" when communicated to field technician. Field technician confirmation is recorded when the operator marks it "Executed" or "Unable to Execute" (with reason).

Instructions are formatted for readability on screen and printable as a one-page summary for radio relay.

### 6. Rollback and Reversion

Every signal timing change executed through the platform must be reversible. This section defines the rollback mechanisms.

#### 6.1 Manual Rollback
* **One-click revert:** For any active timing change, operators can click "Revert to Previous Plan" or use keyboard shortcut (`Ctrl+Shift+R`) to instantly restore the prior signal timing state for the affected intersection(s). `[v2.1: Changed from Ctrl+Z — see shortcut design note in Section 9.3]`
* The reversion is itself logged as an action in the audit trail with full before/after state.

#### 6.2 Automatic Rollback Monitoring
* After any timing change is executed, the platform continuously monitors the affected area's congestion metrics (speed, queue length, throughput).
* **Auto-revert trigger:** If monitored congestion metrics worsen by ≥ 20% (configurable) within 5 minutes (configurable) of a change execution, the system:
  1. Alerts the operator with a prominent "Conditions Worsening" notification and an audible alarm. `[v2: Added audible alarm]`
  2. Proposes an automatic reversion to the previous timing plan.
  3. If configured for auto-revert (opt-in, requires Admin approval to enable globally `[v2: Clarified granularity — global setting]`), executes the reversion automatically and notifies the operator.

#### 6.3 Change Limits
* **Maximum concurrent active changes:** No more than 5 signal timing modifications may be active **system-wide** simultaneously (configurable by Admin). This limit applies across all operators and all active incidents. `[v2: Clarified scope — system-wide]` This prevents cascading complexity and ensures each change's impact can be independently assessed.
* Requests beyond this limit are queued and the operator is notified with the current queue position and estimated wait time.

### 7. Testing, Simulation, and Shadow Mode

Given that the platform controls physical infrastructure, rigorous pre-deployment testing is a product requirement, not an afterthought.

#### 7.1 Shadow Mode (Mandatory for MVP Launch)
* The platform must operate in **Shadow Mode** for a **minimum of 30 calendar days** before any live signal control is enabled. `[v2: Defined specific burn-in period]`
* In Shadow Mode:
  * All data pipelines, anomaly detection, AI recommendations, and workflow triggers operate normally.
  * Operators interact with the platform as if it were live (reviewing recommendations, clicking "Approve").
  * **No changes are pushed to physical signal controllers.** All "approved" actions are logged but not executed.
* Shadow Mode builds operator trust, validates AI recommendation quality, and surfaces integration issues without risk.

**Shadow Mode Acceptance Criteria:** `[v2: NEW — previously undefined]`
The decision to transition from Shadow Mode to Live Mode requires ALL of the following:
1. Minimum 30 calendar days of continuous Shadow Mode operation completed.
2. Minimum 20 real incidents processed through the full workflow (detection → recommendation → operator review).
3. AI recommendation accuracy: ≥ 90% of recommendations confirmed appropriate in blind review (see Success Metrics for methodology).
4. Hallucination block rate: < 1% during the burn-in period.
5. Physical constraint block rate: < 1% during the burn-in period.
6. All operator training and certification completed (Section 11.5).
7. Legal and liability review completed with written sign-off (Section 11.4).
8. Penetration test completed with no critical or high-severity findings unresolved (Section 7.4).
9. Load test passed at 2× MVP scale targets (Section 7.4).
10. Zero critical safety violations during the burn-in period.
11. Explicit written sign-off from the TOC Manager and the platform Admin.

#### 7.2 Dry-Run Impact Simulation
* Before any timing change is approved (in both Shadow and Live modes), the platform runs a **projected impact simulation** on the Dependency Graph.

**Simulation Engine Specification:** `[v2: NEW — previously unspecified]`
* **Modeling approach:** Mesoscopic traffic simulation using the cell transmission model (CTM) for computational efficiency while maintaining intersection-level accuracy.
* **Inputs:** Current city model state (intersection occupancy, queue lengths, signal timing), proposed changes, weather conditions, time of day.
* **Validation:** Simulation predictions are compared against actual outcomes during Shadow Mode. Target: predicted congestion change is within ±15% of actual observed change for ≥ 80% of recommendations.
* **Performance:** Simulation must complete within 5 seconds for changes affecting up to 20 intersections.
* **Third-party option:** If custom simulation development exceeds timeline, integration with SUMO (Simulation of Urban Mobility) via its TraCI API is an acceptable alternative. This decision must be made by Sprint 3.

* The simulation displays:
  * Estimated congestion changes for each affected intersection (better/worse/neutral, with percentage).
  * Projected time to clear the incident.
  * Number of transit routes affected and estimated delay impact.
* This gives operators a clear picture of expected outcomes before committing to an action.

#### 7.3 Staging Environment
* A non-production staging environment is maintained for Data Engineers to:
  * Test new data source integrations.
  * Validate pipeline changes.
  * Run regression tests against the city model.
* The staging environment uses anonymized or synthetic data and is never connected to live signal controllers.
* **Synthetic data requirements:** `[v2: NEW]` Synthetic data must replicate production-scale volume (50 cameras, 200 intersections, 500 transit vehicles), include at least 50 realistic incident scenarios covering all anomaly types, and cover edge cases (rush hour, adverse weather, special events). Synthetic datasets are validated by comparing statistical distributions against historical production data.

#### 7.4 Security and Performance Testing `[v2: NEW SECTION]`
* **Penetration testing:** An independent security firm must conduct a penetration test before Shadow Mode begins. Scope includes: platform APIs, AI Copilot interface (including prompt injection attempts), RBAC enforcement, network boundaries, and signal controller integration layer. Critical and high-severity findings must be remediated before Shadow Mode. Retesting annually.
* **Load testing:** The platform must be load-tested at 2× MVP scale targets (100 camera feeds, 400 intersections, 1,000 transit vehicles, 20 concurrent operator sessions) before Shadow Mode. P95 response times must remain within performance targets (see Section 12) under sustained load.
* **Disaster recovery testing:** Failover and data recovery procedures must be tested quarterly. Tests must verify RPO (≤ 5 minutes) and RTO (≤ 15 minutes) targets are achievable.

### 8. Degraded Operations and Fallback Modes

The platform is used during high-stakes situations where failure is not an option. This section defines what operators see and can do when individual subsystems are unavailable.

**Core principle:** The platform never presents stale data as fresh, never hides failures, and always tells operators what they *can* still do.

| Subsystem Failure | Operator Experience | Available Actions | Automatic System Response |
|---|---|---|---|
| **AI/Copilot pipeline down** | Prominent "AI Unavailable" banner across the top of the screen. Audible alert. `[v2]` Live Grid map and camera feeds continue to function normally. | Operators retain full manual observation capability. Signal changes can be relayed via existing out-of-band tools (phone, radio — see SOP reference panel `[v2]`). | Alert dispatched to Data Engineering on-call. AI availability metric logged. |
| **Degraded AI quality** `[v2: NEW row]` | "AI Accuracy Degraded" amber banner. Confidence threshold auto-increased to 85%. | Operators can still receive high-confidence recommendations. Lower-confidence recommendations are suppressed until quality recovers. | Triggered when block rate exceeds 5% over rolling 1-hour window OR average confidence score drops below 50%. Alert to Data Engineering. |
| **One or more data feeds stale** | Affected data layers show amber (approaching threshold) or red (exceeded) staleness indicators. Stale feeds are visually dimmed on the map. | Operators can still view all other feeds. AI recommendations automatically exclude stale data and indicate reduced confidence. | Stale feeds are removed from AI analysis. Freshness violation logged. |
| **City model / graph database unreachable** | Dependency Graph view displays "Unavailable." Live Grid map degrades to basic overlay (cameras + GPS dots without relationship analysis). | Operators can monitor camera feeds and transit positions directly. No AI recommendations are generated. | System enters read-only observation mode. Full alert to Data Engineering. |
| **Signal controller API unreachable** | "Execution Blocked" badge on all approval actions. Operators can still review and "approve" recommendations (logged for when connectivity is restored). | Operators relay urgent changes via phone/radio to field technicians (see SOP reference panel `[v2]`). | All pending approved-but-unexecuted actions are queued with a **15-minute expiration** `[v2]`. Full audit trail maintained. |
| **Complete platform outage** | Platform unavailable. | Operators fall back entirely to existing legacy tools and standard operating procedures. | Automated notification sent via **independent external watchdog service** `[v2]` to all registered TOC operators and Data Engineering via SMS/email. |

**SOP Reference Panel:** `[v2: NEW]` During any degraded mode, a collapsible panel displays relevant contact information (field technician radio channels, TOC Manager phone, Data Engineering on-call) and links to applicable Standard Operating Procedures.

**Recovery behavior:** When a subsystem recovers, the platform automatically resumes normal operation. For queued signal changes:
* Queued actions that have **not expired** (< 15 minutes old) are re-presented to the operator for re-confirmation before execution. The operator must re-assess current conditions before re-approving. `[v2: Added expiration and re-assessment]`
* Queued actions that have **expired** (≥ 15 minutes old) are logged as "Expired — Not Executed" and are not re-presented.
* The platform logs the outage duration and impact.

### 9. Notification and Alerting `[v2: NEW SECTION — previously absent]`

The platform must provide proactive notifications beyond in-UI indicators to ensure operators are alerted even when not actively monitoring the screen.

#### 9.1 Notification Channels
* **In-UI alerts:** Banners, badges, Incident Mode transitions, countdown timers (as described in Sections 2 and 8).
* **Audible alarms:** Configurable alarm sounds for: new incident detection (high priority), conditions worsening after a timing change, unacknowledged incident escalation. Alarm volume and tone are configurable per operator workstation.
* **Desktop push notifications:** For operators logged in but viewing other applications. Requires browser notification permission.
* **SMS/email notifications:** For critical events only: complete platform outage, unacknowledged incident escalation (> 2 minutes), and Shadow-to-Live transition authorization requests. Sent via an independent notification service hosted separately from the main platform.

#### 9.2 Escalation Rules
* **Unacknowledged incident:** If an incident remains in "Detected" state for > 2 minutes, escalate to TOC Manager via SMS and audible alarm at all operator workstations.
* **Unacknowledged worsening alert:** If a "Conditions Worsening" alert is not acknowledged within 1 minute, escalate to TOC Manager.
* **Critical platform event:** Any critical subsystem failure triggers immediate SMS/email to TOC Manager and Data Engineering on-call.

#### 9.3 Keyboard Shortcuts `[v2: NEW]`
Critical actions have keyboard shortcuts for rapid response:
| Action | Shortcut |
|---|---|
| Approve recommendation | `Ctrl+Enter` |
| Reject recommendation | `Ctrl+Backspace` |
| Revert last timing change | `Ctrl+Shift+R` |
| Acknowledge incident | `Ctrl+Shift+K` |
| Switch to next incident in queue | `Ctrl+→` |
| Toggle Incident Mode split view | `Ctrl+M` |
| Open SOP reference panel | `F1` |

> **Shortcut design note:** `[v2.1]` Shortcuts deliberately avoid overloading universal editing
> semantics (`Ctrl+Z` undo, `Ctrl+A` select-all) — accidental activation of a signal reversion or
> incident acknowledgment via muscle memory is a safety hazard. Destructive/critical shortcuts
> require a `Shift` chord and present a 2-second confirmation toast that can be cancelled with `Esc`.

### 10. Deployment and Operations
* Packaged for deployment within environments that comply with municipal cloud and security restrictions.
* **Data residency:** `[v2: NEW]` All platform data must reside within the United States. Specific state or city residency requirements to be confirmed with the city's IT security office before Sprint 1. If the city requires data to remain within Washington State, architecture must accommodate this constraint.
* The system must support the mandatory Shadow Mode period before any live signal control is enabled.
* Monitoring dashboards track: platform uptime, data feed freshness, AI recommendation quality (acceptance rate, block rate, hallucination rate), and signal controller connectivity.

---

### 11. Security, Governance, and Compliance `[v2: Expanded significantly]`

#### 11.1 Authentication and Identity Management `[v2: NEW — previously absent]`
* **Multi-factor authentication (MFA):** Mandatory for all Operator and Admin roles. MFA must use a second factor that is not SMS-only (hardware token, authenticator app, or smart card).
* **Single Sign-On (SSO):** Integration with the city's identity provider via SAML 2.0 or OpenID Connect (OIDC). The specific identity provider (e.g., Azure AD, Okta) to be confirmed with Municipal IT before Sprint 1.
* **Session management:**
  * Operator and Admin sessions: 15-minute inactivity timeout. Active sessions extend automatically while the user is interacting.
  * Analyst and Viewer sessions: 30-minute inactivity timeout.
  * Maximum concurrent sessions per user: 1 for Operator and Admin roles (prevents conflicting approvals). New login terminates the existing session with notification.
  * Admin can forcibly terminate any user session.
  * **Shift handoff session transfer:** When performing a shift handoff (Section 2), the outgoing operator's session context (active incident views, open panels) is transferred to the incoming operator's new session.

#### 11.2 Role-Based Access Control (RBAC)
* **Viewer** — Read-only access to public-facing dashboards and anonymized data feeds. Suitable for public APIs, news media.
* **Analyst** — Can run simulations, query the AI Copilot, and author draft workflow rules. Cannot approve physical infrastructure changes.
* **Operator** — Can approve AI-recommended signal timing changes, initiate manual rollbacks, promote workflow rules to live status, and manage incidents. This is the core TOC operator role. Requires completion of training certification (Section 11.5) before role is granted. `[v2: Added training prerequisite]`
* **Admin** — Can manage user accounts and role assignments, configure system settings (confidence thresholds, auto-revert policies, change limits), manage workflow rules at a system level, authorize the transition from Shadow Mode to Live Mode, and roll back AI model versions.
* All role assignments, changes, and permission escalations are logged in the audit trail.

**Break-glass emergency escalation:** `[v2: NEW]`
* In the event that no authorized Operator is available during a critical incident, a designated Analyst can request temporary Operator permissions.
* **Procedure:** Analyst initiates break-glass request → requires confirmation from one other Analyst AND phone/SMS confirmation from an Admin → temporary Operator permissions granted for a maximum of 2 hours → prominently logged in audit trail with "BREAK-GLASS" flag → automatic permission revocation after 2 hours (or earlier by Admin).
* Break-glass events trigger immediate SMS notification to the TOC Manager and all Admins.

#### 11.3 Audit Trail
* **Scope:** Every action in the system is logged: AI recommendations generated (with model version `[v2]`), AI recommendations blocked (with reason), operator approvals, operator edits to recommendations, signal changes executed, rollbacks triggered, workflow rule changes, role assignments, break-glass escalations `[v2]`, system configuration changes, incident lifecycle transitions `[v2]`, and shift handoffs `[v2]`.
* **Immutability:** Audit logs are append-only and tamper-evident. No user (including Admin) can delete or modify audit entries.
* **Retention:** 7-year retention period, aligned with municipal records retention requirements. Confirmation with the city's legal and records management office is a Pre-Sprint 1 investigation item (see Constraints — item 5); 7 years is the working requirement for architecture sizing until confirmed. `[v2.1: Reconciled with Pre-Sprint 1 list — previously stated as already confirmed]`
* **Storage estimate:** `[v2: NEW]` Based on MVP scale targets, estimated annual audit log volume is ~50-100 GB. Storage architecture: hot tier (< 1 year, SSD-backed, full query capability), cold tier (1-7 years, object storage, queryable within 4 hours for legal discovery).
* **Format and export:** Logs are stored in a structured, machine-readable format and exportable for legal discovery, regulatory review, and compliance audits.
* **Content per entry:** Timestamp, actor identity (human user ID or "AI Copilot"), AI model version `[v2]`, action type, target entities, before-state, after-state, data sources consulted, approval chain, and outcome.

#### 11.4 Liability and Legal Framework
* A legal and liability review **must be completed** before the platform's signal controller integration goes live (i.e., before transitioning out of Shadow Mode).
* **Owner:** City Attorney's Office in coordination with the platform vendor's legal team. `[v2: Assigned owner]`
* **Deadline:** Must be completed before the Shadow Mode burn-in period ends (i.e., within the first 30 days of Shadow Mode operation). `[v2: Set deadline]`
* **Deliverables:** `[v2: NEW]`
  1. Written legal opinion on municipal liability for AI-assisted signal timing decisions.
  2. Indemnification agreement between the city and the platform vendor.
  3. Insurance confirmation (professional liability and cyber insurance adequate for infrastructure control).
  4. Determination on operator certification requirements for liability purposes.
* The platform is designed and positioned as a **decision-support tool**, not an autonomous controller. The human operator bears final approval responsibility for all physical infrastructure changes.
* All AI recommendations are explicitly labeled as "AI Suggestion" in both the UI and the audit trail to maintain clear attribution.

#### 11.5 Operator Training and Certification `[v2: NEW SECTION]`
* **Training requirement:** All users must complete role-appropriate training before receiving their RBAC role. Operator role is **locked** in the system until training certification is recorded.
* **Training content by role:**
  * **Viewer/Analyst:** Platform overview, data interpretation, simulation tools (2 hours).
  * **Operator:** Full platform operation, AI recommendation interpretation and evaluation, approval workflows, rollback procedures, Incident Mode operation, shift handoff protocol, emergency procedures, degraded operations SOPs (8 hours + 4 hours supervised practice in Shadow Mode).
  * **Admin:** All Operator content + system configuration, RBAC management, Shadow/Live Mode transition, model lifecycle management (12 hours).
* **Certification:** Written assessment (≥ 80% pass score) + practical assessment (complete a simulated incident response scenario). Certification is valid for 12 months; annual recertification required.
* **Training records:** Certification status, completion dates, and assessment scores are stored in the platform and linked to RBAC role assignments.

#### 11.6 Privacy and Data Protection
* **Redaction standard:** Faces and license plates in video feeds must be redacted with ≥ 99% detection accuracy before any video data enters the core platform.
* **Point of capture clarification:** `[v2: NEW]` "Point of capture" means redaction occurs on the **edge processing device** co-located with the camera, before the video stream is transmitted to the cloud platform. Raw video never leaves the edge device.
* **Raw data prohibition:** Raw, unredacted video is never stored on or transmitted to the cloud platform.
* **Compliance:** Redaction practices must comply with applicable local privacy ordinances (e.g., Seattle Municipal Code Chapter 14.18).
* **Auditing:** Redaction effectiveness is audited quarterly with a sample-based review. `[v2: Added methodology]`
  * **Methodology:** Random sample of 1,000 frames per camera per quarter. Manual review by trained reviewer. Results reported as detection rate per camera.
  * **Remediation:** If any camera's redaction rate falls below 99%, that camera's feed is suspended from the platform until the redaction system is recalibrated. Frames with detected redaction failures are automatically deleted from platform storage.
  * **Breach protocol:** If unredacted PII is discovered in platform storage, the incident is reported to the city's privacy officer within 24 hours, affected data is deleted, and a root cause analysis is conducted.

#### 11.7 Network Security `[v2: NEW SECTION]`
* **Encryption in transit:** TLS 1.3 minimum for all communications between platform components, data sources, operator browsers, and signal controller interfaces.
* **Encryption at rest:** AES-256 for all stored data including audit logs, city model data, and processed camera frames.
* **Network segmentation:** The platform operates in a segmented network architecture:
  * **Data ingestion zone:** Receives camera feeds, transit GPS, weather data. Isolated from signal control zone.
  * **Application zone:** Hosts the platform core, AI services, city model. Can read from data ingestion zone and write to signal control zone (via approval gateway only).
  * **Signal control zone:** Communicates with physical signal controllers. Accessible only from the application zone via the approval gateway. No direct internet access.
  * **Operator access zone:** Operator browsers connect via VPN or the city's internal network. No direct access to signal control zone.
* **Compliance:** Architecture must comply with NIST SP 800-82 (Guide to ICS Security) recommendations for IT/OT boundary protection.
* **API security:** All platform APIs require authentication, use rate limiting, and are versioned with semantic versioning. Deprecated API versions receive 6-month notice before removal. `[v2: Added API versioning]`

### 12. Performance Requirements `[v2: NEW SECTION]`

#### 12.1 UI Performance Targets
| Interaction | P95 Target |
|---|---|
| Live Grid map initial render | < 3 seconds |
| Live Grid map update (new data) | < 1 second |
| Incident Mode transition | < 1 second |
| Dependency Graph render (up to 20 intersections) | < 2 seconds |
| AI recommendation display (after generation) | < 3 seconds |
| Approval to execution (Live Mode) | < 5 seconds |
| Search query results | < 2 seconds |

#### 12.2 Platform Availability Targets `[v2: NEW]`
| Component | Availability Target | Maximum Planned Downtime |
|---|---|---|
| Core platform (Live Grid, camera feeds) | 99.9% (8.7 hours/year) | 2 hours/month maintenance window (Sunday 2-4 AM) |
| AI Copilot | 99.5% (43.8 hours/year) | Included in maintenance window |
| Signal controller integration | 99.9% | Zero planned downtime (rolling updates) |
| Audit trail | 99.99% | Zero planned downtime |

#### 12.3 Scalability Targets `[v2: NEW]`
* **MVP scale:** 50 cameras, 200 intersections, 500 transit vehicles, 10 concurrent operators.
* **Phase 2 target scale:** 500 cameras, 1,000 intersections, 2,000 transit vehicles, 50 concurrent operators.
* **Architectural requirement:** MVP architecture must use stateless application services, horizontally scalable data ingestion, and partitioned data storage to support Phase 2 scale without fundamental redesign.

### 13. Accessibility `[v2: NEW SECTION]`
All platform interfaces must comply with **WCAG 2.1 Level AA** and **Section 508** requirements. Specific attention areas:
* **Color contrast:** All staleness indicators (amber/red), incident severity colors, and status badges must meet WCAG AA contrast ratios (4.5:1 for normal text, 3:1 for large text). Information must never be conveyed by color alone — use icons and text labels alongside color.
* **Keyboard navigability:** All platform functions must be accessible via keyboard (see Section 9.3 for critical shortcuts). Focus indicators must be clearly visible.
* **Screen reader compatibility:** All UI elements must have appropriate ARIA labels. Map elements must have text alternatives. Camera feeds must have associated text descriptions of detected conditions.
* **Text scaling:** UI must remain functional at 200% text zoom.
* **Validation:** Accessibility compliance is tested as part of the QA process for every release. Annual third-party accessibility audit.

---

## Success Metrics (MVP) `[v2: Revised significantly]`

### Baselines `[v2: NEW]`
Before the Shadow Mode burn-in period begins, the following baselines must be measured using current tools and processes (without the platform):
* **Current time-to-mitigation:** Median time from incident report to first manual signal adjustment, measured over 30 days of current operations.
* **Current incident acknowledgment time:** Median time from incident occurrence to TOC awareness.
* All MVP metrics are reported as both absolute values and improvement deltas vs. baseline.

### Live Mode Metrics
* **Time-to-Mitigation:** Median time from **incident detection by the platform** to the first approved signal timing adjustment is `< 3 minutes`. `[v2: Changed from "physical incident occurrence" (unknowable) to "incident detection" (measurable)]`
* **Adoption:** ≥ 90% of major incident responses during the pilot phase utilize the platform as the primary coordination tool. `[v2: Changed from "100% of operators" (binary/unrealistic) to usage-based metric]`
* **Quality/Efficacy:** ≥ 85% of AI-suggested signal timing changes are accepted by human operators without manual parameter overrides.
* **Effectiveness:** `[v2: NEW]` ≥ 70% of accepted AI recommendations result in measurable congestion improvement (≥ 10% reduction in average delay) at affected intersections within 10 minutes of execution.
* **AI Safety:** Combined rate of AI recommendations blocked by physical constraint verification or hallucination detection is < 2%. (Tracked separately: hallucination block rate < 1%, physical constraint block rate < 1%.) `[v2: Clarified combined vs. individual targets]`
* **Shadow Mode Validation:** During the Shadow Mode period, ≥ 90% of AI recommendations are retrospectively confirmed as appropriate by TOC operators in blind review. `[v2: Added methodology below]`
  * **Blind review methodology:** Random sample of 50 recommendations per week, stratified by confidence score quartile. Reviewed by operators who were not the original responders. Blinded to AI confidence score. Each recommendation rated: Appropriate / Inappropriate / Insufficient Data to Judge. "Insufficient Data" ratings are excluded from the percentage calculation.
* **Rollback Frequency:** < 10% of executed signal timing changes require rollback within the monitoring window.

### Advisory Mode Metrics `[v2: NEW — separate metrics for Advisory Mode]`
If the MVP operates in Advisory Mode, the following metrics replace Time-to-Mitigation and Effectiveness:
* **Time-to-Instruction:** Median time from incident detection to formatted instruction displayed to operator is `< 2 minutes`.
* **Instruction Relay Rate:** ≥ 95% of approved instructions are marked "Relayed" to field technicians within 3 minutes of approval.
* **Execution Confirmation Rate:** ≥ 80% of relayed instructions receive "Executed" confirmation from field technicians.
* All other metrics (Adoption, Quality/Efficacy, AI Safety, Shadow Mode Validation, Rollback Frequency) apply identically.

### Platform Reliability Metrics `[v2: NEW]`
* **Platform uptime:** ≥ 99.9% for core platform (Live Grid, camera feeds); ≥ 99.5% for AI Copilot.
* **Data feed availability:** ≥ 95% of data feeds meeting freshness thresholds at any given time.
* **P95 UI response time:** All operator-facing interactions meet their per-interaction P95 targets defined in Section 12.1 (ranging from < 1 second for map updates to < 5 seconds for approval-to-execution). `[v2.1: Removed blanket "< 2 seconds for all" which contradicted the Section 12.1 table]`

### Incident Management Metrics `[v2: NEW]`
* **Incident acknowledgment time:** ≥ 95% of detected incidents acknowledged by an operator within 2 minutes.
* **Incident resolution rate:** 100% of incidents reach "Resolved" or "Closed" state (no orphaned incidents).
* **Mean time to resolution (MTTR):** Tracked and reported; no target set for MVP (baseline establishment).

## Constraints and Assumptions `[v2: Revised]`
* The MVP relies on existing municipal data sources accessible through city data sharing agreements or public APIs. Camera feeds require SDOT data sharing agreement (status to be confirmed before Sprint 1). `[v2: Clarified "publicly available" assumption]`
* Initial deployment focuses exclusively on a constrained geographic bounding box: the Downtown Seattle traffic grid, limited to **city-managed (SDOT) intersections and signals only**. State-managed infrastructure (I-5, SR-99) is monitored for situational awareness only if data sharing agreements permit, but is not controllable. `[v2: Clarified jurisdictional scope]`
* All video feeds must have personally identifiable information redacted at the edge processing device before entering the platform, in compliance with local privacy ordinances.
* **MVP scale targets:** Up to 50 concurrent camera feeds, 200 intersections (approximately 50 monitored, 150 unmonitored `[v2: Acknowledged coverage gap]`), 500 tracked transit vehicles, and 10 concurrent operator sessions. These targets define the initial bounding box and architecture requirements.
* The platform will operate in **Shadow Mode** (no live signal control) for a minimum of **30 calendar days** `[v2: Quantified]` before any live integration is authorized.
* Direct signal controller integration may not be feasible for MVP. If the pre-development investigation (see Appendix A) determines that direct integration is blocked, the platform will operate in **Advisory Mode** — generating recommendations that operators execute manually through existing systems.
* **Accessibility:** All UI must comply with WCAG 2.1 AA and Section 508 (see Section 13). `[v2: NEW constraint]`
* **Data residency:** All data must reside within the United States; Washington State residency requirement to be confirmed with city IT security before Sprint 1. `[v2: NEW constraint]`

### Pre-Sprint 1 Investigation Items `[v2: NEW — consolidated]`
The following must be resolved before Sprint 1 begins:
1. Signal controller integration feasibility (Appendix A) — determines Live Mode vs. Advisory Mode
2. SDOT camera feed data sharing agreement status
3. City identity provider (SSO) selection and integration requirements
4. Data residency requirements (US vs. Washington State)
5. Audit log retention period confirmation with city legal (currently specified as 7 years)
6. Municipal network security policy review (for NIST 800-82 compliance)

---

## Appendix A: Signal Controller Integration (Investigation Spike)

The MVP's "execute" step — pushing approved timing changes to physical signal controllers — depends on an integration layer that is not yet defined. This appendix outlines the investigation required before development begins.

### What Must Be Determined
1. **Controller protocol inventory:** Which signal controller communication protocols are in use within the Downtown Seattle bounding box? Likely candidates include NTCIP 1202 (v02, v03, or the forthcoming v04 with SNMPv3), proprietary SCATS protocols, or vendor-specific interfaces (e.g., Econolite, Siemens, McCain).
2. **Central management system:** Does the city operate a central signal management system (e.g., SCATS master, Econolite Centracs, Siemens TACTICS) that provides an existing API layer? If so, integrating at the management system level (rather than directly with individual controllers) is strongly preferred.
3. **Access and authorization:** What municipal approvals, network access, and security certifications are required to connect a third-party platform to the signal control network?
4. **Interoperability constraints:** Are there NTCIP compliance requirements that the platform must meet? (NTCIP 1202 defines standardized object definitions for actuated traffic signal controllers, enabling interoperability across manufacturers.)
5. **Latency requirements:** What is the maximum acceptable latency from operator approval to signal change execution? This informs whether real-time API integration or batch-based approaches are viable.
6. **MUTCD compliance verification:** How does the existing signal management system enforce MUTCD constraints? Does the platform need to independently verify, or can it rely on the downstream system's constraint enforcement? `[v2: NEW investigation item]`

### Deadline
This investigation must be completed **before Sprint 1** of MVP development begins. The results will determine whether the MVP includes live signal control or operates in Advisory Mode.

### Fallback: Advisory Mode
If direct signal controller integration proves infeasible for the MVP timeline (due to protocol complexity, municipal approval timelines, or security requirements), the platform will operate in **Advisory Mode**:
* All AI recommendations and operator approvals are generated and logged normally.
* Instead of pushing changes to controllers, the platform displays the approved action as a **formatted instruction** (see Section 5, Advisory Mode Instruction Format) that the operator relays to field technicians or enters into the existing signal management system manually. `[v2: Referenced instruction format specification]`
* Advisory Mode still delivers value by unifying situational awareness, AI analysis, and decision workflows — even without the final automation step.
* **Advisory Mode success metrics** are defined separately (see Success Metrics — Advisory Mode Metrics). `[v2: Referenced separate metrics]`

---

## Appendix B: Phase 2 Preview

The following capabilities are planned for Phase 2, after the MVP has been validated in production.

### Emergency Dispatcher Integration
* **Emergency Green Wave Request:** Dispatchers can request a "green wave" corridor for an emergency vehicle route. The platform calculates the optimal signal timing sequence along the route and presents it for TOC Operator approval (or auto-approval for pre-authorized emergency classes).
* **Dispatcher Dashboard:** A simplified, read-only view showing the current state of the traffic grid and any active incidents, optimized for the dispatcher's workflow (route status, estimated travel times, active blockages).
* **CAD System Integration:** Integration with the city's Computer-Aided Dispatch (CAD) system to automatically ingest emergency vehicle dispatch events and surface them in the platform.

### Transit Rerouting `[v2: Moved from MVP scope to Phase 2]`
* **Bus route detour recommendations:** AI-generated detour suggestions for transit routes affected by incidents, presented to transit agency dispatchers for approval.
* **Integration with transit dispatch:** Push approved detour instructions to King County Metro and Sound Transit dispatch systems.
* **Passenger notification:** Trigger real-time service alerts to transit riders via GTFS-RT service alerts feed.

### Additional Phase 2 Capabilities
* **Multi-agency shared ontology:** Extend the city model to be accessible by additional municipal departments (public works, utilities) as a shared "digital twin."
* **Predictive congestion modeling:** Move from reactive incident response to proactive congestion prediction using historical patterns, event calendars (city permits, stadium schedules), and weather forecasts. `[v2: Added event calendar reference]`
* **Public-facing transit impact dashboard:** An anonymized, read-only view for the public showing major incidents and estimated transit delays.
* **Historical incident analytics and reporting:** Searchable incident history, trend analysis, and customizable reports for TOC Managers and city leadership. `[v2: NEW — deferred from MVP]`

---

## Appendix C: Change Log `[v2: NEW]`

| Version | Date | Changes |
|---|---|---|
| v1 | — | Initial PRD |
| v2 | — | Comprehensive revision addressing 74 issues from formal PRD review. Key changes: fixed MVP scope (removed "autonomously", removed transit rerouting), added new sections (authentication, network security, MUTCD compliance, training, performance, SLAs, incident lifecycle, notifications, model lifecycle, accessibility, Advisory Mode metrics), resolved 5 internal contradictions, clarified 10 ambiguities, added 4 new user personas, specified simulation engine, defined Shadow Mode acceptance criteria (30-day burn-in), and added concrete performance/availability targets. Project constraints (budget, timeline, staffing) remain tracked in the separate project plan. See `PRD_REVIEW.md` for full issue list. |
| v2.1 | — | Consistency pass: corrected cross-references (legal review 11.3→11.4, audit retention 11.2→11.3), reconciled DoD with Section 7.4 (pen/load test required before Shadow Mode for all variants), fixed DoD feature range (1-10→1-13), removed contradictory blanket "<2s all interactions" reliability metric, reconciled audit retention confirmation status with Pre-Sprint 1 list, replaced hazardous `Ctrl+Z`/`Ctrl+A` shortcuts with `Shift`-chorded equivalents, corrected changelog claim about project constraints section. Informed by `MARKET_RESEARCH.md`; reference implementation in `platform/`. |
