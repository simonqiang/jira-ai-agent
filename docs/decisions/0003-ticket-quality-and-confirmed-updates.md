# ADR-0003: Ticket quality validation and user-confirmed updates

## Status

Proposed — 2026-10-03

## Context

The pilot user wants the Scrum Master Jira assistant to assess ticket writing,
suggest improvements, and update an existing ticket only after an explicit user
confirmation. The current application has versioned Story/Bug/Task templates and a
backend review/approval/update service, but chat has no quality validator and no
safe review card.

## Decision

Use deterministic checks plus a bounded, evidence-backed AI assessment. Keep Jira
schema requirements, local team policy, and advisory guidance separate. A readiness
result expresses ticket-writing readiness and never claims Scrum compliance, sprint
commitment, or Definition of Done.

Prepare immutable field-level proposals from a fresh Jira read, then present the
complete before/after values in a server-rendered review card. A protected explicit
confirmation control is the only first-release approval path. The model can validate
and propose wording but cannot approve, execute, or expose write endpoints.

Bind proposals to a local user and browser conversation. Use an exact payload hash,
CSRF and review tokens, proposal-level execution claims, pre-write raw-field checks,
and post-write verification. Preserve ADF rather than flattening rich Jira content.

## Alternatives considered

| Alternative | Reason not selected |
| --- | --- |
| Prompt-only review and chat approval | Natural-language consent is ambiguous and cannot enforce an immutable reviewed payload. |
| Deterministic checks only | Reliable for blank fields and placeholders, but unable to assess clear free-form requirements. |
| AI assessment with direct update tool | Lets a model cross the user-confirmation boundary. |

## Consequences

The solution reuses the current local model adapter, FastAPI UI, update service, and
PostgreSQL storage. Validation remains available without a database; review/update
requires it. The feature adds one bounded model call per complete validation and
records its usage. Every revision requires a new confirmation.

Jira changes between the final read and PUT remain a residual race. The service
reduces the window, preserves audit information, and reports uncertain outcomes
without automatic write retries. Bulk actions and multi-user hosting require a new
decision.

See [the design](../superpowers/specs/2026-10-03-ticket-quality-and-confirmed-updates-design.md).

