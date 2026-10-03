# Ticket Quality Review and Confirmed Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the chat agent review any ticket against the team's quality policy, and propose plus apply ticket updates from chat — each applied only after the user explicitly confirms the field-level diff.

**Architecture:** A deterministic quality checker (spec §3's three checks: schema validity, mandatory team rules, advisory writing quality) runs over data `get_issue` already returns and is exposed as a read-only `review_ticket` tool. Confirmed updates reuse the existing Week 9 `TicketUpdateService` proposal → approval → execution chain unchanged: two new tools (`propose_ticket_update`, `execute_confirmed_update`) make the chat agent a client of that service. No new database tables, no new dependencies.

**Tech Stack:** Python 3.11+, Google ADK function tools, Pydantic settings, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-27-scrum-master-agent-design.md` §3 (ticket quality policy), FR-06 (reviewed updates), FR-07 (approved execution).

## Global Constraints

- Only the first two §3 checks are hard failures; advisory writing quality never blocks readiness and never becomes a numeric score — findings explain specific weaknesses.
- Never invent business rules, reproduction steps, assignees or estimates; unknown mandatory details become questions for the user.
- No Jira write may happen without the existing approval chain: proposal (local DB row), explicit user confirmation in the conversation, then approve+execute through `TicketUpdateService` with its freshness recheck and read-back verification.
- The update tools are the only non-read-only agent tools; they may not accept raw JQL, credentials, bulk changes, status transitions or deletes — field updates only, via the existing service.
- Tests must not hit live Jira; fake the tool and storage layers (see `tests/agent_fakes.py`, `tests/storage_fakes.py`).
- Use `PYTHONPATH=src` for all local test commands.

## Review Focus

1. A proposal must apply exactly the confirmed diff or nothing: Task 3 tests that a ticket changed between propose and execute yields `rejected_stale`, and that execution without an approved proposal fails.
2. Advisory quality findings must never flip `ready` to false; only schema/mandatory findings may — Task 1 pins this with a draft that has every mandatory section filled and advisory gaps only.
3. The checker must not score or invent content: Task 1 asserts no numeric quality score appears and unknowns surface as questions, not as fabricated text.
4. `propose_ticket_update` must not touch Jira: Task 3 asserts proposal creation performs only the service's local write and one authorized read, while execution goes through the service's existing audit path.
5. Adding parameters to `make_tools`/`build_agent` must not break existing wiring: Task 3 keeps new parameters optional with `None` defaults and updates the agent fakes.

---

### Task 1: Deterministic ticket-quality checker

**Files:**
- Create: `src/scrum_agent/ticketing/quality.py`
- Create: `tests/test_ticket_quality.py`

**Interfaces:**
- Consumes: template metadata from `scrum_agent.drafting.templates` (`required_field`, `team_policy` and advisory sections — same vocabulary `build_draft` already uses).
- Produces: `review_ticket_fields(fields: dict[str, object], template: TicketTemplate) -> dict` returning `{"schema": [...], "mandatory": [...], "advisory": [...], "ready": bool, "questions": [...]}` — one entry per finding naming the section and the specific weakness; `ready` is false only for schema or mandatory findings.

- [ ] **Step 1: Write failing checker tests in `tests/test_ticket_quality.py`**

  Cover: a complete ticket passes with empty findings and `ready is True`; an empty mandatory section produces a hard finding and `ready is False`; advisory-only gaps keep `ready is True`; missing unknown mandatory details appear in `questions`; and no finding text ever fabricates content or emits a numeric score.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `PYTHONPATH=src pytest tests/test_ticket_quality.py -q`
  Expected: FAIL because `scrum_agent.ticketing.quality` does not exist.

- [ ] **Step 3: Implement `review_ticket_fields` in `src/scrum_agent/ticketing/quality.py`**

  Check each template section against the provided fields: empty/missing required or team-policy sections are mandatory findings, empty advisory sections are advisory findings with one-line explanations. Determine schema validity from the fields Jira already validated (`get_issue` payloads are authoritative; flag only what the checker can actually see). No scores, no invented text — unknowns become questions.

- [ ] **Step 4: Run the focused tests to verify GREEN**

  Run: `PYTHONPATH=src pytest tests/test_ticket_quality.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit the checker slice**

  ```bash
  git add src/scrum_agent/ticketing/quality.py tests/test_ticket_quality.py
  git commit -m "feat: add deterministic ticket quality checker"
  ```

### Task 2: Read-only `review_ticket` agent tool

**Files:**
- Modify: `src/scrum_agent/agent/tools.py`
- Modify: `src/scrum_agent/agent/payloads.py`
- Modify: `src/scrum_agent/agent/chat.py`
- Modify: `src/scrum_agent/agent/instructions.py`
- Modify: `tests/test_agent_tools.py`, `tests/test_agent_chat.py`, `tests/test_agent_instructions.py`

**Interfaces:**
- Consumes: `SearchService.get_issue` (already returns summary, description, acceptance criteria and ratings), `review_ticket_fields` from Task 1, template lookup from `scrum_agent.drafting`.
- Produces: agent tool `review_ticket(issue_key: str, issue_type: str = "Story") -> dict` with a `ok_quality_payload` in `payloads.py`, a rendered chat section in `chat.py`, and one instruction paragraph pinning the §3 wording (mandatory vs advisory, no scores, suggestions never become requirements).

- [ ] **Step 1: Write failing tool, rendering and instruction tests**

  Tool test: `review_ticket` fetches the issue once via the (fake) search service and returns findings with `ready`; an unknown issue key surfaces the existing `not_found` error payload. Chat test: the quality payload renders mandatory and advisory findings in separate sections. Instruction test: the agent instruction names the three checks and forbids numeric scores.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `PYTHONPATH=src pytest tests/test_agent_tools.py tests/test_agent_chat.py tests/test_agent_instructions.py -q`
  Expected: FAIL because the tool, payload and instruction text do not exist.

- [ ] **Step 3: Implement the tool, payload, rendering and instruction text**

  Map the issue payload onto the chosen template's fields, run `review_ticket_fields`, wrap in a payload following the existing `ok_*_payload` conventions, render like `_draft_answer` does, and register the `FunctionTool` in `make_tools`. Keep the tool read-only: one authorized `get_issue`, no Jira writes.

- [ ] **Step 4: Run the focused tests to verify GREEN**

  Run: `PYTHONPATH=src pytest tests/test_agent_tools.py tests/test_agent_chat.py tests/test_agent_instructions.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit the review-tool slice**

  ```bash
  git add src/scrum_agent/agent/tools.py src/scrum_agent/agent/payloads.py src/scrum_agent/agent/chat.py src/scrum_agent/agent/instructions.py tests/test_agent_tools.py tests/test_agent_chat.py tests/test_agent_instructions.py
  git commit -m "feat: add review_ticket quality tool to chat agent"
  ```

### Task 3: Confirmed updates in chat

**Files:**
- Modify: `src/scrum_agent/agent/tools.py`
- Modify: `src/scrum_agent/agent/payloads.py`
- Modify: `src/scrum_agent/agent/chat.py`
- Modify: `src/scrum_agent/agent/instructions.py`
- Modify: `src/scrum_agent/web/__init__.py`
- Modify: `tests/agent_fakes.py`, `tests/test_agent_tools.py`, `tests/test_agent_chat.py`, `tests/test_agent_instructions.py`, `tests/test_webapp.py`

**Interfaces:**
- Consumes: the existing `TicketUpdateService` (`propose_update`, `approve_update`, `execute_update`) and its `update_proposals`/`update_approvals`/`update_executions` audit rows — unchanged, no migration.
- Produces: agent tools `propose_ticket_update(issue_key: str, changes: dict[str, str]) -> dict` (local proposal + field-level diff `current → new` + `proposal_id`; no Jira write) and `execute_confirmed_update(proposal_id: int) -> dict` (approve + execute; surfaces `succeeded`/`rejected_stale`/`verification_failed`/`failed` with per-field verification). `make_tools`/`build_agent`/`ChatService` accept an optional `updates` service; the web app passes the instance it already builds.

- [ ] **Step 1: Write failing update-tool tests with fakes**

  Extend `tests/agent_fakes.py` with a fake update service. Assert: proposing returns the diff and `proposal_id` without any Jira write call; executing before approval fails; approve-then-execute succeeds and reports per-field verification; a stale proposal yields `rejected_stale`; `updates=None` produces a clear "ticket updates need the local database" error payload rather than a crash.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `PYTHONPATH=src pytest tests/test_agent_tools.py tests/test_agent_chat.py tests/test_agent_instructions.py tests/test_webapp.py -q`
  Expected: FAIL because the update tools and wiring do not exist.

- [ ] **Step 3: Implement the tools, wiring, rendering and instruction guardrails**

  Add both tools behind the optional `updates` service, thread it through `make_tools` → `build_agent` → `ChatService`, and pass the existing instance from the web app. Render the proposal as an explicit diff with the confirmation question, and the result with per-field outcomes. Instruction text: the agent must show the exact diff and ask for explicit confirmation before calling `execute_confirmed_update`, must report `rejected_stale`/`verification_failed` honestly, and must never widen a confirmed payload with extra fields.

- [ ] **Step 4: Run the focused tests to verify GREEN**

  Run: `PYTHONPATH=src pytest tests/test_agent_tools.py tests/test_agent_chat.py tests/test_agent_instructions.py tests/test_webapp.py tests/test_ticketing_updates.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit the confirmed-updates slice**

  ```bash
  git add src/scrum_agent/agent/tools.py src/scrum_agent/agent/payloads.py src/scrum_agent/agent/chat.py src/scrum_agent/agent/instructions.py src/scrum_agent/web/__init__.py tests/agent_fakes.py tests/test_agent_tools.py tests/test_agent_chat.py tests/test_agent_instructions.py tests/test_webapp.py
  git commit -m "feat: add confirmed ticket updates to chat agent"
  ```

### Task 4: Documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/notes/2026-10-03-week-12.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: documented agent tools and recorded verification evidence; no additional behavior.

- [ ] **Step 1: Update the README**

  Add `review_ticket`, `propose_ticket_update` and `execute_confirmed_update` to the agent-tools documentation, with the confirmation rule stated in one sentence: nothing reaches Jira until the user explicitly confirms the shown diff.

- [ ] **Step 2: Record evidence in the Week 12 note**

  Note the pilot-backlog items addressed (ticket quality review, confirmed chat updates), which automated gates passed, and that live end-to-end confirmation against real Jira remains a manual pilot step.

- [ ] **Step 3: Run the complete automated suite from this worktree**

  Run: `PYTHONPATH=src pytest -q`
  Expected: PASS with no external Jira calls.

- [ ] **Step 4: Run static checks**

  Run: `PYTHONPATH=src ruff check src tests && PYTHONPATH=src ruff format --check src tests`
  Expected: `All checks passed!`

- [ ] **Step 5: Commit the documentation and evidence**

  ```bash
  git add README.md docs/superpowers/notes/2026-10-03-week-12.md
  git commit -m "docs: document ticket quality review and confirmed updates"
  ```

## Self-review

- **Spec coverage:** Task 1 implements §3's three-check policy with advisory/mandatory separation and no scores; Task 2 surfaces it read-only in chat; Tasks 3 implements FR-06/07 through the existing proposal → approval → execution chain with explicit in-chat confirmation, freshness recheck and read-back verification untouched.
- **Type consistency:** Task 1 defines the checker Task 2 consumes; Task 3's optional `updates` parameter defaults to `None` everywhere so existing wiring and tests stay valid until the web app passes the service.
- **Review focus:** Every listed risk is assigned to an explicit test: stale/failed execution (Task 3 Step 1), advisory never blocks (Task 1 Step 1), no scores or invented content (Task 1 Step 1), proposal writes nothing to Jira (Task 3 Step 1), optional-parameter backward compatibility (Task 3 Step 1).
- **Scope:** No database migration, no new dependency, no bulk writes, status transitions, deletes or autonomous execution.
