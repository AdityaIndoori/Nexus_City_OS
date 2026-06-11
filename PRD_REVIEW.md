# PRD Review: Nexus City OS

## Executive Summary
- Total issues found: 74
- Critical: 8 | Major: 41 | Minor: 25
- Top 3 risks:
  1. The MVP scope promises "execute city-wide traffic signal mitigations and transit rerouting" but signal controller integration is an unresolved investigation spike and no transit rerouting feature exists in the feature set — the core deliverable may be unachievable.
  2. Shadow Mode has no defined burn-in duration, no acceptance criteria for Shadow-to-Live transition, and the mandatory legal review has no timeline or owner — the path from development to production is undefined.
  3. Authentication mechanism is completely unspecified for a system that controls physical municipal infrastructure — no MFA, SSO, or smart card requirements exist anywhere in the document.

## Detailed Findings

### 1. Product Vision & Scope

**[SEVERITY: CRITICAL]** — MVP scope may be undeliverable due to unresolved signal controller integration
- **What's wrong:** The MVP scope statement defines the product as able to "execute city-wide traffic signal mitigations and transit rerouting." However, Appendix A reveals that the signal controller integration is an unresolved investigation spike. The PRD acknowledges the MVP may fall back to Advisory Mode, where no execution happens at all.
- **Why it matters:** The MVP scope statement, success metrics (Time-to-Mitigation < 3 minutes), and the entire approval workflow assume live signal control. If Advisory Mode is the realistic outcome, the scope statement, success metrics, and UX workflows all need to be rewritten.
- **Where in PRD:** "MVP Scope" section vs. "Appendix A: Signal Controller Integration (Investigation Spike)"
- **Suggested fix:** Define two explicit MVP scope statements: one for Live Mode and one for Advisory Mode. Ensure success metrics, UX workflows, and the definition of "done" are specified for both scenarios.

**[SEVERITY: MAJOR]** — "Autonomously" in MVP scope contradicts human-in-the-loop non-goal
- **What's wrong:** The MVP scope says "autonomously recommend and execute city-wide traffic signal mitigations." The Non-Goals section says "Fully automate traffic control without a human-in-the-loop (HITL)" is explicitly out of scope. The word "autonomously" directly conflicts with the HITL requirement.
- **Why it matters:** This creates ambiguity about the product's operating model. Developers, operators, and legal counsel will interpret "autonomously" differently, especially in liability contexts.
- **Where in PRD:** "MVP Scope" vs. "Non-Goals" bullet 3
- **Suggested fix:** Replace "autonomously recommend and execute" with "detect, recommend, and — upon operator approval — execute" in the MVP scope statement.

**[SEVERITY: MAJOR]** — Shadow Mode burn-in period is undefined
- **What's wrong:** The PRD states "a minimum burn-in period" for Shadow Mode but never specifies what that period is — days, weeks, months? There are no exit criteria or acceptance gates defined.
- **Why it matters:** Without a defined duration and exit criteria, the project has no clear path from Shadow Mode to Live Mode. This will cause schedule disputes and potentially indefinite delays.
- **Where in PRD:** Section 7.1 "Shadow Mode" — "minimum burn-in period before any live signal control is enabled"
- **Suggested fix:** Define a specific minimum burn-in period (e.g., 30 days) and explicit acceptance criteria: minimum number of incidents processed, AI recommendation accuracy threshold, operator confidence survey results, and legal review completion.

**[SEVERITY: MAJOR]** — No clear definition of "done" for MVP
- **What's wrong:** The PRD does not state whether MVP is "done" when Shadow Mode is operational, or when Live Mode is activated. Given that Live Mode depends on the unresolved signal controller investigation, this is a critical planning gap.
- **Why it matters:** Without a "done" definition, the team cannot scope sprints, set a launch date, or communicate milestones to municipal stakeholders.
- **Where in PRD:** No explicit MVP completion criteria exist anywhere in the document.
- **Suggested fix:** Add an explicit "MVP Definition of Done" section with checkboxes covering: Shadow Mode operational, burn-in period completed, legal review signed off, signal controller integration resolved (or Advisory Mode accepted), and operator training completed.

---

### 2. User Personas & Use Cases

**[SEVERITY: MAJOR]** — TOC Manager is a critical decision-maker but not defined as a persona
- **What's wrong:** The PRD requires "explicit sign-off from the TOC Manager" to transition from Shadow Mode to Live Mode, but the TOC Manager is never listed as a user persona. Their workflows, information needs, and UI requirements are undefined.
- **Why it matters:** The TOC Manager's sign-off is a gate for the most critical product milestone. Without understanding their needs, the team cannot design the reports, dashboards, or evidence they need to make that decision.
- **Where in PRD:** Section 7.1 references "TOC Manager" sign-off; Users and Use Cases section omits this role.
- **Suggested fix:** Add TOC Manager as an MVP persona. Define their specific needs: Shadow Mode performance reports, AI accuracy dashboards, risk assessment views, and the formal sign-off workflow.

**[SEVERITY: MAJOR]** — Field technicians referenced but not defined as users
- **What's wrong:** In Advisory Mode and degraded operations, operators "relay urgent changes via phone/radio to field technicians." Field technicians are actors in the system's operational workflow but have no persona definition.
- **Why it matters:** If Advisory Mode becomes the MVP reality, field technicians are the actual executors of every signal change. Their workflow, information needs (what format do instructions arrive in?), and confirmation protocols must be designed.
- **Where in PRD:** Appendix A "Advisory Mode" and Section 8 degraded operations table.
- **Suggested fix:** Add Field Technician as a secondary persona. Define the instruction format they receive, the confirmation protocol for executed changes, and the feedback loop back to the platform.

**[SEVERITY: MAJOR]** — Representative use case references I-5, which is outside the stated geographic scope
- **What's wrong:** The representative use case says "Within seconds of a severe incident on I-5." However, I-5 through Downtown Seattle is managed by WSDOT (state jurisdiction), not the City of Seattle. The constraints section scopes the MVP to the "Downtown Seattle traffic grid" which typically refers to city-managed infrastructure.
- **Why it matters:** This creates a jurisdictional conflict. The city's TOC likely cannot control WSDOT signal infrastructure. Building the use case around I-5 sets expectations the product cannot meet.
- **Where in PRD:** "Representative MVP Use Case" vs. "Constraints and Assumptions" geographic bounding box.
- **Suggested fix:** Replace the I-5 use case with one centered on city-managed arterials (e.g., "a multi-vehicle collision at the intersection of 4th Ave and Pike St"). If I-5 incident detection is in scope (monitoring only, not signal control), state that explicitly.

**[SEVERITY: MINOR]** — Data Engineers listed as primary users but feature set barely serves them
- **What's wrong:** Data Engineers are listed as a primary MVP persona, but the only feature serving them is a staging environment (Section 7.3). No data pipeline monitoring, data quality dashboards, or integration management tools are described.
- **Why it matters:** Calling them "primary users" sets expectations that the product serves their workflows. In reality, the MVP is almost entirely designed for TOC Operators.
- **Where in PRD:** "Primary Users (MVP)" lists Data Engineers; feature set lacks Data Engineer-specific tools.
- **Suggested fix:** Either add Data Engineer-specific features (pipeline monitoring dashboard, data quality alerts, integration health views) or reclassify them as "Supporting Users" rather than "Primary Users."

