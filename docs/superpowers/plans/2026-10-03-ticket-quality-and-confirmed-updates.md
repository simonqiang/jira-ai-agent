# Ticket Quality and Confirmed Updates Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task after the user requests implementation. Steps use checkbox syntax for tracking. This document does not authorize implementation or live Jira writes.

**Goal:** Validate existing ticket writing and apply proposed improvements only after the user confirms the exact reviewed changes.

**Architecture:** A quality service combines deterministic rules with a bounded structured model assessment. Chat prepares local proposals; server-rendered review cards and the existing update service enforce user confirmation, immutable payloads, ownership, and verified execution.

**Tech Stack:** Python 3.11–3.13, existing Google ADK/model adapter, Pydantic, FastAPI, Jinja2/htmx, PostgreSQL/psycopg, pytest and Ruff. No new runtime dependency is planned.

**Spec:** [Ticket quality and confirmed updates design](../specs/2026-10-03-ticket-quality-and-confirmed-updates-design.md). Read the full spec before executing any task.

## Global constraints

- One existing Story, Bug, or Task at a time in the configured local pilot scope.
- Validation, drafting, and preparing a proposal make zero Jira writes.
- The first release accepts confirmation only through its control.
- Expose no approve, execute, cancel, credentials, raw JQL, or arbitrary field-ID tool.
- New proposals expire after 24 hours. A confirmed approval retains the existing 15-minute lifetime.
- Reports are transient and keyed by browser conversation, with a 30-minute lifetime.
- Limit the semantic assessor to one call, 30 seconds, and 40,000 input characters.
- Preparing updates requires PostgreSQL; validation does not.
- Use `PYTHONPATH=src` for verification so imports use this worktree.
- Keep secrets, issue text, review payloads and prompts out of logs and committed fixtures.

## Review focus

1. Non-template prose and non-English text need semantic review rather than false missing-heading failures: Tasks 1–2.
2. Rich documents with identical visible text but different tables/marks must not bypass conflict detection: Task 3.
3. Repeated or simultaneous confirmations, including separate approval IDs, must produce at most one PUT: Task 4.
4. A timeout after PUT followed by a failed verification read must remain uncertain without retrying the write: Task 4.
5. Multiple cards, an old conversation, or model-invented proposal IDs must never approve the wrong payload: Tasks 5–6.

## Delivery order and file map

| Task | Deliverable | Main files |
| --- | --- | --- |
| 1 | Versioned rubric and typed report contracts | New `quality/{__init__,models,policy}.py` |
| 2 | Evidence-backed live ticket validation | New `quality/{assessor,service}.py`; existing usage adapter |
| 3 | Exact Jira field encoding and rich-content preservation | New `jira/field_values.py`; `jira/client.py` |
| 4 | Durable confirmation lifecycle and safe execution | Migration 006; `storage/repository.py`; `ticketing/updates.py` |
| 5 | Protected review service and HTTP operations | New `ticketing/review.py`; `web/__init__.py` |
| 6 | Chat validation tools and confirmation cards | Agent/chat/payload modules; new review templates |
| 7 | Integrated acceptance, operating docs and pilot gate | New scenario tests; README/runbook/pilot gate |

All paths below are relative to the repository root. Commit suggestions describe
future implementation slices; no commits or implementation are part of preparing
this proposal.

### Task 1: Quality contracts and policy

**Files:** Create `src/scrum_agent/quality/__init__.py`, `models.py`, `policy.py`;
create `tests/test_ticket_quality_policy.py`.

**Interfaces:** Define immutable Pydantic `QualityFinding`, `SemanticAssessment`,
`ValidationReport`, and `QualityPolicy` with strict extra-field rejection. Report
fields and enum values are exactly those in design §4. `SemanticAssessment` holds
`findings` and `open_questions`; it cannot set overall readiness. `QualityPolicy`
holds its `version`, supported `issue_types`, and rules with `rule_id`, `category`,
`applicable_types`, and `description`. Produce
`load_quality_policy(version: str = "quality-v1") -> QualityPolicy` and
`deterministic_findings(issue: Issue, policy: QualityPolicy) -> list[QualityFinding]`.
Use a Python policy definition; reuse template field categories without changing
existing YAML or the drafting readiness behavior.

- [ ] Write tests for empty/whitespace/placeholder content, all three types, unknown
  type, invalid enums, unknown rule IDs, and template category mapping. Assert Story
  role/benefit are writing policy, never inferred Jira schema requirements.
- [ ] Run `PYTHONPATH=src pytest tests/test_ticket_quality_policy.py -q`; expect
  failures until these contracts exist.
- [ ] Implement models, `quality-v1` rule registry and deterministic findings.
  Nonempty prose yields unknown semantic checks rather than fabricated passes;
  rules identify fields without requiring particular headings.
