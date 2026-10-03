# ADR-0003: Ticket quality checks and user-confirmed updates

Status: proposed (2026-10-03); not yet implemented.  
Design: [Ticket quality and confirmed updates](../superpowers/specs/2026-10-03-ticket-quality-and-confirmed-updates-design.md)

## Context

The user wants the assistant to validate ticket writing, suggest improvements, and
ask for confirmation before updating Jira. Week 9 already implements backend
proposals, approvals and verified updates, while chat has no write tools and the UI
has no review card. The drafting templates supply useful local writing policies.

## Decision

Use deterministic completeness checks and a bounded model assessment with verified
text evidence. Distinguish Jira schema requirements, team writing policy and advisory
suggestions. Report unknowns and writing readiness without a compliance score.

Connect chat to proposal preparation, then render a server-owned before/after card.
An explicit protected Confirm action approves its immutable payload and invokes the
existing update orchestrator. The model cannot approve or execute. Cancel/revise,
owner/session binding, expiry, proposal-level deduplication, raw rich-text checks and
uncertain-outcome reconciliation are part of that workflow.

## Alternatives considered

| Approach | Benefit | Cost / reason not selected |
| --- | --- | --- |
| Prompt-only review and chat approval | Small implementation | Weak repeatability; natural-language consent is ambiguous; no enforceable write boundary |
| Deterministic checks only | Predictable and inexpensive | Cannot judge business value or testability in unstructured writing |
| Deterministic checks plus AI assessment and review card | Useful semantic advice with enforceable confirmation | Requires structured assessment, review state and backend safety work |

## Consequences

Reuse the existing local stack, update allowlist, approval session and database.
Validation remains available without a database; update proposals require durable
storage. Every revision needs a new confirmation. Raw ADF must be retained to avoid
invisible formatting loss. Current JSON approval callers must adopt review tokens
and CSRF protection; legacy pending proposals require fresh review.

There is one additional bounded model call per complete validation, recorded in
usage. AI judgments remain fallible and visible as such. External Jira edits can
still race the reread and PUT. Bulk operations and multi-user hosting require a
separate design.
