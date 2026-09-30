# Scrum Master Jira Assistant — Weekly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement a selected week's detailed plan task-by-task. This document is a weekly delivery roadmap, not authorization to execute all weeks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a personal Scrum Master assistant through weekly increments that provide Jira search, reliable sprint reports, standards-based ticket drafting and approved writes, then semantic search.

**Architecture:** One Google ADK agent calls typed Python services for Jira search, reporting and ticket preparation. PostgreSQL stores history, approvals and later vectors; server-side authorization applies to every data path. Run the personal pilot entirely on the user's PC: one local FastAPI/ADK process bound to `127.0.0.1`, an optional local worker, and an OS scheduler for polling while the PC is on. No Google Cloud deployment, Vertex AI, public webhook or hosted collector is required.

**Tech Stack:** Python, Google ADK, Gemini API via a local `GEMINI_API_KEY` (or another configured local/provider model), FastAPI, local PostgreSQL/pgvector, a server-rendered HTML interface (Jinja/HTMX), and local environment/OS credential storage. A React SPA, Vertex AI, hosted deployment and shared-team operation are out of scope.

**Spec:** [Product requirements and architecture](../specs/2026-09-27-scrum-master-agent-design.md). The preceding design review identified reporting, synchronization, permission and evaluation gaps; their resolution is assigned below.

## Capacity and scope

- Planning assumption: one developer with basic Python/web experience, working 10–12 hours per week. Twelve delivery weeks represent approximately 120–144 hours, not a guaranteed completion date. Learning ADK/Jira, access approvals and unexpected API behavior can extend this.
- Weeks are relative to your actual start. No calendar dates, Jira issues or scheduled reminders are created by this plan.
- First release is a personal pilot with one allowlisted user, one Jira site, one explicitly supported Scrum board and its required project scope. Jira Cloud remains provisional until Week 1 confirms it.
- Shared-team onboarding/OAuth administration is out of scope. Re-open the design only if the personal PC pilot later needs multiple users.
- Each week has one outcome, a small task list, a Friday demo and an exit check. Finish an incomplete dependency before starting the next week that needs it.
- At 4–6 hours/week, initially spread each delivery week across two calendar weeks. At 20+ hours/week, combine weeks only after their exit checks pass; observing real sprint boundaries still takes calendar time.
- Budget 7–8 hours for the main deliverable, 2 hours for verification/demo and 1–2 hours for rework or learning. Re-estimate after Week 2 using actual throughput.
- Reserve two to four contingency weeks beyond the twelve — more if ADK, Jira APIs or local PostgreSQL are new to you. Treat them as planned buffer: carry unfinished correctness work into them rather than weakening exit checks.

## Global constraints

The following constraints are carried from the design:

- “App roles never grant additional Jira permissions.”
- “The reporting service computes numbers in Python/SQL.”
- “The approval route is an authenticated application operation; the model cannot approve its own draft.”
- “Do not expose unrestricted HTTP, SQL or arbitrary code execution to the model.”
- “Estimates, priority, assignee and sprint placement remain explicit human choices.”
- “Store timestamps in UTC and render in the configured team timezone.”
- “Bind the web interface to `127.0.0.1`; do not expose the PC-only pilot to a LAN or the internet.”

No phase includes bulk writes, issue deletion, sprint administration, autonomous prioritization or individual performance scoring. Evidence and permissions are release requirements, including for the personal pilot.

## Review focus

