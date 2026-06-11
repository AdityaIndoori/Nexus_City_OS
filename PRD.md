# Product Requirements Document: Nexus City OS

## Product Summary
* **Product Name:** Nexus City OS
* **One-liner:** A unified platform that connects fragmented municipal transit data, computer vision, and AI agents to drive real-time, high-stakes traffic mitigation and emergency routing decisions.
* **MVP Scope:** Support one high-value operational workflow end-to-end: "Detect a major arterial blockage (e.g., multi-vehicle collision) and autonomously recommend and execute city-wide traffic signal mitigations and transit rerouting."

## Goals and Non-Goals

### Goals
* Enable a single "mission thread" where operators can view live camera feeds, run AI-powered traffic flow analysis, and alter signal timings in one environment.
* Demonstrate the safe deployment of AI models directly connected to live municipal infrastructure, with humans always in the approval loop.
* Provide a governed city model (a live "digital twin" of the city grid) that all municipal departments can eventually share.

### Non-Goals
* Replace all existing city IT infrastructure or proprietary traffic light controllers.
* Support utilities (water, power, waste management) from day one.
* Fully automate traffic control without a human-in-the-loop (HITL).
* Serve Emergency Dispatcher workflows in MVP (see Phase 2 Preview).

## Users and Use Cases

### Primary Users (MVP)
* **Traffic Operations Center (TOC) Operators:** Analysts who monitor the city grid, manage congestion, and respond to incidents. This is the primary persona for the MVP.
* **Data Engineers (Municipal IT):** Engineers who maintain the data pipelines connecting edge sensors to the core city model.

### Phase 2 Users
* **Emergency Dispatchers (911/Fire/EMS):** Operators who need immediate, clear routing paths for emergency vehicles through gridlocked areas. Dispatchers are deferred to Phase 2 to maintain MVP focus on the TOC operator workflow (see Phase 2 Preview at the end of this document).

### Representative MVP Use Case
"Within seconds of a severe incident on I-5, the platform surfaces the regional impact, proposes alternate signal timing plans for parallel arterials (like Mercer St. or Aurora Ave.), and tracks the clearing of the bottleneck through execution."

## Problem Statement
Municipal operations teams currently face:
* **Fragmented Data:** Live traffic camera feeds, public transit data, and road sensor metrics live in isolated silos and cannot be analyzed coherently under time pressure.
* **Reactive, Not Proactive Systems:** Current tools report that a traffic jam *has* happened, rather than dynamically re-routing a city *while* it is happening.
* **Lack of Unified Simulation:** There is no single environment that combines live geospatial mapping, relationship-based routing, and AI analysis to simulate the cascading effects of a closed intersection.
* **No Rollback Safety Net:** When a manual signal timing change worsens congestion, there is no systematic way to quickly revert to the prior state and measure the impact.

## Product Principles
* **Operations-First:** Build backwards from the field workflow of a traffic controller managing a crisis.
* **Model-Driven:** The city is a living graph. Everything is defined on a shared model of nodes (Intersections, Cameras, Buses) and edges (Roads, Speed Limits, Current Flow).
* **Secure by Default:** Fine-grained access controls ensuring only authorized personnel can alter physical city infrastructure.
* **AI in the Loop:** Computer vision extracts the data; AI suggests the routing changes; Humans remain strictly responsible for final approvals.
* **Graceful Degradation:** The platform must remain useful even when individual subsystems are unavailable. Operators must never be left without situational awareness.
* **Provable Safety:** Every AI recommendation must be traceable to specific data, bounded by physical constraints, and independently verifiable before reaching an operator.

---

## MVP Feature Set

### 1. Data Integration and City Model
* Ingest 3–5 key municipal data sources in real time:
  * **Live traffic camera feeds** from regional transportation authorities.
  * **Public transit GPS telemetry** (real-time bus and rail positions).
  * **Roadwork and street closure schedules** from open municipal data sources.
* Maintain a unified city model that maps the physical world into a relationship graph. Core entities include: `Intersection`, `RoadSegment`, `TransitVehicle`, `Incident`, and `SignalTimingPlan`.
* This model acts as the real-time engine calculating the interconnected dependencies of every moving piece of transit across the city grid.

#### Data Freshness Requirements
Each data source must meet the following latency thresholds from point of capture to availability in the platform:
* **Camera feeds:** < 5 seconds.
* **Transit vehicle GPS:** < 15 seconds.
* **Roadwork and closure schedules:** Updated at minimum every 15 minutes.
* Feeds exceeding these thresholds are automatically flagged with a visual staleness indicator (amber for approaching threshold, red for exceeded). Stale data is excluded from AI recommendations.

