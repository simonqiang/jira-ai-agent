# Scrum Master Jira Assistant — Weekly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement a selected week's detailed plan task-by-task. This document is a weekly delivery roadmap, not authorization to execute all weeks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a personal Scrum Master assistant through weekly increments that provide Jira search, reliable sprint reports, standards-based ticket drafting and approved writes, then semantic search.

**Architecture:** One Google ADK agent calls typed Python services for Jira search, reporting and ticket preparation. PostgreSQL stores history, approvals and later vectors; server-side authorization applies to every data path. Start with one user and one Scrum board, then add shared-team operation as a separate extension. The pilot deploys a simplified topology — one Cloud Run service, Cloud Scheduler-driven idempotent reconciliation instead of Cloud Tasks, direct webhook ingestion with deduplication — while keeping module boundaries so the shared-team split is a deployment change, not a rewrite.

**Tech Stack:** Python, Google ADK, Gemini, FastAPI, PostgreSQL/pgvector, a server-rendered HTML interface (Jinja/HTMX; a React SPA is deferred to the shared-team phase), and Google Cloud hosting for the unattended collector and pilot.

**Spec:** [Product requirements and architecture](../specs/2026-09-27-scrum-master-agent-design.md). The preceding design review identified reporting, synchronization, permission and evaluation gaps; their resolution is assigned below.

## Capacity and scope

- Planning assumption: one developer with basic Python/web experience, working 10–12 hours per week. Twelve delivery weeks represent approximately 120–144 hours, not a guaranteed completion date. Learning ADK/Jira, access approvals and unexpected API behavior can extend this.
- Weeks are relative to your actual start. No calendar dates, Jira issues or scheduled reminders are created by this plan.
- First release is a personal pilot with one allowlisted user, one Jira site, one explicitly supported Scrum board and its required project scope. Jira Cloud remains provisional until Week 1 confirms it.
- Shared-team onboarding/OAuth administration is a separate extension. This intentionally narrows the original multi-user requirement for the first release; it does not claim that the personal pilot completes shared-team support.
- Each week has one outcome, a small task list, a Friday demo and an exit check. Finish an incomplete dependency before starting the next week that needs it.
- At 4–6 hours/week, initially spread each delivery week across two calendar weeks. At 20+ hours/week, combine weeks only after their exit checks pass; observing real sprint boundaries still takes calendar time.
- Budget 7–8 hours for the main deliverable, 2 hours for verification/demo and 1–2 hours for rework or learning. Re-estimate after Week 2 using actual throughput.
- Reserve two to four contingency weeks beyond the twelve — more if ADK and Google Cloud are new to you. Treat them as planned buffer: carry unfinished correctness work into them rather than weakening exit checks.

## Global constraints

The following constraints are carried from the design:

- “App roles never grant additional Jira permissions.”
- “The reporting service computes numbers in Python/SQL.”
- “The approval route is an authenticated application operation; the model cannot approve its own draft.”
- “Do not expose unrestricted HTTP, SQL or arbitrary code execution to the model.”
- “Estimates, priority, assignee and sprint placement remain explicit human choices.”
- “Store timestamps in UTC and render in the configured team timezone.”

No phase includes bulk writes, issue deletion, sprint administration, autonomous prioritization or individual performance scoring. Evidence and permissions are release requirements, including for the personal pilot.

## Review focus