- [ ] Run the same command; require all cases to pass.
- [ ] Commit the isolated policy slice with `feat: define ticket quality policy`.

### Task 2: Scoped validation and bounded semantic assessment

**Files:** Create `src/scrum_agent/quality/assessor.py`, `service.py`;
modify `src/scrum_agent/agent/usage.py` only as needed to meter assessor responses;
create `tests/test_ticket_quality.py`, `tests/test_quality_assessor.py`.

**Interfaces:** Consume Task 1 contracts, `SearchService.get_issue`, the existing
`BaseLlm` adapter and `UsageRecorder`. Produce async
`QualityAssessor.assess(text: str, *, policy_version: str, issue_type: str,
conversation_id: str) -> SemanticAssessment`, async
`TicketQualityService.validate_ticket(issue_key: str, *, conversation_id: str)
-> ValidationReport`, and
`get_report(validation_id: str, *, conversation_id: str) -> ValidationReport`.
The service owns its transient report cache and injected clock; it accepts the
assessor by interface for fake-model testing. Runtime wiring is Task 6.

- [ ] Write tests using anonymized Story/Bug/Task payloads, clear prose/checklists,
  non-English content, missing capacity/estimates, unsupported type, denied reads,
  provider timeout/malformed output and fabricated excerpts. Assert source key,
  fetch time, policy version, `input_hash`, and exact readiness precedence.
- [ ] Run `PYTHONPATH=src pytest tests/test_ticket_quality.py tests/test_quality_assessor.py -q`;
  expect failures for missing service and assessor.
- [ ] Implement one tool-free assessment with strict output parsing, allowlisted
  rule IDs and excerpt verification. Enforce 30 seconds/40,000 characters; large
  content bypasses semantic assessment with explicit partial results. Derive
  readiness in Python; preserve deterministic failures when semantic review fails.
- [ ] Add cache ownership/30-minute expiry/reset tests and usage-accounting tests.
  Assert validation needs no database, issues carrying injected instructions cannot
  change policy or trigger tools, and one validation has at most one assessor call.
- [ ] Run the focused commands plus `PYTHONPATH=src pytest tests/test_auth_scope.py tests/test_agent_chat.py -q`;
  require passes and no mutation calls.
- [ ] Commit with `feat: validate ticket writing with evidence`.

### Task 3: Exact field encoding and raw rich-content safety

**Files:** Create `src/scrum_agent/jira/field_values.py`;
modify `src/scrum_agent/jira/client.py` and `src/scrum_agent/ticketing/updates.py`;
create `tests/test_field_values.py`; modify `tests/test_jira_client.py` and
`tests/test_ticketing_updates.py`.

**Interfaces:** Produce `JiraClient.get_edit_metadata(issue_key: str) -> dict`,
`read_raw_values(detail: dict, field_names: tuple[str, ...]) -> dict`, and
`encode_update_fields(changes: dict, edit_metadata: dict) -> dict`. Reuse scoped
`get_issue_detail()` for raw values. Produce
`replace_template_section(document: dict, section_name: str, text: str) -> dict`;
raise a typed validation error if the section boundary is missing/ambiguous.
Keep readable text in validation; use raw values for update base/hash/conflicts.

- [ ] Write tests for ADF paragraphs, lists, tables, media, links and marks; plain
  strings; the acceptance-criteria field's text versus textarea schema; labels and
  explicit clearing. Assert unaffected nodes remain identical in section edits.
- [ ] Run `PYTHONPATH=src pytest tests/test_field_values.py tests/test_jira_client.py tests/test_ticketing_updates.py -q`;
  new format/safety cases must fail before implementation.
- [ ] Implement scoped edit metadata retrieval and schema-aware exact encoding.
  Reject unknown field names before dictionary access; reject empty summaries and
  invalid types. Preserve raw documents; distinguish section edits from explicit
  whole-field replacement. Encode once before freezing a proposal.
- [ ] Add a case where raw marks change but visible text stays equal; assert a
  reviewed-field conflict. Assert metadata changes requiring different encoding
  demand fresh review rather than changing an approved payload.
- [ ] Run the focused tests and require existing supported field updates to remain
  functional with their new exact-payload contract.
- [ ] Commit with `fix: preserve rich Jira fields in reviewed updates`.

### Task 4: Durable review lifecycle and one execution per proposal

**Files:** Create `src/scrum_agent/migrations/006_ticket_quality_reviews.sql`;
modify `src/scrum_agent/storage/repository.py`,
`src/scrum_agent/ticketing/updates.py`, `tests/storage_fakes.py`,
`tests/test_storage.py`, `tests/test_ticketing_updates.py`.