### 2. Situational Awareness and Visualization
* Unified geospatial search and visualization over the integrated dataset.
* Key views for operators:
  * **The "Live Grid" Map:** A unified UI showing active camera feeds, live transit vehicle locations, and current speed telemetry overlaid on the street map.
  * **Dependency Graph:** A visual web showing cascading impacts (e.g., "If Exit 167 is blocked, these 14 connected intersections will reach gridlock in 8 minutes").

#### Incident Mode UX
When a significant incident is detected (automatically or flagged by an operator), the UI transitions into **Incident Mode**:
* The map automatically centers on the affected area and zooms to show the impact radius.
* Relevant camera feeds are surfaced in a priority panel — no manual searching required.
* The AI-recommended mitigation plan is presented prominently alongside the projected ripple effects.
* Normal monitoring clutter (non-critical alerts, routine status updates) is suppressed to reduce cognitive load.
* **Countdown timers** display estimated time-to-gridlock for each affected intersection.
* **Action history sidebar** shows a running, timestamped log of all actions taken during the current incident for shared situational awareness across the TOC team.

### 3. AI-Powered Analysis and Recommendations
* **Automated Anomaly Detection:** Continuously parse live traffic camera feeds to automatically flag anomalies (stopped vehicles, pedestrians on highways, wrong-way drivers).
* **AI Copilot:** Anchored strictly to the municipal city model (see AI Grounding and Safety Architecture below).
  * Can answer complex queries: *"Which rapid ride bus routes are currently delayed by the Mercer street closure, and what is the nearest viable detour?"*
  * Generates candidate mitigation plans as structured actions (e.g., "Increase green-light duration on 4th Ave by 15 seconds").

### 4. AI Grounding and Safety Architecture

This section defines how the AI Copilot is constrained to prevent hallucinated, unsafe, or untraceable recommendations. Given that the platform connects AI directly to physical city infrastructure, this architecture is a safety-critical requirement.

#### 4.1 Tool-Calling Agent Pattern
The AI Copilot operates exclusively through a **structured tool-calling interface**. It does not generate free-form infrastructure commands. Instead, it can only invoke pre-validated, schema-checked action functions. Examples:
* `adjust_signal_timing(intersection_id, phase, duration_delta_seconds)` — with hard min/max bounds enforced (e.g., green duration: 10–120 seconds; cycle length: 60–180 seconds).
* `query_city_model(entity_type, filters)` — read-only queries against the city model.
* `simulate_impact(proposed_changes)` — run a simulation of proposed changes before recommending.

Any AI output that does not conform to a validated action schema is **blocked** and logged as an anomaly.

#### 4.2 Mandatory Provenance and Citation
Every AI recommendation must include:
* The specific city model entities (intersections, road segments, incidents) that informed the recommendation.
* The data sources and their timestamps used in the analysis.
* A human-readable rationale explaining *why* this action is suggested.

Recommendations without complete provenance are **automatically suppressed** and never shown to operators.

#### 4.3 Confidence Scoring and Abstention
* The AI Copilot outputs a confidence score (0–100%) with every recommendation.
* Recommendations below a configurable confidence threshold (default: 70%) are **withheld** from the operator. Instead, the system displays: *"Insufficient data confidence to recommend an action. Manual assessment recommended."*
* Operators can adjust the confidence threshold within a governed range.

#### 4.4 Physical Constraint Verification
Before any AI-generated recommendation reaches the operator's screen, a **secondary validation layer** independently verifies the proposed action against hard physical constraints:
* Minimum pedestrian crossing times are preserved.
* Conflicting signal phases (e.g., green on two opposing approaches simultaneously) are detected and blocked.
* Maximum cycle lengths and phase durations conform to traffic engineering standards.
* No single intersection receives more than one concurrent timing change.

Any recommendation that fails physical constraint verification is **blocked**, logged, and reported as a safety violation.

#### 4.5 Hallucination Monitoring
* The system continuously monitors for patterns that indicate hallucination: recommendations citing non-existent intersections, referencing data outside the valid time window, or proposing actions on entities not in the city model.
* A hallucination rate metric (recommendations blocked / total recommendations generated) is tracked and reported. Target: < 2% of generated recommendations are blocked for safety violations.