| Failure mode from the review | Owning week and required check |
|---|---|
| Done-before-start, reopening and closure-time rollover distort completion | Week 1 defines the rules; Week 6 checks event fixtures and one manually reviewed sprint. |
| Missed events, lagging search and expired webhook subscriptions create false completeness | Week 4 checks recovery/checkpoints; Week 6 prevents an incomplete report from becoming final. |
| Revoked access remains visible in history, exports or model context | Week 2 establishes checks; Weeks 3, 5 and 10 exercise chat, report and retrieval paths. |
| Board filters span projects or project types differ | Week 1 records supported configuration; Week 2 tests the actual board scope. |
| No-match queries or missing ticket details cause invented answers | Week 3 checks abstention; Week 7 checks questions instead of invented requirements; Week 12 reruns both. |
| Untrusted issue text is injected into exports or rendered reports (spreadsheet formulas, markup) | Week 5 sanitizes and tests exports; Week 12 reruns the checks. |
| Report generation blocks the conversation and times out | Week 5 makes report generation asynchronous with a job handle and status tool. |
| A timed-out create leaves an unknown duplicate ticket | Week 8 writes a correlation marker and reconciles by search before declaring unknown. |
| Collector credentials expire silently | Week 1 records the token-versus-3LO decision; Week 4 monitors credential expiry as a collection-freshness alarm. |
| Webhook setup presumes an authentication method the pilot may not use | Week 1's decision selects the Week 4 webhook branch; scheduled polling with stated limitations is the fallback. |

## Phase overview

| Phase | Weeks | Outcome at phase exit |
|---|---|---|
| 1. Foundation and useful search | 1–3 | Ask questions about your selected board and receive authorized Jira evidence. |
| 2. Reliable sprint reporting | 4–6 | Generate current and evidence-supported historical reports with clear freshness and limitations. |
| 3. Ticket preparation and controlled changes | 7–9 | Draft, review, create and update individual tickets using your team's rules. |
| 4. Semantic search and personal pilot | 10–12 | Find related work and use the integrated assistant in daily Scrum Master work. |
| 5. Shared-team extension, optional | 13–16, re-estimate after pilot | Multiple people use their own identities with verified isolation. |

## Proposed module boundaries

These are future paths to guide implementation; this planning task does not create application code. Design each week's exact interfaces and tests when that week starts.

| Proposed location | Responsibility | Introduced |
|---|---|---|
| `src/scrum_agent/app.py`, `config.py` | Application startup and validated configuration | Week 1 |
| `src/scrum_agent/jira/client.py`, `models.py` | Typed Jira reads, writes, pagination and field mapping | Week 1 onward |
| `src/scrum_agent/auth/` | Trusted identity, permission checks and connection lifecycle | Week 2 onward |
| `src/scrum_agent/search/`, `agent/` | Structured search, ADK tools and source-backed responses | Weeks 2–3 |
| `src/scrum_agent/storage/`, `sync/`, `migrations/` | Snapshots, change history, ingestion jobs and checkpoints | Week 4 |
| `src/scrum_agent/reports/` | Metric inputs, calculations, report narrative and exports | Weeks 5–6 |
| `src/scrum_agent/tickets/`, `templates/` | Versioned team policy, draft validation, previews and execution | Weeks 7–9 |
| `src/scrum_agent/retrieval/` | Chunking, embeddings, hybrid retrieval and citations | Weeks 10–11 |
| `web/` | Small authenticated server-rendered UI (Jinja/HTMX): search, reports, drafts and approvals | Week 3 onward |
| `tests/`, `evals/` | Behavioral tests and representative user tasks, grouped by feature | Every implementation week |

## Week 1 — Establish a working Jira foundation

**Weekly goal:** “I can run the project and retrieve a known ticket and my board configuration.”

- [ ] Confirm Jira deployment, personal-pilot scope, board type, project scope, estimate field, timezone and permitted Google Cloud region/budget. If Data Center is selected, revise the adapter plan before continuing.
- [ ] Decide pilot authentication by comparing the options accurately: a scoped personal API token (per-token scopes, selectable 1–365 day expiry, central `api.atlassian.com/ex/jira/{cloudId}` endpoints) versus OAuth 3LO from the start. The shared-team phase requires 3LO; building it early avoids a collector migration. Either way, add expiry monitoring for the unattended collector.
- [ ] Collect three representative ticket examples and one manually prepared sprint report; record current report preparation time as a baseline. Anonymize the examples before they become fixtures.
- [ ] Set up minimal CI (lint and tests) and pre-commit secret scanning; no credential ever enters source control.
- [ ] Set up the Python application, configuration and a typed Jira connection; fetch one known issue and board metadata without exposing credentials.
- [ ] Write a metric-policy decision record: use “initial planned scope”; distinguish done-by-end from completed-during-sprint; define pre-closure state, rollover and human-confirmed Sprint Goal outcome. Update the specification's metric table with these decisions as an explicit exit criterion, so the specification no longer carries the older cutoff-state definitions an implementer could follow by mistake.