**Interfaces:** Extend proposal rows with `conversation_id`, `policy_version`,
`validation_input_hash`, `jira_fields`, `raw_base`, `expires_at`, and `review_state`.
Add transactional repository operations:
`approve_review(proposal_id: int, *, approver: str, conversation_id: str,
expected_hash: str, now: datetime) -> dict`,
`change_review_state(proposal_id: int, *, creator: str, conversation_id: str,
new_state: str, now: datetime) -> dict`, and
`claim_update_execution(proposal_id: int, approval_id: int, *, now: datetime)
-> tuple[dict, bool]`, returning `(execution, newly_claimed)`.
Keep existing read/audit APIs; route `TicketUpdateService` approval/execution through
these atomic operations. Add `outcome_unknown` to the execution status constraint.
Produce `reconcile_interrupted_updates() -> list[dict]` for startup read-only recovery.

- [ ] Write tests for expired proposals/approvals, wrong owner/conversation, modified
  stored hashes, cancelled/superseded proposals, duplicate confirmations and two
  approval IDs for one proposal. Assert `put_count <= 1` for concurrent execution.
- [ ] Run `PYTHONPATH=src pytest tests/test_ticketing_updates.py tests/test_storage.py -q`;
  the newly specified lifecycle cases should fail.
- [ ] Implement migration/backfill exactly as design §7, preserving all historical
  execution rows. Use row locks and the unique proposal claim inside transactions;
  retry a uniqueness collision by loading its recorded execution, never by PUT.
- [ ] Implement expiry/state/owner/hash checks before claiming; only the claim owner
  performs the scoped metadata check, raw reread, PUT and verification. Treat known
  pre-PUT errors as failed; uncertain write/read failures as `outcome_unknown`.
- [ ] Test crash points before PUT, after PUT and during read-back. Reconciliation
  performs only reads; matching fields can reconcile to success, other outcomes
  require inspection. A consumed proposal cannot be reset into a writable state.
- [ ] Run the focused suite. Run gated PostgreSQL concurrency tests only with
  `SCRUM_AGENT_TEST_DATABASE_URL` set to a disposable database; record pass/skip.
- [ ] Commit with `fix: enforce one confirmed execution per proposal`.

### Task 5: Protected review service and confirmation routes

**Files:** Create `src/scrum_agent/ticketing/review.py`;
modify `src/scrum_agent/web/__init__.py`;
create `tests/test_ticket_review.py`; modify `tests/test_webapp.py`.

**Interfaces:** Define `ReviewCard` with `proposal_id`, `issue_key`, `diff`,
`payload_hash`, `review_state`, `expires_at`, `quality_cautions`,
`unresolved_questions`, `replacement_warnings`, and `confirmable`.
Implement `TicketReviewService` preparation/get/cancel signatures from design §6.
Implement its `revise_review(proposal_id: int, changes: dict, *, creator: str,
conversation_id: str) -> ReviewCard` contract for atomic replacement/superseding.
Consume trusted user/conversation and quality report references; no model-supplied
identity. Add review GET, cancel POST and browser confirm POST as specified.
Extend existing JSON approval calls to require `expected_payload_hash`, `review_token`,
and session CSRF protection. Legacy unbound pending proposals require new preparation.

- [ ] Write tests for complete exact before/after values, selected-field updates,
  explicit clear/replacement warnings, no-op proposals, revision/superseding and
  cancellation. Assert each non-confirm action performs zero PUTs.
- [ ] Run `PYTHONPATH=src pytest tests/test_ticket_review.py tests/test_webapp.py -q`;
  the new review/confirmation cases should fail.
- [ ] Implement protected session review tokens and CSRF validation for ticket
  mutations, including existing JSON endpoints. Server derives identity; token
  claims bind the exact hash, proposal, user, conversation and expiry. Encode safe
  error responses; do not return raw upstream error bodies.
- [ ] Implement confirmation composition: approve the protected frozen review and
  immediately execute via Task 4. Refuse pending assumptions/placeholders; permit
  partial improvements with visible remaining cautions and explicit acknowledgment.
- [ ] Test missing/bad CSRF, forged/expired token, wrong conversation, reset, old
  legacy proposals, direct JSON bypass attempts, and cancellation racing execution.
  Assert blocked requests have zero PUTs; in-progress cancellation gives conflict.
- [ ] Run the focused suites and `PYTHONPATH=src pytest tests/test_ticketing.py -q`
  to retain ticket creation's approval boundary after shared CSRF changes.
- [ ] Commit with `feat: add protected ticket review confirmation`.

### Task 6: Agent tools, wiring and browser review cards

**Files:** Modify `src/scrum_agent/agent/tools.py`, `instructions.py`, `chat.py`,
`payloads.py`, `src/scrum_agent/app.py`, `src/scrum_agent/web/__init__.py`,
`web/templates/_turn.html`, `web/static/chat.css`;
create `web/templates/_ticket_validation.html`, `_ticket_review.html`,
`_ticket_update_result.html`;
modify `tests/test_agent_tools.py`, `test_agent_chat.py`,
`test_agent_instructions.py`, `test_webapp.py`, and `tests/agent_fakes.py`.