| Failure mode from the review | Owning week and required check |
|---|---|
| Done-before-start, reopening and closure-time rollover distort completion | Week 1 defines the rules; Week 6 checks event fixtures and one manually reviewed sprint. |
| Missed polling windows and lagging search create false completeness | Week 4 checks recovery/checkpoints; Week 6 prevents an incomplete report from becoming final. |
| Revoked access remains visible in history, exports or model context | Week 2 establishes checks; Weeks 3, 5 and 10 exercise chat, report and retrieval paths. |
| Board filters span projects or project types differ | Week 1 records supported configuration; Week 2 tests the actual board scope. |
| No-match queries or missing ticket details cause invented answers | Week 3 checks abstention; Week 7 checks questions instead of invented requirements; Week 12 reruns both. |
| Untrusted issue text is injected into exports or rendered reports (spreadsheet formulas, markup) | Week 5 sanitizes and tests exports; Week 12 reruns the checks. |
| Report generation blocks the conversation and times out | Week 5 makes report generation asynchronous with a job handle and status tool. |
| A timed-out create leaves an unknown duplicate ticket | Week 8 writes a correlation marker and reconciles by search before declaring unknown. |
| Collector credentials expire silently | Week 1 records the token-versus-3LO decision; Week 4 monitors credential expiry as a collection-freshness alarm. |
| A stopped PC creates collection gaps | Reports record freshness and gaps; scheduled polling is the only pilot synchronization mechanism. |

## Phase overview

| Phase | Weeks | Outcome at phase exit |
|---|---|---|
| 1. Foundation and useful search | 1–3 | Ask questions about your selected board and receive authorized Jira evidence. |
| 2. Reliable sprint reporting | 4–6 | Generate current and evidence-supported historical reports with clear freshness and limitations. |
| 3. Ticket preparation and controlled changes | 7–9 | Draft, review, create and update individual tickets using your team's rules. |
| 4. Semantic search and personal pilot | 10–12 | Find related work and use the integrated assistant in daily Scrum Master work. |

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

- [x] Confirm Jira deployment, personal-pilot scope, board type, project scope, estimate field and timezone. If Data Center is selected, revise the adapter plan before continuing. *(Jira Cloud, project GACD, board 23031, filter scope, Story Points field and Asia/Hong_Kong timezone are recorded in ADR-0001. No hosting region or cloud budget is required for the PC-only pilot.)*
- [x] Decide pilot authentication: use a scoped personal API token (selectable 1–365 day expiry, central `api.atlassian.com/ex/jira/{cloudId}` endpoints) and monitor expiry. OAuth 3LO is out of scope for the PC-only pilot. *(Decided: scoped personal token — ADR-0001.)*
- [ ] Collect three representative ticket examples and one manually prepared sprint report; record current report preparation time as a baseline. Anonymize the examples before they become fixtures. *(Examples drafted from real tickets in `docs/samples/` — pending pilot-user review; the manual sprint-report baseline and its minutes-spent figure remain to be filled in.)*
- [x] Set up minimal CI (lint and tests) and pre-commit secret scanning; no credential ever enters source control. *(GitHub Actions + pre-commit with gitleaks.)*
- [x] Set up the Python application, configuration and a typed Jira connection; fetch one known issue and board metadata without exposing credentials. *(Implemented and regression-tested. Live verification 2026-09-27: `python -m scrum_agent probe` exits 0 against board 23031 — issue link, board metadata, filter ID, estimate field and column/status-ID mapping. See the Week 1 verification note.)*
- [x] Write a metric-policy decision record: use “initial planned scope”; distinguish done-by-end from completed-during-sprint; define pre-closure state, rollover and human-confirmed Sprint Goal outcome. Update the specification's metric table with these decisions as an explicit exit criterion, so the specification no longer carries the older cutoff-state definitions an implementer could follow by mistake. *(ADR-0002 written; spec §4 table updated.)*

**Friday demo:** Run the application and show the selected board, estimate/status mapping and a known issue link.

**Done when:** A clean local setup works from the README, secrets are external to source control, the supported configuration is explicit, and sample data is available for later checks.

## Week 2 — Search Jira accurately

**Weekly goal:** “I can find the correct tickets for a board, sprint and set of filters.”