**Friday demo:** Run the application and show the selected board, estimate/status mapping and a known issue link.

**Done when:** A clean local setup works from the README, secrets are external to source control, the supported configuration is explicit, and sample data is available for later checks.

## Week 2 — Search Jira accurately

**Weekly goal:** “I can find the correct tickets for a board, sprint and set of filters.”

- [ ] Implement issue-key lookup, board/sprint selection and typed filters for status, assignee, issue type and labels.
- [ ] Implement complete pagination, bounded requests and actionable handling of access denial and rate limits.
- [ ] Establish trusted user/site scope and centralized access checks that later reports and retrieval must reuse.
- [ ] Build a small checked query set containing empty results, ambiguous sprint names, multiple pages and a board spanning the allowed project scope.

**Friday demo:** Find unresolved bugs in the selected sprint and compare issue IDs with Jira.

**Done when:** Expected issue sets match the checked queries; unauthorized tickets are excluded; ambiguity prompts a selection; empty results are reported accurately.

## Week 3 — Ask the ADK agent in natural language

**Weekly goal:** “I can ask a Jira question conversationally and verify the answer from its sources.”

- [ ] Wrap Week 2 operations as narrow ADK tools with validated inputs and structured results.
- [ ] Measure model calls and tokens per completed task during agent development; record them as the cost baseline the specification requires before any cost conclusion.
- [ ] Add a small single-user interface for chat, context selection, issue links and follow-up questions. Start with short-lived conversations until persisted context has source/access tracking.
- [ ] Make the agent resolve ambiguity, distinguish missing data from zero and avoid unsupported claims.
- [ ] Verify that issue text cannot grant permissions or change tool rules; test no-match and revoked-access follow-ups. Never silently reuse restricted earlier context.

**Friday demo:** Ask “Show unresolved bugs in this sprint” and then “Which ones are explicitly blocked?”

**Done when:** The agent returns correct cited results for the checked query set, asks necessary clarification and has no Jira write capability.

**Milestone:** A useful local read-only assistant.

## Week 4 — Collect sprint history continuously

**Weekly goal:** “My sprint data continues to be collected when my laptop is off.”

- [ ] Add PostgreSQL migrations for snapshots, issue events, board configuration and ingestion checkpoints.
- [ ] Enable automated backups when the database is introduced. Persistent ADK sessions (DatabaseSessionService, user-isolated) are also due here, but they are this week's first deferrable item: if the week runs long, keep Week 3's short-lived conversations and move sessions into the contingency buffer rather than compressing collection reliability.
- [ ] Deploy a minimal private collector on the approved cloud setup: one Cloud Run service plus a Cloud Scheduler idempotent reconciliation endpoint; the Cloud Tasks queue arrives in Week 5 when asynchronous reports need it. Implement webhook ingestion per the Week 1 authentication choice — OAuth-app webhooks with signed JWT bearer validation under 3LO; administrator-configured webhooks with `X-Hub-Signature` HMAC validation for a token pilot with admin rights; otherwise scheduled polling only, with stated history limitations — and give the webhook endpoint authenticated public ingress while scheduler, application and write endpoints keep separate access controls. Webhooks are hints only; include webhook renewal if dynamic subscriptions are used.
- [ ] Monitor credential expiry: an expiring or expired token must surface as a collection-freshness alarm, not as silent gaps in history.
- [ ] Deduplicate events, checkpoint all-page ingestion and recover from interruption. Treat webhook events as hints and reconcile against authoritative data.
- [ ] Record sync freshness, gaps and configuration versions. Set an initial target of a successful reconciliation every 15 minutes; represent this as collection freshness, not proof that Jira indexing has no lag.