### 5. Decision Workflows and Approvals
* **Workflow Canvas:** For defining automated triggers (e.g., `IF CollisionDetected AND Severity > High → Alert Operator + Propose Reroute`).
  * **Rule authoring permissions:** Analysts can create and test draft rules. Only Operators (or Admins) can promote rules to "live" status.
  * **Testing requirement:** All rules must be validated in Shadow Mode (see Testing & Simulation) before going live in production.
  * **Versioning:** Every rule change is version-controlled with full change history. Any rule version can be rolled back.
  * **Default rule library:** The platform ships with a curated set of pre-configured rules for common incident scenarios (e.g., highway on-ramp closure, multi-vehicle collision, transit vehicle breakdown blocking a lane).
* **Approval Flows (Human-in-the-Loop):**
  1. AI generates a draft action to change traffic light timings (as a structured, schema-validated proposal with provenance and confidence score).
  2. TOC Operator reviews the proposed ripple effects via the Dependency Graph visualization, edits parameters if necessary, and clicks "Approve."
  3. The platform pushes the approved action downstream to the physical traffic signal controllers.
  4. Post-approval, the system monitors the impact in real time and alerts the operator if conditions worsen (see Rollback and Reversion).

### 6. Rollback and Reversion

Every signal timing change executed through the platform must be reversible. This section defines the rollback mechanisms.

#### 6.1 Manual Rollback
* **One-click revert:** For any active timing change, operators can click "Revert to Previous Plan" to instantly restore the prior signal timing state for the affected intersection(s).
* The reversion is itself logged as an action in the audit trail with full before/after state.

#### 6.2 Automatic Rollback Monitoring
* After any timing change is executed, the platform continuously monitors the affected area's congestion metrics (speed, queue length, throughput).
* **Auto-revert trigger:** If monitored congestion metrics worsen by ≥ 20% (configurable) within 5 minutes (configurable) of a change execution, the system:
  1. Alerts the operator with a prominent "Conditions Worsening" notification.
  2. Proposes an automatic reversion to the previous timing plan.
  3. If configured for auto-revert (opt-in, requires Admin approval to enable), executes the reversion automatically and notifies the operator.

#### 6.3 Change Limits
* **Maximum concurrent active changes:** No more than 5 signal timing modifications may be active simultaneously (configurable by Admin). This prevents cascading complexity and ensures each change's impact can be independently assessed.
* Requests beyond this limit are queued and the operator is notified.

### 7. Testing, Simulation, and Shadow Mode

Given that the platform controls physical infrastructure, rigorous pre-deployment testing is a product requirement, not an afterthought.

#### 7.1 Shadow Mode (Mandatory for MVP Launch)
* The platform must operate in **Shadow Mode** for a minimum burn-in period before any live signal control is enabled.
* In Shadow Mode:
  * All data pipelines, anomaly detection, AI recommendations, and workflow triggers operate normally.
  * Operators interact with the platform as if it were live (reviewing recommendations, clicking "Approve").
  * **No changes are pushed to physical signal controllers.** All "approved" actions are logged but not executed.
* Shadow Mode builds operator trust, validates AI recommendation quality, and surfaces integration issues without risk.
* The decision to transition from Shadow Mode to Live Mode requires explicit sign-off from the TOC Manager and the platform Admin.

#### 7.2 Dry-Run Impact Simulation
* Before any timing change is approved (in both Shadow and Live modes), the platform runs a **projected impact simulation** on the Dependency Graph.
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

### 8. Degraded Operations and Fallback Modes

The platform is used during high-stakes situations where failure is not an option. This section defines what operators see and can do when individual subsystems are unavailable.

**Core principle:** The platform never presents stale data as fresh, never hides failures, and always tells operators what they *can* still do.