- [x] Implement issue-key lookup, board/sprint selection and typed filters for status, assignee, issue type and labels. *(Implemented in `src/scrum_agent/search/` (`IssueFilters` compiled to quoted, project-scoped JQL; `SearchService` with sprint resolution that prompts on ambiguity) plus client sprint endpoints; CLI `sprints`/`search` commands. Verified by mocked tests; the live Friday demo is pending.)*
- [x] Implement complete pagination, bounded requests and actionable handling of access denial and rate limits. *(Sprint listing follows `startAt`/`isLast` with bounded pages and rejects incomplete/malformed pages; issue search already followed `nextPageToken`; 401/403/429 raise typed errors with actionable messages and `Retry-After`.)*
- [x] Establish trusted user/site scope and centralized access checks that later reports and retrieval must reuse. *(`src/scrum_agent/auth/PilotScope` is now the single scope authority: the client, search service and checked queries all delegate to it; out-of-scope issues, boards and sprints fail closed.)*
- [x] Build a small checked query set containing empty results, ambiguous sprint names, multiple pages and a board spanning the allowed project scope. *(`tests/checked_queries.py`: 14 queries over a fixture Jira implementing the real pagination contracts — empty results, exact/substring/ambiguous/unknown sprint names, multi-page search and sprint listing, cross-project results and cross-board sprints denied. Week 3 must answer the same set.)*

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

**Weekly goal:** “My sprint data is collected reliably while my local collector is running.”

- [x] Add PostgreSQL migrations for snapshots, issue events, board configuration and ingestion checkpoints. *(`migrations/001_init.sql` + `scrum-agent migrate` runner: collection runs, issue snapshots with tombstones, changelog events deduped on (issue, entry, item), board-config versions with change detection, ingestion checkpoints, freshness view. Applied twice live; second run applies nothing.)*
- [x] Configure local PostgreSQL backups and persistent ADK sessions (user-isolated). Use the PC's backup tooling; test restoring the database and application state. *(`docker-compose.yml` loopback Postgres; `pg_dump`/`psql` restore walkthrough in README, exercised live — 256 issues/1,285 events intact after restore. ADK `DatabaseSessionService` used when `SCRUM_AGENT_DATABASE_URL` is set; session persistence across restart verified live. `pg_dump` one-liners documented; wiring the user's own backup scheduler remains theirs.)*
- [x] Run a local collector/worker and an idempotent reconciliation command. Configure cron or Windows Task Scheduler only if unattended polling while the PC is on is useful. Do not implement public webhooks in the PC-only pilot; scheduled polling is the authoritative freshness mechanism. *(`scrum-agent collect` idempotent cycle; `--full` reconciles whole scope. Live cold start: 256 issues, 1,285 events, no duplicate events on replay. Scheduling documented as a crontab line, not configured; no webhooks.)*
- [x] Monitor credential expiry: an expiring or expired token must surface as a collection-freshness alarm, not as silent gaps in history. *(`SCRUM_AGENT_TOKEN_EXPIRES_ON` — Jira cannot report token expiry (ADR-0001) — drives `freshness`: expired ⇒ ALARM + exit 1, ≤7 days ⇒ WARNING. Verified live.)*
- [x] Deduplicate events, checkpoint all-page ingestion and recover from interruption. Treat polling results as hints and reconcile against authoritative data. *(`collect --full` replay of 256 issues added 0 duplicate events; `kill -9` mid-run left a visible stale row that the next run marks `interrupted` and recovers from; every hint is reconciled by an authoritative per-issue detail + changelog read; 404/403 tombstones, 401 aborts.)*
- [x] Record sync freshness, gaps and configuration versions. Set a practical local polling interval and represent it as collection freshness, not proof that Jira indexing has no lag. *(Freshness view + `scrum-agent freshness` report last success age, failures since success and tombstoned gaps; board-config versions stored per change; 1-hour overlap window documented as the polling interval representation.)*