**Friday demo:** Change a sandbox ticket, show its persisted history, stop/restart the collector and demonstrate recovery.

**Done when:** Collection runs unattended for 48 hours, replayed events do not duplicate history, outages are visible and the recovery path has been exercised.

**Scope rule:** Keep the hosted surface to the collector and required endpoints. The essential outcome is reliable unattended collection; every other task added this week defers to it, persistent sessions first. A missed snapshot must create a completeness warning. If cloud access is delayed, use an approved always-on host or extend this week; laptop-only collection cannot meet this goal.

## Week 5 — Generate a useful current-sprint report

**Weekly goal:** “I can generate and export a report for my current sprint.”

- [ ] Calculate current scope, status counts, configured estimates and explicit blockers in Python/SQL using a persisted set of report inputs.
- [ ] Generate a short narrative with issue references, Sprint Goal, impediments and decisions needed. Goal achievement stays human-confirmed or unknown.
- [ ] Add report selection/view and Markdown/CSV export, including freshness, estimate coverage and completeness labels. Sanitize CSV cells beginning with `=`, `+`, `-` or `@` and escape rendered markup.
- [ ] Make report generation asynchronous: `build_sprint_report` returns a job handle with an estimate and a `get_report` tool reports status and results; no blocking tool call in the conversation loop. Execute jobs durably with a Cloud Tasks queue targeting the same Cloud Run service: persist job state in PostgreSQL, and enforce idempotency in the application through persisted job identity and execution state. Verify both an instance restart and a duplicate delivery — Cloud Tasks is at-least-once.
- [ ] Revalidate access for every contributing issue before generation/export; check historical/cache paths as well as live search. Exclude unauthorized data from both details and totals.

**Friday demo:** Generate a report for your board, verify totals manually and export the same authorized result.

**Done when:** Current-state numbers match a checked dataset; exported totals match the UI; exports resist formula and markup injection; revoked access does not reappear in context or reports. Historical metrics without sufficient inputs remain unavailable.

## Week 6 — Make historical sprint reports trustworthy

**Weekly goal:** “I can explain what happened during a closed sprint, with evidence for the calculations.”

- [ ] Implement the Week 1 metric policy using ordered changelog events as the primary source (membership, status and estimate changes live in issue history), with snapshots as cross-checks and for board configuration: initial scope, additions/removals, estimate changes, completion and rollover.
- [ ] Check done-before-start, reopening, missing estimates, parent/subtask rules, cross-project scope, timezone boundaries and ambiguous closure ordering.
- [ ] Finalize a report only when relevant ingestion has reconciled and required historical evidence is present; retain immutable metric inputs and policy version. Otherwise return a provisional/partial report with specific missing evidence.
- [ ] Compare one suitable closed sprint with a manually checked report; document intentional differences from Jira. Add same-board velocity trends only for sufficiently supported sprints.

**Friday demo:** Show one completed-during-sprint issue, one scope change and one unfinished/rolled-over issue with the supporting event history.

**Done when:** Fixture results match hand-calculated expectations and a real sprint review finds no unexplained differences. If no sufficiently observed sprint has closed, pass the fixture checks but keep real-history validation open; independent ticket work can continue.

**Milestone:** Search and sprint reporting are usable for personal work, with explicit history limits.

## Week 7 — Draft tickets using team standards

**Weekly goal:** “I can turn a rough request into a useful Story, Bug or Task draft.”

- [ ] Add versioned templates based on the Week 1 examples and project/type field metadata. Store them as versioned YAML in the repository with the version recorded at load time; no administrative UI in the pilot.
- [ ] Generate editable drafts with context, scope, acceptance criteria, dependencies and open questions appropriate to the issue type.
- [ ] Separate required Jira fields, mandatory team policy and advisory writing suggestions. Retrieve mandatory templates by exact identity/version.
- [ ] Review at least two examples of each issue type, including incomplete input; require questions instead of invented business rules, estimates or bug evidence.

**Friday demo:** Turn an incomplete feature request into a draft, answer its missing-information questions and inspect the revised result.