| Subsystem Failure | Operator Experience | Available Actions | Automatic System Response |
|---|---|---|---|
| **AI/Copilot pipeline down** | Prominent "AI Unavailable" banner across the top of the screen. Live Grid map and camera feeds continue to function normally. | Operators retain full manual observation capability. Signal changes can be relayed via existing out-of-band tools (phone, radio). | Alert dispatched to Data Engineering on-call. AI availability metric logged. |
| **One or more data feeds stale** | Affected data layers show amber (approaching threshold) or red (exceeded) staleness indicators. Stale feeds are visually dimmed on the map. | Operators can still view all other feeds. AI recommendations automatically exclude stale data and indicate reduced confidence. | Stale feeds are removed from AI analysis. Freshness violation logged. |
| **City model / graph database unreachable** | Dependency Graph view displays "Unavailable." Live Grid map degrades to basic overlay (cameras + GPS dots without relationship analysis). | Operators can monitor camera feeds and transit positions directly. No AI recommendations are generated. | System enters read-only observation mode. Full alert to Data Engineering. |
| **Signal controller API unreachable** | "Execution Blocked" badge on all approval actions. Operators can still review and "approve" recommendations (logged for when connectivity is restored). | Operators relay urgent changes via phone/radio to field technicians (existing SOPs). | All pending approved-but-unexecuted actions are queued. Full audit trail maintained. |
| **Complete platform outage** | Platform unavailable. | Operators fall back entirely to existing legacy tools and standard operating procedures. | Automated notification sent to all registered TOC operators and Data Engineering via SMS/email. |

**Recovery behavior:** When a subsystem recovers, the platform automatically resumes normal operation, processes any queued actions (with operator re-confirmation for signal changes), and logs the outage duration and impact.

### 9. Security, Governance, and Audit

#### 9.1 Role-Based Access Control (RBAC)
* **Viewer** — Read-only access to public-facing dashboards and anonymized data feeds. Suitable for public APIs, news media.
* **Analyst** — Can run simulations, query the AI Copilot, and author draft workflow rules. Cannot approve physical infrastructure changes.
* **Operator** — Can approve AI-recommended signal timing changes, initiate manual rollbacks, and promote workflow rules to live status. This is the core TOC operator role.
* **Admin** — Can manage user accounts and role assignments, configure system settings (confidence thresholds, auto-revert policies, change limits), manage workflow rules at a system level, and authorize the transition from Shadow Mode to Live Mode.
* All role assignments, changes, and permission escalations are logged in the audit trail.

#### 9.2 Audit Trail
* **Scope:** Every action in the system is logged: AI recommendations generated, AI recommendations blocked (with reason), operator approvals, operator edits to recommendations, signal changes executed, rollbacks triggered, workflow rule changes, role assignments, and system configuration changes.
* **Immutability:** Audit logs are append-only and tamper-evident. No user (including Admin) can delete or modify audit entries.
* **Retention:** Minimum 7-year retention period, aligned with municipal records retention requirements. Retention policy to be confirmed with the city's legal and records management office.
* **Format and export:** Logs are stored in a structured, machine-readable format and exportable for legal discovery, regulatory review, and compliance audits.
* **Content per entry:** Timestamp, actor identity (human user ID or "AI Copilot"), action type, target entities, before-state, after-state, data sources consulted, approval chain, and outcome.

#### 9.3 Liability and Legal Framework
* A legal and liability review **must be completed** before the platform's signal controller integration goes live (i.e., before transitioning out of Shadow Mode).
* The platform is designed and positioned as a **decision-support tool**, not an autonomous controller. The human operator bears final approval responsibility for all physical infrastructure changes.
* All AI recommendations are explicitly labeled as "AI Suggestion" in both the UI and the audit trail to maintain clear attribution.
* The liability review should address: municipal indemnification, AI decision-support liability precedents, operator training and certification requirements, and insurance implications.

#### 9.4 Privacy and Data Protection
* **Redaction standard:** Faces and license plates in video feeds must be redacted with ≥ 99% detection accuracy before any video data enters the core platform.
* **Raw data prohibition:** Raw, unredacted video is never stored on or transmitted to the cloud platform. Redaction occurs at the point of capture.
* **Compliance:** Redaction practices must comply with applicable local privacy ordinances (e.g., Seattle Municipal Code).
* **Auditing:** Redaction effectiveness is audited quarterly with a sample-based review. Failures are logged and remediated.

### 10. Deployment and Operations
* Packaged for deployment within environments that comply with municipal cloud and security restrictions.
* The system must support the mandatory Shadow Mode period before any live signal control is enabled.
* Monitoring dashboards track: platform uptime, data feed freshness, AI recommendation quality (acceptance rate, block rate, hallucination rate), and signal controller connectivity.

---