**[SEVERITY: MINOR]** — System administrator and DevOps roles absent from user list
- **What's wrong:** The platform requires deployment, monitoring, and infrastructure management, but no DevOps or system administrator persona is defined. The Admin role in RBAC focuses on user management and configuration, not infrastructure operations.
- **Why it matters:** Someone must manage deployments, monitor platform health, and respond to infrastructure incidents. Without defining this role, operational responsibilities are ambiguous.
- **Where in PRD:** Users and Use Cases section; RBAC section 9.1.
- **Suggested fix:** Add a Platform Operations / DevOps persona or explicitly state that the Admin role encompasses infrastructure operations, and define those responsibilities.

---

### 3. Feature Completeness

**[SEVERITY: CRITICAL]** — Transit rerouting is in MVP scope but no feature implements it
- **What's wrong:** The MVP scope states "transit rerouting" as a deliverable. The feature set only describes signal timing changes. There is no feature for generating, proposing, or executing transit route modifications (bus detours, rail service adjustments, etc.).
- **Why it matters:** This is a gap between the product's stated scope and its actual capabilities. Transit agencies, operators, and stakeholders will expect rerouting functionality that doesn't exist.
- **Where in PRD:** "MVP Scope" mentions "transit rerouting"; Feature Set sections 1-8 describe only signal timing changes.
- **Suggested fix:** Either add a Transit Rerouting feature (with integration to transit agency dispatch systems) or remove "transit rerouting" from the MVP scope and defer it to Phase 2.

**[SEVERITY: MAJOR]** — Simulation engine is referenced but never specified
- **What's wrong:** The `simulate_impact(proposed_changes)` tool is defined in the AI Grounding section and the Dry-Run Impact Simulation feature depends on it. However, the underlying simulation engine — its algorithm, fidelity, validation approach, and computational requirements — is never described.
- **Why it matters:** A traffic simulation engine is a complex system in its own right. Without specifying its approach (microsimulation, mesoscopic, macroscopic), the team cannot estimate development effort or validate its accuracy.
- **Where in PRD:** Section 4.1 tool-calling interface and Section 7.2 Dry-Run Impact Simulation.
- **Suggested fix:** Add a specification for the simulation engine: modeling approach, required inputs, accuracy validation methodology, computational performance targets, and whether a third-party engine (e.g., SUMO, Aimsun) will be integrated or a custom engine built.

**[SEVERITY: MAJOR]** — Anomaly detection model training and validation are unspecified
- **What's wrong:** Section 3 states "Continuously parse live traffic camera feeds to automatically flag anomalies (stopped vehicles, pedestrians on highways, wrong-way drivers)" but does not specify the CV model architecture, training data requirements, accuracy targets, false positive/negative rates, or validation methodology.
- **Why it matters:** Computer vision models for safety-critical anomaly detection require rigorous validation. Without accuracy targets and validation protocols, the team could ship a model with unacceptable false positive rates that desensitize operators.
- **Where in PRD:** Section 3 "Automated Anomaly Detection."
- **Suggested fix:** Specify: target detection accuracy per anomaly type, maximum acceptable false positive rate, training dataset requirements, validation protocol (including edge cases like weather, lighting, camera angle), and model update/retraining cadence.

**[SEVERITY: MAJOR]** — No proactive notification system beyond in-UI banners
- **What's wrong:** All alerting described in the PRD is within the platform UI (banners, incident mode transitions, badges). There is no specification for proactive notifications — push alerts, audible alarms, pager/SMS notifications — to reach operators who may not be actively viewing the screen.
- **Why it matters:** In a TOC environment, operators manage multiple systems. If a critical incident occurs and the operator is monitoring another system, the Nexus City OS alert will be missed. For a safety-critical system, this is a serious gap.
- **Where in PRD:** Sections 2, 5, and 8 describe only in-UI alerting. The only mention of SMS/email is for complete platform outage notifications.
- **Suggested fix:** Add a notification subsystem specification covering: audible alarms for critical incidents, push notifications to operator devices, configurable alert escalation (if unacknowledged in N seconds, escalate to supervisor), and integration with existing TOC alerting infrastructure.

**[SEVERITY: MAJOR]** — Advisory Mode instruction format is undefined
- **What's wrong:** Appendix A states that in Advisory Mode, the platform displays "a formatted instruction that the operator relays to field technicians." The format, content, and structure of these instructions are never specified.
- **Why it matters:** If Advisory Mode is the MVP reality, these formatted instructions are the product's primary output. Without a specification, the team will design something that may not match field technician workflows or existing communication protocols.
- **Where in PRD:** Appendix A "Fallback: Advisory Mode."
- **Suggested fix:** Define the instruction format: what information is included (intersection ID, current timing, new timing, duration, priority), what communication channel it targets (radio script, printable card, SMS template), and how confirmation of execution is captured.

**[SEVERITY: MAJOR]** — Workflow Canvas rule language and authoring UX are unspecified
- **What's wrong:** Section 5 describes a "Workflow Canvas" for defining automated triggers with an example syntax (`IF CollisionDetected AND Severity > High → Alert Operator + Propose Reroute`). The actual rule language, available conditions, available actions, and the authoring interface are not defined.
- **Why it matters:** The Workflow Canvas is a complex feature that requires its own specification. Without defining the rule language, engineers cannot implement it and analysts cannot plan their workflows.
- **Where in PRD:** Section 5 "Decision Workflows and Approvals."
- **Suggested fix:** Add a Workflow Canvas specification: enumerated list of available trigger conditions, available actions, rule syntax or visual authoring paradigm (code vs. drag-and-drop), and validation rules for draft vs. live rules.

**[SEVERITY: MINOR]** — Default rule library contents are unspecified
- **What's wrong:** The PRD mentions "a curated set of pre-configured rules for common incident scenarios" with examples but does not list the actual rules or their parameters.
- **Why it matters:** These default rules are the starting point for operator workflows. Without specifying them, the team will need a separate design exercise to define them, adding schedule risk.
- **Where in PRD:** Section 5 "Default rule library."
- **Suggested fix:** Define at least the 5-10 default rules with their trigger conditions, proposed actions, and default parameters.

**[SEVERITY: MINOR]** — No historical incident search or reporting capability
- **What's wrong:** The PRD describes real-time incident response but provides no capability for operators or managers to search historical incidents, review past actions, or generate reports on incident trends.
- **Why it matters:** Post-incident review and trend analysis are standard TOC workflows. The audit trail captures the data, but no search or reporting interface is described.
- **Where in PRD:** Not present. The audit trail (Section 9.2) captures data but no query/reporting interface is specified.
- **Suggested fix:** Add a historical incident search and reporting feature, or explicitly defer it to Phase 2 with a note that audit trail data will be queryable via export.

**[SEVERITY: MINOR]** — Dependency Graph visualization algorithm and cascading impact calculation are unspecified
- **What's wrong:** The Dependency Graph is a central visualization showing "cascading impacts" with specific predictions (e.g., "these 14 connected intersections will reach gridlock in 8 minutes"). The algorithm for calculating these cascading predictions is not described.
- **Why it matters:** The accuracy of cascading impact predictions determines whether operators trust the system. If the algorithm is naive, predictions will be wrong and erode confidence.
- **Where in PRD:** Section 2 "Dependency Graph" visualization.
- **Suggested fix:** Specify the cascading impact model: what inputs it uses, what propagation model it employs, how it handles uncertainty, and what accuracy validation will be performed.

---

### 4. UX & Operator Workflow

**[SEVERITY: MAJOR]** — Incident Mode has no exit criteria or return-to-normal workflow
- **What's wrong:** Section 2 describes Incident Mode entry (auto-center, priority panels, suppressed clutter) but never defines when or how the UI exits Incident Mode. Is it manual? Automatic when congestion clears? What if the operator needs to monitor two areas simultaneously?
- **Why it matters:** Without exit criteria, operators may be stuck in Incident Mode or may lose situational awareness of the broader grid while focused on one incident.
- **Where in PRD:** Section 2 "Incident Mode UX" — entry is defined, exit is not.
- **Suggested fix:** Define Incident Mode exit criteria: manual dismissal by operator, automatic transition when congestion metrics return to baseline, and a "picture-in-picture" or split-view option for monitoring the broader grid while in Incident Mode.