**Friday demo:** Change a sandbox ticket, show its persisted history, stop/restart the collector and demonstrate recovery. *(Collection side verified 2026-09-30 — cold start 256 issues/1,285 events, replay dedup, kill/restart recovery, backup restore; sandbox-ticket edit and restart walkthrough remain for the pilot user's demo. See the [Week 4 note](notes/2026-09-30-week-4.md).)*

**Done when:** Collection runs reliably during a representative working session, replayed events do not duplicate history, stopped-process gaps are visible and the recovery path has been exercised.

**Scope rule:** Keep all components local. The essential outcome is reliable collection while the PC is running; every other task added this week defers to it, persistent sessions first. A missed snapshot must create a completeness warning. Do not add cloud deployment, public ingress or webhook registration to this pilot.

## Week 5 — Generate a useful current-sprint report

**Weekly goal:** “I can generate and export a report for my current sprint.”

- [x] Calculate current scope, status counts, configured estimates and explicit blockers in Python/SQL using a persisted set of report inputs. *(`reports/metrics.py` computes from Week 4 snapshots plus the versioned board configuration — done status via the board's mapped status IDs, estimates from the configured field with missing kept unknown, blockers from explicit `blocked` label or blocks-issue link only. Verified against the hand-computed fixture dataset.)*
- [x] Generate a short narrative with issue references, Sprint Goal, impediments and decisions needed. Goal achievement stays human-confirmed or unknown. *(Deterministic template: every claim traces to a computed field; goal outcome printed as unknown/human-confirmed; decisions limited to unassigned unfinished and missing-estimate issues; commitment history explicitly unavailable until Week 6.)*
- [x] Add report selection/view and Markdown/CSV export, including freshness, estimate coverage and completeness labels. Sanitize CSV cells beginning with `=`, `+`, `-` or `@` and escape rendered markup. *(`/reports` pages with sprint selector, HTML-escaped view, md/csv downloads of the same report object; `reports/exports.py` guards formula prefixes, escapes pipes/newlines; tests prove the exports resist injection and totals match the UI.)*
- [x] Make report generation asynchronous: `build_sprint_report` returns a job handle with an estimate and a `get_report` tool reports status and results; no blocking tool call in the conversation loop. Execute jobs with a local worker and durable PostgreSQL job state; enforce idempotency through persisted job identity and execution state. Verify both an application restart and duplicate local delivery. *(`migrations/002_report_jobs.sql` — one row per job, `request_key` = board/sprint/cutoff-hour identity; `ReportJobs.submit` is dedup-idempotent, `claim_next_report_job` requeues orphans; the web process runs a background worker, the CLI inline. Tests cover duplicate delivery, crash/restart recovery and error capture; gated round-trip on real Postgres.)*
- [x] Revalidate access for every contributing issue before generation/export; check historical/cache paths as well as live search. Exclude unauthorized data from both details and totals. *(Execution runs one scoped live `sprint = id` search; stored snapshots absent from it are excluded from details and totals with a named note and a `partial` completeness label — tested with a revoked fixture issue.)*

**Friday demo:** Generate a report for your board, verify totals manually and export the same authorized result.

**Done when:** Current-state numbers match a checked dataset; exported totals match the UI; exports resist formula and markup injection; revoked access does not reappear in context or reports. Historical metrics without sufficient inputs remain unavailable.

## Week 6 — Make historical sprint reports trustworthy

**Weekly goal:** “I can explain what happened during a closed sprint, with evidence for the calculations.”

- [x] Implement the Week 1 metric policy using ordered changelog events as the primary source (membership, status and estimate changes live in issue history), with snapshots as cross-checks and for board configuration: initial scope, additions/removals, estimate changes, completion and rollover.
- [x] Check done-before-start, reopening, missing estimates, parent/subtask rules, cross-project scope, timezone boundaries and ambiguous closure ordering. *(Synthetic event fixtures cover reopening, rollover, start-time estimate changes/missing values, UTC boundaries and subtask exclusion; scope continues to be enforced by `PilotScope`.)*
- [x] Finalize a report only when relevant ingestion has reconciled and required historical evidence is present; retain immutable metric inputs and policy version. Otherwise return a provisional/partial report with specific missing evidence. *(The persisted report JSON retains policy/evidence inputs. Missing boundary history or ambiguous ordered events yields partial/unavailable history with issue-specific evidence notes.)*
- [ ] Compare one suitable closed sprint with a manually checked report; document intentional differences from Jira. Add same-board velocity trends only for sufficiently supported sprints. *(Blocked on a suitable fully observed real closed sprint; the checked synthetic fixture is covered by automated tests.)*

**Friday demo:** Show one completed-during-sprint issue, one scope change and one unfinished/rolled-over issue with the supporting event history.

**Done when:** Fixture results match hand-calculated expectations and a real sprint review finds no unexplained differences. If no sufficiently observed sprint has closed, pass the fixture checks but keep real-history validation open; independent ticket work can continue.

**Milestone:** Search and sprint reporting are usable for personal work, with explicit history limits.

## Week 7 — Draft tickets using team standards

**Weekly goal:** “I can turn a rough request into a useful Story, Bug or Task draft.”

- [x] Add versioned templates based on the Week 1 examples and project/type field metadata. Store them as versioned YAML in the repository with the version recorded at load time; no administrative UI in the pilot. *(`drafting/ticket_templates/*.yaml`, loaded once via `load_templates`/`default_templates`; each carries its own `version` string, e.g. `story-v1`. Fields reflect the Week 1 examples' observed structure and this project's known mandatory content; no live createmeta call — editing the YAML and bumping the version is the pilot's "admin UI".)*
- [x] Generate editable drafts with context, scope, acceptance criteria, dependencies and open questions appropriate to the issue type. *(`drafting/draft.py:build_draft` renders every template section, plus `open_questions` for unmet required/team-policy fields.)*
- [x] Separate required Jira fields, mandatory team policy and advisory writing suggestions. Retrieve mandatory templates by exact identity/version. *(Each template field carries a `category`: `required_field`, `team_policy` or `advisory`; `get_template` requires an exact case-insensitive issue-type match, never a fuzzy guess.)*
- [x] Review at least two examples of each issue type, including incomplete input; require questions instead of invented business rules, estimates or bug evidence. *(`tests/test_drafting.py` covers a complete and an incomplete example per type, mirroring the Week 1 `docs/samples/*.example.md` gaps; missing content always becomes an `open_questions` entry, never invented text.)*

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

- [ ] Run the integrated personal app locally with a single-user allowlist and persistent sessions. Keep secrets outside source control and configure local logs/error visibility.
- [ ] Run search, reporting, drafting, creation and update scenarios; rerun permission, approval, model-behavior and export-injection checks against the pinned release configuration.
- [ ] Re-verify backup/restore and failure recovery (first exercised in Week 8) against the local configuration; document setup and operating steps.
- [ ] Use the app during a working week, record defects and report-preparation time, and prioritize the next backlog from observed friction.

**Friday demo:** Complete a realistic workflow from finding issues through generating a report and reviewing a ticket change.

**Done when:** Relevant release gates pass, no unresolved authorization/report-correctness/write-integrity defects remain, and known limitations are documented. Measure the design's latency targets on the agreed pilot data; unresolved performance issues require a revised scope or another iteration.

**Milestone:** Personal MVP release candidate. The proposed 50% report-time reduction requires observation over several real sprints; Week 12 establishes initial evidence and does not claim that longer-term outcome is already proven.

## Out of scope after the personal pilot

Shared-team identity, OAuth 3LO, multi-user isolation, hosted deployment, public
webhooks, Confluence ingestion, scheduled report distribution, chat integrations and
retrospective-action tracking are not part of this PC-only roadmap. Re-open the design
and create a new delivery plan only if shared use becomes a requirement.

## Requirement coverage

| Requirement | Delivery |
|---|---|
| FR-01 connection and context | Weeks 1–3 personal scope |
| FR-02 natural-language Jira search | Weeks 2–3 |
| FR-03 related work | Weeks 10–11 |
| FR-04 selected-sprint reports | Weeks 5–6; real-history check depends on evidence availability |
| FR-05 ticket drafts, including related examples | Week 7, enhanced in Week 11 |
| FR-06 reviewed updates | Week 9 |
| FR-07 approved execution | Weeks 8–9 |
| FR-08 team standards | Week 7 versioned templates |
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
