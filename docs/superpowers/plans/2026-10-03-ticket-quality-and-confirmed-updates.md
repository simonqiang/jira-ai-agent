# Ticket Quality and Confirmed Updates Implementation Plan

**Goal:** Let the local pilot validate existing tickets against a versioned writing
policy and apply exact reviewed improvements only after an explicit confirmation.

**Spec:** [Ticket quality validation and confirmed updates](../specs/2026-10-03-ticket-quality-and-confirmed-updates-design.md)

**Constraints:** Keep the existing loopback, single-user scope. Do not add a model
write tool. Validation/preparation/revision/cancellation make no Jira write. Use
`PYTHONPATH=src` for tests. Never place real issue text, credentials, prompts, or
review payloads in committed fixtures or logs.

## 1. Define the quality policy and result contracts

**Files:** Create `src/scrum_agent/quality/__init__.py`, `models.py`, `policy.py`;
create `tests/test_ticket_quality_policy.py`.

- [ ] Write failing tests for Story/Bug/Task rules, blank content, placeholders,
  unsupported types, invalid rule IDs, and template-category mapping.
- [ ] Implement immutable Pydantic findings/reports and the `quality-v1` policy.
  Include Jira-required, team-policy, and advisory categories.
- [ ] Implement deterministic checks. Treat clear non-heading prose as semantically
  unknown instead of structurally missing.
- [ ] Run `PYTHONPATH=src pytest tests/test_ticket_quality_policy.py -q`.
- [ ] Commit `feat: define ticket quality policy`.

## 2. Add safe live-ticket validation

**Files:** Create `src/scrum_agent/quality/assessor.py`, `service.py`; modify
`src/scrum_agent/agent/usage.py` if required; create `tests/test_ticket_quality.py`
and `tests/test_quality_assessor.py`.

- [ ] Write tests with anonymized supported ticket examples, non-English prose,
  provider timeouts/malformed output, injected instructions, missing estimates, and
  fabricated AI excerpts.
- [ ] Implement `TicketQualityService.validate_ticket()` using existing scoped issue
  reads and `QualityAssessor.assess()` with strict structured output. Enforce one
  30-second/40,000-character call with no tools.
- [ ] Derive readiness in Python and retain deterministic findings on AI failure.
  Add transient conversation-bound report storage with 30-minute expiry.
- [ ] Run `PYTHONPATH=src pytest tests/test_ticket_quality.py tests/test_quality_assessor.py -q`.
- [ ] Commit `feat: validate ticket writing with evidence`.

## 3. Preserve exact Jira field values and rich documents

**Files:** Create `src/scrum_agent/jira/field_values.py`; modify
`src/scrum_agent/jira/client.py`, `src/scrum_agent/ticketing/updates.py`,
`tests/test_jira_client.py`, and `tests/test_ticketing_updates.py`; create
`tests/test_field_values.py`.

- [ ] Write failing tests for ADF paragraphs/lists/tables/links/marks, plain strings,
  labels, explicit clears, edit metadata, and visible-text-equal but raw-value-different
  content.
- [ ] Implement scoped edit metadata lookup, raw value extraction, exact Jira-payload
  encoding, and safe template-section replacement.
- [ ] Reject unknown fields before lookup; preserve untouched rich nodes; require a
  fully disclosed whole-field replacement when a section cannot be located safely.
- [ ] Run `PYTHONPATH=src pytest tests/test_field_values.py tests/test_jira_client.py tests/test_ticketing_updates.py -q`.
- [ ] Commit `fix: preserve rich Jira fields in reviewed updates`.

## 4. Make approval lifecycle durable and idempotent

**Files:** Create `src/scrum_agent/migrations/006_ticket_quality_reviews.sql`; modify
`src/scrum_agent/storage/repository.py`, `src/scrum_agent/ticketing/updates.py`,
`tests/storage_fakes.py`, `tests/test_storage.py`, and `tests/test_ticketing_updates.py`.

- [ ] Add failing tests for expiry, owner/conversation mismatch, cancellation,
  superseding, altered hash, concurrent confirmation, multiple approvals, stale
  values, pre-PUT failure, post-PUT timeout, and failed read-back.
- [ ] Persist review binding, raw base values, exact Jira fields, payload provenance,
  expiry, state, and a proposal-level unique execution claim. Backfill old executions
  and require fresh review for pending legacy proposals.
- [ ] Make approval/claim atomic and return the existing result on duplicate clicks.
  Add `outcome_unknown`; reconcile interrupted work by reading only.
- [ ] Run focused tests and gated disposable-PostgreSQL concurrency tests when
  `SCRUM_AGENT_TEST_DATABASE_URL` is configured.
- [ ] Commit `fix: enforce one confirmed execution per proposal`.

## 5. Add protected review cards and HTTP actions

**Files:** Create `src/scrum_agent/ticketing/review.py` and web review templates;
modify `src/scrum_agent/web/__init__.py`, `tests/test_webapp.py`; create
`tests/test_ticket_review.py`.

- [ ] Add failing tests for complete before/after values, explicit replacements,
  revise/supersede, cancellation, direct JSON bypass, bad CSRF/review tokens, wrong
  conversation, and execution/cancellation races.
- [ ] Implement prepare/get/revise/cancel review service methods and browser routes.
  Bind every ticket mutation endpoint to session CSRF and a review token.
- [ ] Implement the single Confirm action as approve plus execute of the frozen hash.
  All rejected actions must make zero Jira PUTs.
- [ ] Run `PYTHONPATH=src pytest tests/test_ticket_review.py tests/test_webapp.py -q`.
- [ ] Commit `feat: add protected ticket review confirmation`.

## 6. Connect validation and review to chat

**Files:** Modify `src/scrum_agent/agent/tools.py`, `instructions.py`, `chat.py`,
`payloads.py`, `src/scrum_agent/app.py`, templates, CSS, and their corresponding
agent/web tests.

- [ ] Add tests that confirm only `validate_ticket` and `prepare_ticket_update` are
  registered, model-created IDs are ignored, chat “yes” causes no write, and cards
  are resolved from trusted server records.
- [ ] Wire the quality/review services with existing dependencies. Collect actual
  tool artifacts in `TurnResult`; do not render model-provided action data.
- [ ] Render findings and accessible review cards with Confirm, Revise, and Cancel
  controls. Keep model output and ticket content escaped.
- [ ] Run agent and web focused tests, then manually inspect fake-transport browser
  flows at narrow and wide widths.
- [ ] Commit `feat: review ticket improvements in chat`.

## 7. Verify the complete workflow and update operations documentation

**Files:** Create `tests/test_ticket_review_workflow.py`; modify `README.md`,
`src/scrum_agent/pilot.py`, `tests/test_pilot.py`, and
`docs/superpowers/runbooks/week-12-personal-pilot.md`.

- [ ] Add end-to-end fake-transport scenarios for validate → prepare → revise →
  confirm → verified success, cancel, stale reviewed field, unrelated edit,
  lost response reconciliation, outcome unknown, and double confirmation.
- [ ] Document the live sandbox check that requires the displayed review-card action
  and independent Jira inspection. Do not automate live Jira writes.
- [ ] Run `PYTHONPATH=src pytest`, `ruff check .`, `ruff format --check .`, and
  `PYTHONPATH=src scrum-agent pilot-check`. Clearly record skipped live/database checks.
- [ ] Commit `docs: document confirmed ticket quality workflow`.

The feature is complete when the design acceptance criteria pass with fake transports,
no negative scenario writes to Jira, every confirmed proposal issues at most one PUT,
and the sandbox procedure is documented for manual pilot acceptance.