**[SEVERITY: MAJOR]** — Multiple simultaneous incidents are not addressed
- **What's wrong:** The Incident Mode UX assumes a single incident. The PRD does not address what happens when two or more significant incidents occur simultaneously — which is common in major weather events or during large public events.
- **Why it matters:** A UI designed for one incident will break or confuse operators during multi-incident scenarios, precisely when the system is most needed.
- **Where in PRD:** Section 2 "Incident Mode UX" — single-incident assumption throughout.
- **Suggested fix:** Specify multi-incident UX: incident list/queue, ability to switch between incident contexts, priority ranking of incidents, and how the Dependency Graph handles overlapping impact zones.

**[SEVERITY: MAJOR]** — Shift handoff during extended incidents is not addressed
- **What's wrong:** The approval workflow and action history assume a single operator session. For incidents lasting beyond a shift change, there is no mechanism for handing off incident context, pending approvals, or active mitigations to the incoming operator.
- **Why it matters:** Shift changes during active incidents are a known source of errors in operations centers. Without a handoff protocol, critical context will be lost.
- **Where in PRD:** Sections 2 and 5 — no mention of shift handoff or multi-session incident management.
- **Suggested fix:** Add a shift handoff feature: incident summary generation, pending action transfer, and a formal handoff acknowledgment step that is logged in the audit trail.

**[SEVERITY: MINOR]** — No keyboard shortcuts or rapid-action patterns for time-critical operations
- **What's wrong:** For a system with a "< 3 minutes" time-to-mitigation target, the UX description relies entirely on mouse-driven interactions (clicking "Approve," navigating maps). No keyboard shortcuts or rapid-action patterns are described.
- **Why it matters:** In time-critical scenarios, every second counts. Experienced operators in control rooms rely heavily on keyboard shortcuts and memorized action sequences.
- **Where in PRD:** Section 2 and Section 5 — all interactions described as click-based.
- **Suggested fix:** Specify keyboard shortcut requirements for critical actions: approve recommendation, initiate rollback, acknowledge alert, and navigate between incidents.

**[SEVERITY: MINOR]** — Incident lifecycle management (open/close) is informal
- **What's wrong:** Incidents appear to be implicitly created when anomaly detection fires or an operator flags something. There is no formal incident lifecycle: creation, assignment, escalation, resolution, and closure with root cause.
- **Why it matters:** Without formal lifecycle management, there is no way to track incident resolution rates, mean time to resolution, or ensure every incident is formally closed.
- **Where in PRD:** Section 2 mentions an "Action history sidebar" but no formal incident lifecycle is defined.
- **Suggested fix:** Define a formal incident lifecycle with states (Detected → Acknowledged → Mitigating → Resolved → Closed), transitions, and required actions at each state. This also supports the historical reporting feature.

---

### 5. AI Safety & Grounding

**[SEVERITY: CRITICAL]** — Confidence score calculation method is undefined
- **What's wrong:** Section 4.3 specifies a confidence score (0-100%) with a threshold mechanism but never defines how the score is calculated. Is it model output probability? An ensemble score? A heuristic based on data freshness?
- **Why it matters:** The confidence score is a safety-critical mechanism — it determines which recommendations reach operators. Without a defined calculation method, the score is meaningless and cannot be validated or audited.
- **Where in PRD:** Section 4.3 "Confidence Scoring and Abstention."
- **Suggested fix:** Define the confidence score calculation: inputs (model certainty, data freshness, coverage of affected area, historical accuracy for similar scenarios), formula or algorithm, and validation methodology. Include calibration requirements (a 70% confidence score should be correct ~70% of the time).

**[SEVERITY: MAJOR]** — No LLM model selection, versioning, or update strategy
- **What's wrong:** The PRD never specifies which LLM or AI models power the Copilot and anomaly detection. There is no model versioning strategy, update process, rollback capability for model updates, or A/B testing framework.
- **Why it matters:** Model updates in safety-critical systems can introduce regressions. Without a model lifecycle management strategy, a bad model update could degrade recommendations with no rollback path.
- **Where in PRD:** Sections 3 and 4 — no model selection or lifecycle management mentioned.
- **Suggested fix:** Add a Model Lifecycle section specifying: model selection criteria, versioning scheme, staged rollout process for model updates (shadow testing new models before promotion), rollback capability, and performance regression monitoring.

**[SEVERITY: MAJOR]** — No prompt injection or adversarial input protections
- **What's wrong:** The AI Copilot accepts operator queries (Section 3 example: "Which rapid ride bus routes are currently delayed..."). The PRD does not address prompt injection attacks, adversarial inputs, or input sanitization.
- **Why it matters:** An operator (malicious or compromised) could craft queries designed to bypass the tool-calling constraints. While the tool-calling pattern provides some protection, the attack surface is not analyzed.
- **Where in PRD:** Section 3 "AI Copilot" and Section 4 "AI Grounding and Safety Architecture" — no adversarial input protections.
- **Suggested fix:** Add an adversarial input protection specification: input sanitization for operator queries, rate limiting for AI Copilot interactions, monitoring for anomalous query patterns, and penetration testing requirements for the AI interface.

**[SEVERITY: MAJOR]** — Operator-adjustable confidence threshold "governed range" is undefined
- **What's wrong:** Section 4.3 states operators can "adjust the confidence threshold within a governed range" but the range is never specified. Could an operator set it to 10% and receive low-quality recommendations?
- **Why it matters:** An improperly configured threshold could either suppress useful recommendations or allow unsafe ones through. The "governed range" is a safety control that needs concrete bounds.
- **Where in PRD:** Section 4.3 "Confidence Scoring and Abstention."
- **Suggested fix:** Define the governed range explicitly (e.g., 50-95%), specify which role can adjust it (Operator vs. Admin), and require audit logging of threshold changes.

**[SEVERITY: MINOR]** — 2% hallucination target lacks baseline or justification
- **What's wrong:** Section 4.5 sets a target of "< 2% of generated recommendations are blocked for safety violations." There is no baseline for comparison or justification for why 2% is the right threshold.
- **Why it matters:** Without a baseline from Shadow Mode testing, 2% could be either trivially easy or impossible to achieve. The target is meaningless without context.
- **Where in PRD:** Section 4.5 "Hallucination Monitoring."
- **Suggested fix:** State that the 2% target will be validated during Shadow Mode, define the baseline measurement methodology, and specify the action plan if the target is not met (model retraining, threshold adjustment, or scope reduction).

---

### 6. Data Architecture & Freshness

**[SEVERITY: MAJOR]** — PRD specifies "3-5 data sources" but only 3 are identified
- **What's wrong:** Section 1 says "Ingest 3–5 key municipal data sources" but only lists three: camera feeds, transit GPS, and roadwork schedules. The remaining 1-2 sources are unidentified.
- **Why it matters:** Unidentified data sources cannot be architected, integrated, or tested. This creates hidden scope that will emerge during development.
- **Where in PRD:** Section 1 "Data Integration and City Model."
- **Suggested fix:** Either identify all data sources explicitly (candidates: road sensor speed/volume data, weather feeds, event permits) or change the requirement to "Ingest 3 key municipal data sources" with a note that additional sources may be added post-MVP.