**Done when:** Required-field validation works, the six reviewed examples preserve user intent, and drafting makes no Jira changes. Similar-ticket suggestions are explicitly deferred until Week 11.

## Week 8 — Create a ticket after exact-payload approval

**Weekly goal:** “I can review a draft and create exactly that ticket in Jira.”

- [ ] Persist drafts, payload hashes, approval identity/expiry and execution records; provide a preview and authenticated approval action.
- [ ] Revalidate Jira permissions and field metadata before creating one issue; verify the created issue and return its link.
- [ ] Invalidate approval after draft edits. Check duplicate clicks, expired approval and application restart.
- [ ] Handle ambiguous create timeouts: write a unique correlation marker (label or description footer) with every create, reconcile by searching for it, and mark the outcome unknown only when the marker cannot be found. Never automatically retry a potentially successful create.
- [ ] Exercise backup and restore of approval and execution records here, so Week 12 re-verifies rather than discovers.

**Friday demo:** Approve a sandbox draft, create it once and inspect the actual Jira fields and audit record.

**Done when:** Approved payload and created fields agree, the app prevents duplicate submission, and unapproved/edited/expired requests cannot execute.

## Week 9 — Update tickets without losing unrelated edits

**Weekly goal:** “I can review and apply a precise change to an existing ticket.”

- [ ] Fetch current issue content and generate a field-level diff that preserves unrelated fields and description sections.
- [ ] Extend the approval/execution flow to issue updates; re-read relevant fields before writing and reject a stale proposal.
- [ ] Check concurrent edits, partial failures, unrelated-field preservation and post-write verification. Document the remaining external-edit race where Jira provides no atomic precondition.
- [ ] Provide clear outcomes and an audit trail of requested and verified changes.

**Friday demo:** Add acceptance criteria to a sandbox ticket, then demonstrate that an intervening edit causes a conflict instead of silent overwrite.

**Done when:** Only reviewed changes are applied; stale diffs cannot execute; failures do not claim success.

**Milestone:** All three core workflows work: search, reports, and reviewed ticket creation/update. Semantic retrieval remains a later enhancement.

## Week 10 — Build permission-aware semantic search

**Weekly goal:** “I can find related tickets even when they use different wording.”

- [ ] Enable pgvector and index authorized descriptions plus approved examples; record source revision, content hash and embedding model/configuration. Chunk Atlassian Document Format content by document nodes and index the summary as a separate chunk.
- [ ] Combine metadata-filtered vector retrieval with full-text retrieval; deduplicate issue results.
- [ ] Recheck current source access and revision before any retrieved text reaches the model, batching the access check into one user-scoped JQL query; invalidate obsolete/deleted chunks.
- [ ] Evaluate paraphrases, exact identifiers, no-match requests, stale content and revoked-access cases against the structured-search baseline.

**Friday demo:** Find related retry/payment issues from a differently worded query and open the cited sources.

**Done when:** A 20-query development set demonstrates useful semantic matches; permission/revision checks pass. This is a development checkpoint, not the final quality gate.

## Week 11 — Integrate similar-ticket suggestions and measure quality

**Weekly goal:** “The assistant suggests useful related work while keeping my ticket requirements accurate.”

- [ ] Add related-ticket suggestions to search and drafting; clearly distinguish examples from requirements and potential duplicates from confirmed duplicates.
- [ ] Evaluate at least 50 held-out, labeled queries. For answerable queries, target a relevant authorized result in the top five in at least 90%; score no-match abstention separately.
- [ ] Review generated answers for source support and drafts for intent preservation; report duplicate-suggestion precision separately, with a suggested pilot gate of at least 80% across at least 20 reviewed suggestions.
- [ ] Tune chunking/ranking only where failures justify it; keep low-quality suggestions disabled while leaving core drafting available.

**Friday demo:** Draft a ticket, inspect related work, reject an irrelevant suggestion and confirm the draft still follows the actual request.

**Done when:** Retrieval gates pass or the feature is explicitly kept out of release; the complete evaluated set contains no unsupported factual claims or unauthorized content. Preserve the original design's semantic requirement as open if the feature is disabled.