**Interfaces:** Add optional injected `quality` and `reviews` dependencies to
`make_tools`, `build_agent` and `ChatService`; preserve existing call defaults.
Task 2 quality service is always wired when chat has a configured model; Task 5
review service is wired only with the existing local storage. Tools are exactly
`validate_ticket(issue_key)` and `prepare_ticket_update(issue_key, changes,
validation_id=None)`; trusted tool context supplies identity/conversation.
`TurnResult` and `TurnView` gain empty-default `validation_reports` and
`update_proposal_ids` fields. Web resolves IDs by owner and store before rendering.

- [ ] Write fake-runner tests for validate/prepare intent, missing database, two
  simultaneous proposals, chat “yes,” and model-invented IDs. Assert no approve or
  execute tool is registered, and actions come only from successful tool artifacts.
- [ ] Run `PYTHONPATH=src pytest tests/test_agent_tools.py tests/test_agent_chat.py tests/test_agent_instructions.py tests/test_webapp.py -q`;
  new tool/card scenarios should fail.
- [ ] Wire services during startup; share the existing scoped Jira client and
  storage; call interrupted-update reconciliation before accepting confirmation
  work. Offload synchronous storage/Jira work in async routes/tools using the
  project's asyncio/thread pattern so chat remains responsive.
- [ ] Update instructions to describe validation and proposal preparation, preserve
  untrusted-data/source rules, and direct chat confirmation to the card. Preserve
  existing draft/report/retrieval behavior and usage recording.
- [ ] Collect structured tool artifacts in `run_turn`; render findings and full
  before/after values from server data. Add exact confirmation prompt and
  **Confirm update to <key>**, **Review revised changes**, **Cancel** controls.
  Use escaped content, accessible labels, status announcements, and responsive
  values; disable controls while executing and render verified status fragments.
- [ ] Run focused suites. Inspect local browser flows with fake transports: good and
  poor tickets, long values, keyboard confirmation, cancel/revise, stale result,
  unknown result, narrow viewport and two cards. Never use a live issue for automated
  UI writes. Record observations when implementation is executed.
- [ ] Commit with `feat: review ticket improvements in chat`.

### Task 7: Integrated acceptance and operating documentation

**Files:** Create `tests/test_ticket_review_workflow.py`;
modify `src/scrum_agent/pilot.py`, `tests/test_pilot.py`, `README.md`,
`docs/superpowers/runbooks/week-12-personal-pilot.md`;
create `docs/superpowers/notes/ticket-quality-acceptance.md` during implementation.

**Interfaces:** Consume the complete workflow. Include focused quality/review suites
in the existing `pilot-check` gate. Document the new behavior as shipped only after
verification; explain rubric categories, limited fields, explicit confirmation,
expiry, rich-content replacement, and uncertain-result recovery.

- [ ] Write end-to-end fake transport scenarios: validate → prepare → revise →
  confirm → verified success; cancel without write; edited field → stale rejection;
  unrelated edit survives; PUT response lost → read reconciliation; read-back lost
  → uncertainty; duplicate click → one PUT. Assert exact request body and sources.
- [ ] Run `PYTHONPATH=src pytest tests/test_ticket_review_workflow.py -q`; failures
  identify integration gaps that must be fixed before completion.
- [ ] Update the pilot gate/runbook and fix those integration gaps. Add a manual
  sandbox checklist requiring a review card confirmation for the live write, plus
  independent Jira inspection. No unattended or automatically approved live writes.
- [ ] Run `PYTHONPATH=src pytest` and `ruff check .` and
  `ruff format --check .`; record final outcomes. Run `PYTHONPATH=src scrum-agent pilot-check`
  with fake/offline gates. Report any database or live acceptance checks separately
  as skipped or pending rather than claiming they passed.
- [ ] Record anonymized quality-review evidence for at least one good and poor
  example of each supported type; inspect false positives with the pilot user.
  Require zero unconfirmed mutation calls in all negative scenarios and at most one
  PUT per confirmed proposal. Record provider latency/usage without ticket text.
- [ ] Commit with `docs: document confirmed ticket quality workflow`.

## Handoff and completion criteria

Recommended execution order is Tasks 1–7 in this session's normal implementation
workflow when the user asks to proceed. Validation can be demonstrated after Task 2;
new update controls should be enabled only after Tasks 3–6 satisfy the confirmation
and execution gates. No calendar date or rollout commitment is assumed.

This enhancement is complete when the design's ten acceptance checks are covered,
the targeted and existing regression suites pass, the browser flow works with fake
transports, and the operating docs reflect actual behavior. Live sandbox acceptance
is a separate explicitly confirmed action, never implied by this planning document.