**[SEVERITY: MAJOR]** — Data throughput requirements are missing
- **What's wrong:** The PRD specifies 50 concurrent camera feeds but never states the required frame rate, resolution, or bandwidth per feed. Video processing throughput requirements for the anomaly detection system are also absent.
- **Why it matters:** A 50-camera system at 1 FPS has vastly different infrastructure requirements than 50 cameras at 30 FPS. Without throughput specs, the architecture cannot be properly sized.
- **Where in PRD:** Section 1 "Data Freshness Requirements" and "Constraints and Assumptions" — latency is specified but throughput is not.
- **Suggested fix:** Specify per-camera requirements: minimum frame rate for anomaly detection (e.g., 5 FPS), resolution requirements, and aggregate bandwidth/compute budget. Also specify the throughput for the city model graph database (queries per second, update rate).

**[SEVERITY: MAJOR]** — City model graph database technology and implementation are unspecified
- **What's wrong:** The city model is described conceptually (entities: Intersection, RoadSegment, TransitVehicle, etc.) but the implementation technology is never specified. Is it a graph database (Neo4j, Neptune)? A relational database with graph queries? A custom in-memory structure?
- **Why it matters:** The city model is the foundation of the entire platform. Technology selection affects query performance, scalability, real-time update capabilities, and the simulation engine design.
- **Where in PRD:** Section 1 "Data Integration and City Model" — conceptual model only.
- **Suggested fix:** Either specify the technology choice or define the non-functional requirements that will drive the selection: query latency targets, update frequency, concurrent query capacity, and graph traversal depth requirements.

**[SEVERITY: MINOR]** — No backup or disaster recovery for city model data
- **What's wrong:** The city model is described as a "real-time engine" but there are no backup, replication, or disaster recovery requirements specified.
- **Why it matters:** Loss of the city model means complete platform failure. Without DR requirements, the team may build a single-point-of-failure architecture.
- **Where in PRD:** Section 1 — no mention of data durability, backup, or replication.
- **Suggested fix:** Specify RPO (Recovery Point Objective) and RTO (Recovery Time Objective) for the city model. Define backup frequency, replication strategy, and failover requirements.

**[SEVERITY: MINOR]** — Operational data retention policy is undefined (separate from audit logs)
- **What's wrong:** Audit log retention is specified at 7 years (Section 9.2), but retention for operational data — historical camera feeds, transit GPS tracks, congestion metrics — is never addressed.
- **Why it matters:** Operational data has different retention needs than audit data. Without a policy, storage costs will grow unbounded and historical analysis capabilities are undefined.
- **Where in PRD:** Section 9.2 covers audit retention only.
- **Suggested fix:** Define retention policies for each data type: processed camera data, transit telemetry, congestion metrics, and city model snapshots. Consider tiered retention (hot/warm/cold storage).

---

### 7. Security, Access Control & Audit

**[SEVERITY: CRITICAL]** — Authentication mechanism is completely unspecified
- **What's wrong:** The PRD defines RBAC roles but never specifies the authentication mechanism. There is no mention of MFA, SSO, smart cards, CAC/PIV cards, or any specific authentication protocol. For a system that controls physical municipal infrastructure, this is a glaring omission.
- **Why it matters:** Authentication is the first line of defense against unauthorized signal changes. Municipal systems typically require PIV/smart card authentication or at minimum MFA. Without specifying this, the team may implement basic username/password authentication.
- **Where in PRD:** Section 9.1 "Role-Based Access Control" — defines roles but not authentication.
- **Suggested fix:** Specify: mandatory MFA for Operator and Admin roles, integration with municipal identity provider (Active Directory, SAML/OIDC SSO), session timeout policies (shorter for Operator/Admin roles), and concurrent session limits.

**[SEVERITY: MAJOR]** — No session management requirements
- **What's wrong:** The PRD does not specify session timeout policies, concurrent session limits, or session termination procedures. An operator could leave a session open indefinitely or log in from multiple locations.
- **Why it matters:** In a control room environment, abandoned sessions with Operator-level permissions are a security and safety risk. Concurrent sessions could lead to conflicting approvals.
- **Where in PRD:** Section 9 — no session management mentioned.
- **Suggested fix:** Define session timeout (e.g., 15-minute inactivity timeout for Operator role), maximum concurrent sessions per user (1 for Operator), forced logout capability for Admin, and session transfer protocol for shift handoff.

**[SEVERITY: MAJOR]** — Network security requirements are absent
- **What's wrong:** The PRD does not specify network security requirements: encryption in transit, encryption at rest, VPN requirements for remote access, network segmentation between the platform and the signal controller network, or compliance with municipal network security policies.
- **Why it matters:** The platform bridges the IT network (data processing) and the OT network (signal controllers). Without network security requirements, the signal controller network could be exposed to internet-connected threats.
- **Where in PRD:** Sections 9 and 10 — no network security specifications.
- **Suggested fix:** Specify: TLS 1.3 minimum for all communications, encryption at rest for all stored data, network segmentation requirements (IT/OT boundary), VPN or zero-trust access requirements, and compliance with NIST 800-82 (Guide to ICS Security) or equivalent municipal standard.

**[SEVERITY: MAJOR]** — No emergency escalation path for temporary permission elevation
- **What's wrong:** The RBAC model has fixed roles with no mechanism for temporary permission elevation during emergencies. If the only authorized Operator is unavailable during a critical incident, an Analyst cannot step up.
- **Why it matters:** In emergency operations, rigid access controls can create dangerous bottlenecks. The system needs a break-glass procedure.
- **Where in PRD:** Section 9.1 RBAC — static roles only.
- **Suggested fix:** Define a "break-glass" emergency escalation procedure: temporary permission elevation for designated Analysts, requiring dual authorization (e.g., another Analyst + phone confirmation with Admin), time-limited (e.g., 2 hours), and prominently logged in the audit trail.

**[SEVERITY: MINOR]** — 7-year audit log retention lacks storage and implementation planning
- **What's wrong:** Section 9.2 requires 7-year audit log retention but provides no guidance on storage architecture, estimated volume, archival strategy, or cost implications.
- **Why it matters:** 7 years of append-only logs for a system processing 50 camera feeds, 500 vehicles, and 200 intersections will generate substantial storage volume. Without planning, costs could be unexpected.
- **Where in PRD:** Section 9.2 "Retention."
- **Suggested fix:** Add estimated annual log volume based on MVP scale targets, define a tiered storage strategy (hot for recent, cold for archived), and specify the query capability required for archived logs (for legal discovery).

---

### 8. Governance, Compliance & Legal

**[SEVERITY: CRITICAL]** — MUTCD compliance is not mentioned for signal timing changes
- **What's wrong:** The Manual on Uniform Traffic Control Devices (MUTCD) is the federal standard governing traffic signal operations in the United States. The PRD never references MUTCD compliance for the signal timing changes the platform generates and executes.
- **Why it matters:** MUTCD compliance is legally required. Signal timing changes that violate MUTCD standards could expose the city to federal funding clawbacks, litigation, and safety hazards. The physical constraint verification (Section 4.4) mentions "traffic engineering standards" generically but never references MUTCD specifically.
- **Where in PRD:** Section 4.4 "Physical Constraint Verification" mentions generic standards; MUTCD is never named.
- **Suggested fix:** Explicitly require MUTCD compliance for all signal timing parameters (minimum green, pedestrian intervals, yellow change intervals, red clearance intervals). Reference MUTCD Chapter 4D specifically. Encode MUTCD minimums as hard constraints in the physical constraint verification layer.