## Week 12 — Run a personal pilot and decide readiness

**Weekly goal:** “I can use the integrated assistant for a working week and judge its practical value.”

- [ ] Deploy the integrated personal app behind authentication and a single-user allowlist, with the persistent sessions running since Week 4. Keep secrets outside the app image and configure cost/error visibility.
- [ ] Run search, reporting, drafting, creation and update scenarios; rerun permission, approval, model-behavior and export-injection checks against the pinned release configuration.
- [ ] Re-verify backup/restore and failure recovery (first exercised in Week 8) against the deployed configuration; document setup and operating steps.
- [ ] Use the app during a working week, record defects and report-preparation time, and prioritize the next backlog from observed friction.

**Friday demo:** Complete a realistic workflow from finding issues through generating a report and reviewing a ticket change.

**Done when:** Relevant release gates pass, no unresolved authorization/report-correctness/write-integrity defects remain, and known limitations are documented. Measure the design's latency targets on the agreed pilot data; unresolved performance issues require a revised scope or another iteration.

**Milestone:** Personal MVP release candidate. The proposed 50% report-time reduction requires observation over several real sprints; Week 12 establishes initial evidence and does not claim that longer-term outcome is already proven.

## Optional Phase 5 — Shared-team operation

Re-estimate after the personal pilot; reserve an initial four weekly slots if shared use is wanted.

| Week | Goal | Exit check |
|---|---|---|
| 13 | Separate application identity from each user's Jira OAuth connection | Two users connect independently; refresh, revocation and reconnect are handled without mixing credentials. |
| 14 | Isolate data and context across users | Cross-user tests cover issue security, sessions, historical reports, exports, approvals and retrieval; inaccessible data never enters another user's model context. |
| 15 | Operate multiple boards and team policies | Team admins configure supported boards/templates without granting Jira access; polling, retention, quotas and webhook registrations handle the agreed team scope. |
| 16 | Validate a small shared pilot | Multiple users complete representative workflows; load/cost observations and access tests meet the agreed release criteria. |

Confluence ingestion, scheduled report distribution, chat integrations and retrospective-action tracking remain separate backlog items. Each requires its own weekly goal and authorization model when prioritized.

## Requirement coverage

| Requirement | Delivery |
|---|---|
| FR-01 connection and context | Weeks 1–3 personal scope; Weeks 13–15 shared scope |
| FR-02 natural-language Jira search | Weeks 2–3 |
| FR-03 related work | Weeks 10–11 |
| FR-04 selected-sprint reports | Weeks 5–6; real-history check depends on evidence availability |
| FR-05 ticket drafts, including related examples | Week 7, enhanced in Week 11 |
| FR-06 reviewed updates | Week 9 |
| FR-07 approved execution | Weeks 8–9 |
| FR-08 team standards | Week 7 versioned templates; Week 15 shared administration |
| FR-09 provenance | Sources from Week 2; historical inputs Week 4; report provenance Weeks 5–6; write audit Weeks 8–9 |
| FR-10 report exports | Week 5 |

## Weekly working routine

1. **Start:** Select the week's goal, confirm prerequisites and break only that week's work into implementation tasks. Copy the relevant specification constraints and acceptance checks into those tasks.
2. **Build:** Work on one deliverable at a time. Use behavioral checks for calculations, permissions and side effects; keep the demo working as you integrate changes.
3. **Review:** Compare the result against the weekly exit checks, run the demo and record defects plus actual hours.
4. **Adapt:** Carry unfinished correctness work forward. Cut optional polish or stretch features before weakening permissions, metric accuracy or write approvals. Reforecast dependent weeks when needed.

Weekly note template:

```text
Week:
Goal:
Actual hours:
Demo / evidence:
Exit checks passed:
Remaining defects or missing evidence:
Decision: complete / continue next week
Next goal:
```

Only begin detailed implementation for a selected week when requested. The immediate next outcome is Week 1's working Jira connection and explicit board/report policy.