## Success Metrics (MVP)
* **Time-to-Mitigation:** Median time from physical incident occurrence to the first active adjustment of surrounding traffic signals is `< 3 minutes`.
* **Adoption:** 100% of TOC operators utilizing the platform for major incident response during the pilot phase.
* **Quality/Efficacy:** ≥ 85% of AI-suggested signal timing changes are accepted by human operators without manual overrides.
* **AI Safety:** < 2% of AI-generated recommendations are blocked by the physical constraint verification or hallucination detection systems.
* **Shadow Mode Validation:** During the Shadow Mode period, ≥ 90% of AI recommendations are retrospectively confirmed as appropriate by TOC operators in blind review.
* **Rollback Frequency:** < 10% of executed signal timing changes require rollback within the monitoring window.

## Constraints and Assumptions
* The MVP relies purely on existing, publicly available or municipal data sources. Custom hardware integration is out of scope.
* Initial deployment focuses exclusively on a constrained geographic bounding box (e.g., the Downtown Seattle traffic grid).
* All video feeds must have personally identifiable information redacted at the point of capture before entering the platform, in compliance with local privacy ordinances.
* **MVP scale targets:** Up to 50 concurrent camera feeds, 200 intersections, 500 tracked transit vehicles, and 10 concurrent operator sessions. These targets define the initial bounding box and architecture requirements.
* The platform will operate in **Shadow Mode** (no live signal control) for a minimum burn-in period before any live integration is authorized.
* Direct signal controller integration may not be feasible for MVP. If the pre-development investigation (see Appendix A) determines that direct integration is blocked, the platform will operate in **Advisory Mode** — generating recommendations that operators execute manually through existing systems.

---

## Appendix A: Signal Controller Integration (Investigation Spike)

The MVP's "execute" step — pushing approved timing changes to physical signal controllers — depends on an integration layer that is not yet defined. This appendix outlines the investigation required before development begins.

### What Must Be Determined
1. **Controller protocol inventory:** Which signal controller communication protocols are in use within the Downtown Seattle bounding box? Likely candidates include NTCIP 1202 (v02, v03, or the forthcoming v04 with SNMPv3), proprietary SCATS protocols, or vendor-specific interfaces (e.g., Econolite, Siemens, McCain).
2. **Central management system:** Does the city operate a central signal management system (e.g., SCATS master, Econolite Centracs, Siemens TACTICS) that provides an existing API layer? If so, integrating at the management system level (rather than directly with individual controllers) is strongly preferred.
3. **Access and authorization:** What municipal approvals, network access, and security certifications are required to connect a third-party platform to the signal control network?
4. **Interoperability constraints:** Are there NTCIP compliance requirements that the platform must meet? (NTCIP 1202 defines standardized object definitions for actuated traffic signal controllers, enabling interoperability across manufacturers.)
5. **Latency requirements:** What is the maximum acceptable latency from operator approval to signal change execution? This informs whether real-time API integration or batch-based approaches are viable.

### Deadline
This investigation must be completed **before Sprint 1** of MVP development begins. The results will determine whether the MVP includes live signal control or operates in Advisory Mode.

### Fallback: Advisory Mode
If direct signal controller integration proves infeasible for the MVP timeline (due to protocol complexity, municipal approval timelines, or security requirements), the platform will operate in **Advisory Mode**:
* All AI recommendations and operator approvals are generated and logged normally.
* Instead of pushing changes to controllers, the platform displays the approved action as a **formatted instruction** that the operator relays to field technicians or enters into the existing signal management system manually.
* Advisory Mode still delivers value by unifying situational awareness, AI analysis, and decision workflows — even without the final automation step.

---

## Appendix B: Phase 2 Preview

The following capabilities are planned for Phase 2, after the MVP has been validated in production.

### Emergency Dispatcher Integration
* **Emergency Green Wave Request:** Dispatchers can request a "green wave" corridor for an emergency vehicle route. The platform calculates the optimal signal timing sequence along the route and presents it for TOC Operator approval (or auto-approval for pre-authorized emergency classes).
* **Dispatcher Dashboard:** A simplified, read-only view showing the current state of the traffic grid and any active incidents, optimized for the dispatcher's workflow (route status, estimated travel times, active blockages).
* **CAD System Integration:** Integration with the city's Computer-Aided Dispatch (CAD) system to automatically ingest emergency vehicle dispatch events and surface them in the platform.

### Additional Phase 2 Capabilities
* **Multi-agency shared ontology:** Extend the city model to be accessible by additional municipal departments (public works, utilities) as a shared "digital twin."
* **Predictive congestion modeling:** Move from reactive incident response to proactive congestion prediction using historical patterns and event calendars.
* **Public-facing transit impact dashboard:** An anonymized, read-only view for the public showing major incidents and estimated transit delays.