**[SEVERITY: MAJOR]** — Legal review has no timeline, owner, or completion criteria
- **What's wrong:** Section 9.3 states a legal review "must be completed" before going live but assigns no owner, sets no deadline, and defines no completion criteria or deliverables.
- **Why it matters:** Legal reviews for municipal infrastructure can take 3-12 months. Without a timeline integrated into the project plan, it will become the longest pole in the tent and delay the Shadow-to-Live transition indefinitely.
- **Where in PRD:** Section 9.3 "Liability and Legal Framework."
- **Suggested fix:** Assign an owner (city attorney's office + platform vendor legal), set a deadline (must complete before Shadow Mode burn-in ends), define deliverables (written legal opinion, indemnification agreement, insurance confirmation), and identify the specific legal questions that must be answered.

**[SEVERITY: MAJOR]** — ADA / accessibility compliance is absent
- **What's wrong:** The PRD does not mention ADA compliance, WCAG standards, or any accessibility requirements for the operator UI. Municipal software procurement typically requires Section 508 compliance.
- **Why it matters:** Municipal procurement contracts almost universally require accessibility compliance. Failure to address this could block procurement approval or expose the city to ADA litigation.
- **Where in PRD:** Not mentioned anywhere in the document.
- **Suggested fix:** Add a WCAG 2.1 AA compliance requirement for all platform interfaces. Specify particular attention to color contrast (critical for staleness indicators), screen reader compatibility, and keyboard navigability (which also supports the rapid-action requirement).

**[SEVERITY: MAJOR]** — Operator training and certification requirements are undefined
- **What's wrong:** Section 9.3 mentions "operator training and certification requirements" as a topic for the legal review but never defines what training is needed, who provides it, how certification is tracked, or whether certification is a prerequisite for receiving Operator role permissions.
- **Why it matters:** Untrained operators approving AI-recommended signal changes on live infrastructure is a safety and liability risk. Training is a prerequisite for the system's safety model (human-in-the-loop only works if the human is competent).
- **Where in PRD:** Section 9.3 mentions training in passing; no training specification exists.
- **Suggested fix:** Define: minimum training requirements for each RBAC role, certification process and renewal cadence, training content (platform operation, AI recommendation interpretation, rollback procedures, emergency procedures), and a requirement that Operator role is locked until training certification is recorded in the system.

**[SEVERITY: MINOR]** — Data sovereignty and municipal residency requirements are unspecified
- **What's wrong:** Section 10 mentions "municipal cloud and security restrictions" but does not specify data residency requirements (must data stay within city boundaries? State? US?), sovereign cloud requirements, or FedRAMP compliance needs.
- **Why it matters:** Municipal contracts frequently mandate data residency within specific jurisdictions. Cloud architecture decisions depend on these requirements.
- **Where in PRD:** Section 10 "Deployment and Operations."
- **Suggested fix:** Specify data residency requirements explicitly. If unknown, flag it as a pre-development investigation item with the city's IT security office.

**[SEVERITY: MINOR]** — 99% redaction accuracy target lacks measurement methodology and failure remediation
- **What's wrong:** Section 9.4 requires "≥ 99% detection accuracy" for face and license plate redaction, with quarterly audits. But the measurement methodology (how is accuracy measured?), sample size, and remediation protocol for the 1% failures are not defined.
- **Why it matters:** At 50 cameras, even 1% failure rate could mean thousands of unredacted faces per day. The remediation for discovered failures (retroactive redaction? data deletion? notification?) is not addressed.
- **Where in PRD:** Section 9.4 "Privacy and Data Protection."
- **Suggested fix:** Define: measurement methodology (manual review of random samples, size, frequency), acceptable failure remediation (automatic deletion of frames with detected redaction failures), and incident response protocol for discovered privacy breaches.

---

### 9. Testing, Simulation & Rollout

**[SEVERITY: CRITICAL]** — No acceptance criteria for Shadow Mode to Live Mode transition
- **What's wrong:** The PRD requires Shadow Mode before Live Mode but defines no quantitative acceptance criteria for the transition. "Sign-off from TOC Manager and Admin" is a process step, not a decision framework.
- **Why it matters:** Without acceptance criteria, the transition decision is subjective and politically fraught. The TOC Manager has no framework for evaluating readiness, which could lead to either premature activation or indefinite delay.
- **Where in PRD:** Section 7.1 "Shadow Mode."
- **Suggested fix:** Define explicit acceptance criteria: minimum duration (e.g., 30 days), minimum incidents processed (e.g., 20), AI recommendation accuracy in blind review (≥ 90%, already a success metric), operator proficiency assessment, legal review completion, and zero critical safety violations in the burn-in period.

**[SEVERITY: MAJOR]** — No load testing, penetration testing, or disaster recovery testing requirements
- **What's wrong:** The PRD specifies a staging environment and Shadow Mode but does not require load testing (to validate MVP scale targets), penetration testing (for a system connected to critical infrastructure), or disaster recovery testing.
- **Why it matters:** Municipal infrastructure systems are high-value targets for cyberattack. Without mandated security testing, vulnerabilities may reach production. Without load testing, the system may fail under the 50-camera, 200-intersection scale target.
- **Where in PRD:** Section 7 "Testing, Simulation, and Shadow Mode" — functional testing only.
- **Suggested fix:** Add requirements for: load testing against MVP scale targets (with 2x headroom), penetration testing by an independent security firm before Shadow Mode, disaster recovery testing (failover, data recovery), and annual re-testing cadence.

**[SEVERITY: MAJOR]** — No performance benchmarks for operator-facing interactions
- **What's wrong:** The PRD defines data freshness latency (< 5 seconds for cameras) but no UI performance benchmarks: page load time, map rendering time, recommendation display latency, or approval-to-execution latency.
- **Why it matters:** With a 3-minute time-to-mitigation target, every second of UI latency matters. If the Dependency Graph takes 30 seconds to render, the workflow breaks.
- **Where in PRD:** Data freshness is in Section 1; no UI performance requirements exist.
- **Suggested fix:** Define UI performance targets: Live Grid map rendering < 2 seconds, Incident Mode transition < 1 second, recommendation display < 3 seconds after generation, approval-to-execution < 5 seconds (for live signal control).

**[SEVERITY: MINOR]** — Synthetic data for staging environment lacks validation standards
- **What's wrong:** Section 7.3 requires "anonymized or synthetic data" for the staging environment but does not define the fidelity requirements for synthetic data or how it will be validated as representative of production conditions.
- **Why it matters:** If synthetic data does not realistically represent production traffic patterns, staging tests will not surface real-world issues.
- **Where in PRD:** Section 7.3 "Staging Environment."
- **Suggested fix:** Define synthetic data requirements: must replicate production-scale volume, include realistic incident scenarios, cover edge cases (rush hour, weather events, special events), and be validated against historical production data distributions.

---

### 10. Degraded Operations & Failure Modes

**[SEVERITY: MAJOR]** — Queued actions after recovery have no expiration or staleness handling
- **What's wrong:** Section 8 states that when signal controller connectivity is restored, "the platform automatically resumes normal operation, processes any queued actions (with operator re-confirmation for signal changes)." There is no expiration policy for queued actions. A signal timing change queued during a 2-hour outage may be completely irrelevant when connectivity returns.
- **Why it matters:** Executing stale queued actions could worsen traffic conditions. The incident that prompted the change may have cleared hours ago.
- **Where in PRD:** Section 8 "Recovery behavior."
- **Suggested fix:** Define a queue expiration policy: queued actions expire after a configurable period (e.g., 15 minutes). Expired actions are logged but not re-presented for confirmation. All queued actions require re-assessment of current conditions before re-approval.

**[SEVERITY: MAJOR]** — No SLA or uptime targets defined for the platform
- **What's wrong:** The PRD does not define availability targets (e.g., 99.9% uptime), maximum acceptable downtime, or maintenance window policies. Section 10 mentions monitoring "platform uptime" but sets no target.
- **Why it matters:** Municipal contracts require SLAs. Without uptime targets, the team cannot design redundancy, failover, or maintenance strategies. The degraded operations table addresses failure responses but not prevention targets.
- **Where in PRD:** Section 10 "Deployment and Operations" — monitoring mentioned without targets.
- **Suggested fix:** Define platform availability targets (e.g., 99.9% for core monitoring, 99.5% for AI Copilot), maximum planned downtime per month, maintenance window policies, and RTO/RPO targets for each subsystem.

**[SEVERITY: MINOR]** — Complete outage notification assumes independent SMS/email systems
- **What's wrong:** The degraded operations table states "Automated notification sent to all registered TOC operators and Data Engineering via SMS/email" during complete platform outage. This assumes the notification system itself survives the outage.
- **Why it matters:** If the notification system is co-located with the platform, a complete outage takes down notifications too. Operators won't know the system is down.
- **Where in PRD:** Section 8 degraded operations table — "Complete platform outage" row.
- **Suggested fix:** Specify that the outage notification system must be hosted independently of the main platform (separate infrastructure, separate provider). Define a heartbeat monitoring architecture where an external watchdog detects platform absence and triggers notifications.

**[SEVERITY: MINOR]** — Partial AI degradation (reduced accuracy without full failure) is not addressed
- **What's wrong:** The degraded operations table covers "AI/Copilot pipeline down" (complete failure) but not partial degradation — scenarios where the AI is technically functioning but producing lower-quality recommendations (e.g., due to a model regression or partial data loss).
- **Why it matters:** Partial degradation is more insidious than complete failure because the system appears operational but its outputs are unreliable. Operators may trust degraded recommendations.
- **Where in PRD:** Section 8 degraded operations table — binary failure states only.
- **Suggested fix:** Add a "Degraded AI Quality" row to the table. Define detection criteria (e.g., recommendation block rate exceeds 5%, confidence scores trending below threshold), operator notification, and automatic actions (increase confidence threshold, display prominent warning).

---

### 11. Integration & External Dependencies

**[SEVERITY: MAJOR]** — No weather data integration despite critical impact on traffic
- **What's wrong:** Weather is one of the most significant factors affecting traffic flow, incident rates, and safe signal timing. The PRD does not include weather data as a data source, and the AI recommendations have no weather context.
- **Why it matters:** AI recommendations generated without weather awareness could be unsafe (e.g., extending green phases during icy conditions when stopping distances increase) or ineffective (predicting normal flow patterns during a snowstorm).
- **Where in PRD:** Section 1 "Data Integration" — weather not listed among data sources.
- **Suggested fix:** Add weather data (NWS API or similar) as a required data source. Weather conditions should be inputs to the AI recommendation engine and the simulation model. At minimum, extreme weather alerts should modify the AI's confidence scoring.

**[SEVERITY: MAJOR]** — Transit GPS telemetry source and provider are unidentified
- **What's wrong:** Section 1 lists "Public transit GPS telemetry (real-time bus and rail positions)" as a data source but never identifies the specific provider, API, data format, or access requirements.
- **Why it matters:** Transit data access varies enormously by agency. King County Metro, Sound Transit, and Community Transit all have different data systems. Without identifying the source, the integration cannot be planned.
- **Where in PRD:** Section 1 "Data Integration and City Model."
- **Suggested fix:** Identify the specific transit agencies and data sources (e.g., King County Metro GTFS-RT feed, Sound Transit real-time API). Document access requirements, data formats, and rate limits for each.

**[SEVERITY: MAJOR]** — GTFS/GTFS-RT standardization not mentioned for transit data
- **What's wrong:** GTFS (General Transit Feed Specification) and GTFS-RT (real-time) are the industry standard formats for transit data. The PRD does not reference these standards, creating ambiguity about how transit data will be ingested and normalized.
- **Why it matters:** Without specifying GTFS-RT compliance, the team may build custom integrations for each transit agency rather than leveraging standardized feeds, increasing development cost and fragility.
- **Where in PRD:** Section 1 — transit data described generically without format specification.
- **Suggested fix:** Specify GTFS-RT as the standard ingestion format for transit vehicle positions. Define the mapping from GTFS entities to city model entities (TransitVehicle, route, stop).

**[SEVERITY: MINOR]** — No event calendar integration for predictive awareness
- **What's wrong:** Major events (sports games, concerts, protests, construction) significantly impact traffic patterns. The PRD does not include event data as a source, limiting the system to purely reactive operations.
- **Why it matters:** An AI system that doesn't know about the Seahawks game ending in 15 minutes will be blindsided by 70,000 fans leaving the stadium. This contradicts the "proactive, not reactive" problem statement.
- **Where in PRD:** Problem Statement identifies "Reactive, Not Proactive Systems" as a problem; feature set includes no proactive data sources.
- **Suggested fix:** Add event calendar data (city permits, stadium schedules) as a data source for MVP or explicitly defer to Phase 2's "predictive congestion modeling" with a note about this dependency.

**[SEVERITY: MINOR]** — Existing TOC tools referenced for fallback but no integration specified
- **What's wrong:** Multiple sections reference operators falling back to "existing out-of-band tools (phone, radio)" and "existing legacy tools and standard operating procedures." These tools are never inventoried or integrated.
- **Why it matters:** Seamless fallback requires knowing what operators fall back to. If the platform could display the relevant radio channel or phone number during degraded operations, the fallback would be faster.
- **Where in PRD:** Section 8 degraded operations table — multiple references to existing tools.
- **Suggested fix:** Inventory the existing TOC tools and SOPs. At minimum, display relevant contact information and SOP references within the platform's degraded-mode UI. Consider integrating with the TOC's existing communication systems.

---

### 12. Success Metrics & Measurement

**[SEVERITY: CRITICAL]** — Time-to-Mitigation measures from an unknowable event
- **What's wrong:** The metric is defined as "Median time from physical incident occurrence to the first active adjustment." The platform cannot know when a physical incident actually occurred — it can only know when it was detected. Measuring from occurrence requires ground truth that doesn't exist in real-time.
- **Why it matters:** This metric cannot be measured as defined. The team will either redefine it informally (creating inconsistency) or use detection time as a proxy without stating the assumption.
- **Where in PRD:** "Success Metrics (MVP)" — Time-to-Mitigation definition.
- **Suggested fix:** Redefine as "Median time from incident detection (by the platform or operator) to the first approved signal timing adjustment." This is measurable with platform data alone.

**[SEVERITY: MAJOR]** — "100% adoption" is binary and unrealistic as a metric
- **What's wrong:** The adoption metric requires "100% of TOC operators utilizing the platform for major incident response during the pilot phase." This is binary — one non-participating operator means failure.
- **Why it matters:** 100% adoption targets are never achievable in practice (operators on leave, sick, in training, newly hired). This metric will either be perpetually failing or informally relaxed to meaninglessness.
- **Where in PRD:** "Success Metrics (MVP)" — Adoption metric.
- **Suggested fix:** Redefine as a usage metric: "≥ 90% of major incident responses during the pilot phase utilize the platform as the primary coordination tool." This is measurable, achievable, and meaningful.

**[SEVERITY: MAJOR]** — No baselines defined for any metric
- **What's wrong:** All six success metrics lack baselines. What is the current time-to-mitigation without the platform? What is the current AI acceptance rate? Without baselines, improvement cannot be measured.
- **Why it matters:** A "< 3 minutes" time-to-mitigation target is meaningless if the current process takes 2 minutes or 20 minutes. Baselines are essential for demonstrating value.
- **Where in PRD:** "Success Metrics (MVP)" — no baselines anywhere.
- **Suggested fix:** Add a requirement to measure baselines for each metric during the pre-deployment period (using current tools and processes). Present all metrics as both absolute targets and improvement deltas.

**[SEVERITY: MAJOR]** — AI acceptance rate could be gamed by overly conservative recommendations
- **What's wrong:** The "≥ 85% acceptance rate" metric measures operator acceptance of AI recommendations. An AI that only recommends obvious, low-impact changes would achieve high acceptance rates while providing little value.
- **Why it matters:** This metric incentivizes conservative AI behavior, which contradicts the product's goal of proactive, high-impact mitigation. The metric measures agreement, not quality.
- **Where in PRD:** "Success Metrics (MVP)" — Quality/Efficacy metric.
- **Suggested fix:** Supplement acceptance rate with an effectiveness metric: "≥ 70% of accepted AI recommendations result in measurable congestion improvement within 10 minutes." This measures outcome, not just agreement.

**[SEVERITY: MINOR]** — No system reliability or data quality metrics defined
- **What's wrong:** The success metrics focus on AI quality and operational speed but include no platform reliability metrics (uptime, data feed availability, response time) or data quality metrics.
- **Why it matters:** A system that achieves great AI accuracy but has 80% uptime or regularly drops camera feeds is not successful. Reliability is a prerequisite for all other metrics.
- **Where in PRD:** "Success Metrics (MVP)" — no reliability metrics. Section 10 mentions monitoring these but sets no targets.
- **Suggested fix:** Add success metrics for: platform uptime (≥ 99.5%), data feed availability (≥ 95% of feeds meeting freshness thresholds), and P95 UI response time (< 2 seconds).

**[SEVERITY: MINOR]** — Shadow Mode validation methodology is undefined
- **What's wrong:** The success metric states "≥ 90% of AI recommendations are retrospectively confirmed as appropriate by TOC operators in blind review" but does not define the blind review methodology, sample size, selection criteria, or review protocol.
- **Why it matters:** Without a defined methodology, the review could be biased (reviewing only easy cases), insufficient (too small a sample), or inconsistent (different reviewers using different criteria).
- **Where in PRD:** "Success Metrics (MVP)" — Shadow Mode Validation metric.
- **Suggested fix:** Define the blind review protocol: random sample of N recommendations per week, blinded to AI confidence score, reviewed by operators who were not the original responders, using a standardized rubric (appropriate / inappropriate / insufficient data to judge).

---

### 13. Constraints & Assumptions

**[SEVERITY: MAJOR]** — "Publicly available" data assumption conflicts with camera feed reality
- **What's wrong:** The constraints state "The MVP relies purely on existing, publicly available or municipal data sources." Live traffic camera feeds from regional transportation authorities are typically not publicly available — they require data sharing agreements, API keys, and often fees.
- **Why it matters:** If the team assumes camera feeds are freely available, they will discover access restrictions during integration, blocking the most critical data source.
- **Where in PRD:** "Constraints and Assumptions" — "publicly available or municipal data sources."
- **Suggested fix:** Clarify: "publicly available" means available through existing municipal data sharing agreements, not free public APIs. Identify which data sharing agreements are already in place and which must be negotiated (and add negotiation timelines to the project plan).

**[SEVERITY: MAJOR]** — 50 cameras across 200 intersections provides only 25% coverage
- **What's wrong:** The MVP scale target is 50 cameras for 200 intersections. This means 75% of intersections have no camera coverage. The anomaly detection system, which relies on camera feeds, will have massive blind spots.
- **Why it matters:** AI recommendations based on 25% camera coverage may be unreliable. The system cannot detect incidents at 150 of its 200 modeled intersections. This undermines the "automated anomaly detection" feature.
- **Where in PRD:** "Constraints and Assumptions" — MVP scale targets.
- **Suggested fix:** Acknowledge the coverage gap explicitly. Define how the system handles intersections without camera coverage (relying on transit GPS, speed data from other sources, or marking them as "unmonitored" in the city model). Adjust AI confidence scoring to account for coverage gaps.

**[SEVERITY: MINOR]** — No budget, timeline, or staffing constraints stated
- **What's wrong:** The PRD lists technical constraints and assumptions but no project constraints: budget, timeline, team size, or development methodology (agile sprints, etc.).
- **Why it matters:** Without project constraints, the feature set could require 5 engineers or 50. The scope cannot be validated against available resources.
- **Where in PRD:** "Constraints and Assumptions" — technical constraints only.
- **Suggested fix:** Add project constraints: target launch date (or range), development team size and composition, budget envelope, and sprint/release cadence. If these are in a separate project plan, reference that document.

---

### 14. Scalability & Future-Proofing

**[SEVERITY: MAJOR]** — No architectural scaling plan beyond MVP targets
- **What's wrong:** MVP scale targets are 50 cameras, 200 intersections, 500 transit vehicles, and 10 operator sessions. The PRD does not describe how the architecture will scale beyond these limits, or what the next scale tier looks like.
- **Why it matters:** If the MVP architecture is designed tightly for 200 intersections, scaling to city-wide (2,000+ intersections) may require a complete rewrite. Architecture decisions made now will constrain or enable Phase 2.
- **Where in PRD:** "Constraints and Assumptions" — MVP scale targets. No scaling discussion.
- **Suggested fix:** Add a scalability section: define the target scale for Phase 2 (e.g., 500 cameras, 1,000 intersections), identify the architectural decisions that must be made in MVP to enable this scale (stateless services, horizontal scaling, partitioned data), and flag any MVP decisions that would become bottlenecks.

**[SEVERITY: MINOR]** — Phase 2 multi-agency ontology may require rearchitecting the city model
- **What's wrong:** Phase 2 plans to "extend the city model to be accessible by additional municipal departments (public works, utilities) as a shared digital twin." If the MVP city model is designed solely for traffic entities, extending it to utilities and public works may require fundamental schema changes.
- **Why it matters:** If extensibility is not considered in MVP architecture, Phase 2 could require a costly migration or parallel system.
- **Where in PRD:** Appendix B "Phase 2 Preview" — Multi-agency shared ontology.
- **Suggested fix:** Add an extensibility requirement to the MVP city model design: the entity/relationship schema must support the addition of new entity types (utilities, public works assets) without schema-breaking changes. This does not require implementing those entities — just designing for their future addition.

**[SEVERITY: MINOR]** — No API versioning strategy for external integrations
- **What's wrong:** The PRD describes integrations with external systems (camera feeds, transit GPS, signal controllers) but does not specify an API versioning strategy for the platform's own interfaces.
- **Why it matters:** As the platform evolves through phases, breaking API changes could disrupt integrations that other municipal systems depend on.
- **Where in PRD:** Sections 1 and 10 — no API versioning mentioned.
- **Suggested fix:** Require semantic versioning for all platform APIs, a deprecation policy (minimum 6-month notice before breaking changes), and a compatibility matrix for supported external system versions.

---

### 15. Internal Consistency

**[SEVERITY: CRITICAL]** — MVP scope claims "transit rerouting" but only signal timing is implemented
- **What's wrong:** The MVP Scope says "transit rerouting." The feature set (Sections 1-8) exclusively describes signal timing changes. The AI tool-calling interface (Section 4.1) only includes `adjust_signal_timing`, `query_city_model`, and `simulate_impact` — no transit rerouting tool exists.
- **Why it matters:** This is not just a missing feature — it's an internal contradiction between the product's stated purpose and its actual capabilities. Stakeholders reading the scope will expect rerouting; developers reading the features will not build it.
- **Where in PRD:** "MVP Scope" vs. Sections 3, 4.1, and 5.
- **Suggested fix:** Resolve the contradiction: either add transit rerouting capabilities (tool, feature, integration) or amend the scope statement to remove it. Do not leave this ambiguous.

**[SEVERITY: MAJOR]** — "Autonomously" in scope vs. HITL in non-goals and principles
- **What's wrong:** The MVP scope uses "autonomously recommend and execute." Non-Goals says "no full automation without HITL." Product Principles says "Humans remain strictly responsible for final approvals." The scope language contradicts two other sections.
- **Why it matters:** Legal counsel reviewing this document will flag "autonomously" as a liability risk. Engineers may interpret it as requiring autonomous execution capability.
- **Where in PRD:** "MVP Scope" vs. "Non-Goals" bullet 3 vs. "Product Principles — AI in the Loop."
- **Suggested fix:** Align all three sections on consistent language: the system recommends; humans approve; the system executes approved actions. Remove "autonomously" from the scope.

**[SEVERITY: MAJOR]** — I-5 use case conflicts with Downtown Seattle bounding box
- **What's wrong:** The representative use case references I-5, a WSDOT-managed highway. The constraints specify "Downtown Seattle traffic grid," which refers to city-managed streets. I-5 runs through downtown but is state infrastructure.
- **Why it matters:** This creates confusion about jurisdictional scope. Does the MVP monitor I-5 but only control city signals? Can it detect I-5 incidents? The operational model is unclear.
- **Where in PRD:** "Representative MVP Use Case" vs. "Constraints and Assumptions."
- **Suggested fix:** Clarify jurisdictional scope: the platform monitors city-managed intersections and signals only. I-5 incidents may be detected through camera feeds if available, but signal control is limited to city-managed infrastructure. Update the use case to reflect this.

**[SEVERITY: MINOR]** — Hallucination metric defined differently in two sections
- **What's wrong:** Section 4.5 defines the hallucination rate as "recommendations blocked / total recommendations generated." The Success Metrics section defines AI Safety as "< 2% of AI-generated recommendations are blocked by the physical constraint verification or hallucination detection systems." The success metric combines two distinct block reasons (physical constraint violations and hallucination), while Section 4.5 tracks hallucination alone.
- **Why it matters:** These are different metrics with the same target (< 2%). If physical constraint blocks are 1.5% and hallucination blocks are 1.5%, the combined rate is 3% (failing the success metric) even though each individual metric is below 2%.
- **Where in PRD:** Section 4.5 "Hallucination Monitoring" vs. "Success Metrics — AI Safety."
- **Suggested fix:** Define separate metrics: hallucination block rate (< X%) and physical constraint block rate (< Y%), with separate targets. Or explicitly state that the 2% target applies to the combined rate.

**[SEVERITY: MINOR]** — Data Engineers labeled "primary users" but feature set barely serves them
- **What's wrong:** The Users section calls Data Engineers a "Primary User (MVP)" alongside TOC Operators. The feature set provides TOC Operators with 8 major feature areas. Data Engineers get a staging environment (Section 7.3) and are mentioned in degraded operations alerts — that's it.
- **Why it matters:** The "primary" label implies equal design attention. In practice, the product is built for TOC Operators with Data Engineers as supporting infrastructure staff.
- **Where in PRD:** "Primary Users (MVP)" vs. feature set throughout.
- **Suggested fix:** Reclassify Data Engineers as "Supporting Users" or add Data Engineer-specific features to justify the "Primary" designation.

---

## Missing Sections or Topics

The following topics are entirely absent from the PRD and should be added:

1. **MVP Definition of Done** — No explicit completion criteria for the MVP milestone.
2. **Authentication and Identity Management** — No specification for how users authenticate (MFA, SSO, smart cards).
3. **Network Security Architecture** — No specification for encryption, segmentation, or IT/OT boundary protection.
4. **Operator Training Program** — No training requirements, curriculum, or certification process.
5. **Performance Requirements (UI)** — No response time, rendering time, or latency targets for operator-facing interfaces.
6. **Platform Availability / SLA Targets** — No uptime, RTO, or RPO requirements.
7. **Incident Lifecycle Management** — No formal incident states, transitions, or resolution workflow.
8. **Notification and Alerting Subsystem** — No specification for out-of-band alerts (audible, push, SMS, pager).
9. **Model Lifecycle Management** — No specification for AI/ML model selection, versioning, updates, or rollback.
10. **Accessibility / ADA Compliance** — No WCAG or Section 508 requirements.
11. **MUTCD Compliance Specification** — No explicit reference to the federal standard governing signal timing.
12. **Project Constraints** — No budget, timeline, team size, or development methodology.

## Internal Contradictions

| # | Section A | Section B | Contradiction |
|---|-----------|-----------|---------------|
| 1 | MVP Scope: "autonomously recommend and execute" | Non-Goals: "Fully automate traffic control without HITL" is a non-goal | "Autonomously execute" contradicts the requirement for human approval |
| 2 | MVP Scope: "transit rerouting" | Feature Set (Sections 1-8): only signal timing changes described | Scope promises a capability that no feature implements |
| 3 | Representative Use Case: "severe incident on I-5" | Constraints: "Downtown Seattle traffic grid" | I-5 is state (WSDOT) infrastructure, not city-managed |
| 4 | Section 4.5: hallucination rate = blocked/total | Success Metrics: AI Safety = blocked by physical constraint OR hallucination / total | Different denominators and numerators for ostensibly the same metric |
| 5 | Users: Data Engineers are "Primary Users (MVP)" | Feature Set: only staging environment serves Data Engineers | "Primary" designation is inconsistent with feature coverage |

## Ambiguities Requiring Clarification

1. **Shadow Mode burn-in duration:** "Minimum burn-in period" — what is the minimum? Days, weeks, months? Product owner must specify a concrete duration.
2. **Confidence threshold governed range:** "Operators can adjust the confidence threshold within a governed range" — what are the bounds? Product owner must define the minimum and maximum.
3. **Advisory Mode vs. Live Mode as MVP:** If signal controller integration is infeasible, is Advisory Mode an acceptable MVP? Product owner must make a binary decision and define success metrics for that scenario.
4. **3-5 data sources:** Are there 3 data sources or 5? The unidentified 1-2 sources create scope ambiguity. Product owner must either enumerate all sources or fix the count at 3.
5. **Concurrent change limit scope:** "No more than 5 signal timing modifications may be active simultaneously" — is this per operator, per incident, or system-wide? Product owner must clarify.
6. **Auto-revert opt-in:** Auto-revert "requires Admin approval to enable" — enable once globally, or per-incident, or per-intersection? Product owner must specify the granularity.
7. **Audit log retention confirmation:** Retention is "Minimum 7-year... to be confirmed with the city's legal and records management office." This is an open question, not a requirement. Product owner must resolve before architecture decisions are made.
8. **"Significant incident" threshold:** Incident Mode triggers when "a significant incident is detected" — what quantitative threshold defines "significant"? Product owner must define this in terms of measurable criteria (severity score, number of lanes blocked, estimated delay).
9. **Camera feed redaction point of capture:** Redaction must occur "at the point of capture" — does this mean on the camera hardware, on an edge processing device, or at the first platform ingestion point? Product owner must clarify the architectural boundary.
10. **Emergency Dispatcher exclusion scope:** Non-goals exclude "Emergency Dispatcher workflows" but Appendix A references incidents that would trigger emergency dispatch. Does the platform receive incident data from dispatch? Product owner must clarify the data flow boundary with emergency services.
