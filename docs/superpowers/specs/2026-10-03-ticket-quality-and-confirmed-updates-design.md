# Ticket quality validation and confirmed updates

Date: 2026-10-03  
Status: Proposed; documentation only, implementation requires a separate request.  
Companion: [Implementation plan](../plans/2026-10-03-ticket-quality-and-confirmed-updates.md) · [ADR-0003](../../decisions/0003-ticket-quality-and-confirmed-updates.md)

## 1. Intent and success criteria

Extend the personal Scrum Master assistant so the user can review an existing Jira
ticket against useful agile writing practices, see concrete improvements, and update
the ticket **only after explicitly confirming the exact proposed changes**.

User requirements: ticket validation, suggested updates, confirmation before every
update, and a documented design and plan. Proposed first-release assumptions: one
existing Story, Bug, or Task at a time; the current local browser chat and pilot
project; confirmation through a review card. These assumptions are open to review.

Success means a user can ask “Validate PAY-3,” understand the findings, request an
improvement, revise the proposal, confirm or cancel, and see the verified Jira result.
Validation, drafting, and preparing a proposal make zero Jira writes. “Improve this,”
“update it,” silence, and acceptance of a writing suggestion never authorize a write.

## 2. Existing implementation and gaps

| Existing code | Current behavior | Extension needed |
| --- | --- | --- |
| `drafting/ticket_templates/*.yaml` | Versioned Story/Bug/Task writing policies | Reuse categories; separate actual Jira schema constraints from writing policies |
| `agent/tools.py`, `agent/instructions.py` | Jira reads, drafting, reports and retrieval; no Jira write tools | Add validation and local proposal preparation |
| `ticketing/updates.py` | Freeze field diff, approve for 15 minutes, reread, PUT changed fields, verify | Bind review to browser conversation; cancel/supersede; concurrent execution protection |
| `web/__init__.py` | JSON propose/approve/execute routes; signed local approval session | Visible confirmation card and protected confirmation requests |
| `agent/chat.py`, `web/templates/_turn.html` | Server-derived sources; textual answers | Server-derived validation and review artifacts |
| `jira/models.py` | Flattens ADF to readable text | Keep raw rich documents for safe updates and conflict checks |
| `migrations/004_ticket_updates.sql` | Durable proposals, approvals and executions | Review lifecycle and one execution per proposal |

The current update allowlist is `summary`, `description`, `acceptance_criteria`,
`labels`, and `due_date`. Acceptance criteria currently maps to
`customfield_10350`. The existing creation workflow stays governed by its approval
service. This feature does not grant the chat model approval or execution access.

Specific gaps to address: unknown update keys currently reach a dictionary lookup;
approval deduplication is per approval, allowing separate approvals for one proposal;
exceptions before or during verification can leave an execution in `executing`;
plain-string description writes are still an open live check in the Week 9 note.
These are design inputs, not claims of newly discovered live failures.

## 3. Writing policy and standards

Scrum supports clear backlog items, refinement, and a team Definition of Done.
Our writing rubric is a proposed team practice: Scrum does not prescribe this ticket
schema, an “As a…” sentence, or Given/When/Then formatting. Acceptance criteria for
one item do not replace the Definition of Done for an Increment.
[Source: Scrum Guide](https://scrumguides.org/scrum-guide.html).

Use INVEST as an advisory lens for Stories: independence, negotiability, value,
estimability, size, and testability. Given/When/Then is one useful way to express
observable behavior; clear checklists are also acceptable.
[INVEST](https://agilealliance.org/glossary/invest/),
[Given/When/Then](https://agilealliance.org/glossary/given-when-then/).

Keep three categories in a versioned `quality-v1` policy:

- `jira_required`: actual schema/permission constraints from live Jira metadata.
- `team_policy`: writing expectations derived from the current drafting templates.
- `advisory`: helpful suggestions that never block an update by themselves.

Existing templates call several writing fields `required_field`, including Story
role and benefit. Do not interpret those labels as proof Jira requires separate
fields. Map those template fields into writing checks without changing draft readiness.

| Ticket type | Team writing checks | Advisory checks |
| --- | --- | --- |
| All supported types | Meaningful summary; understandable description; actionable outcome; no unresolved placeholders in a proposed write | Ambiguous language; conflicting statements; relevant risks/dependencies |
| Story | User/beneficiary, goal and value; scope boundary; observable acceptance criteria | INVEST review; applicable error cases; possible split into smaller work |
| Bug | Observable problem; environment; reproduction steps; expected/actual results; impact; verification criteria | Evidence references; workaround; relevant regression checks |
| Task | Objective; scope/deliverables; completion checklist | Dependencies and background |

Content may appear in prose, headings, lists, or dedicated fields. Do not demand
exact headings, English phrasing, or a literal Given/When/Then pattern. Whitespace-
only content and explicit placeholders such as `[TBD]` count as incomplete.
An explicit report that reproduction is intermittent is useful evidence; ask for
available observations instead of fabricating exact reproduction steps.

Missing dependencies are not evidence of independence. Missing estimates or team
capacity make estimability and sprint size `unknown`; do not invent points, a size
limit, deadlines, performance thresholds, business impact, or production behavior.
Unsupported issue types receive general advice with `unsupported_type`, never a
misleading Story score. There is no numeric “agile compliance” score.

## 4. Validation result and assessment

`TicketQualityService` fetches the scoped live issue, retains a content fingerprint
and fetch time, then combines deterministic checks with one bounded semantic review.
The policy ID and issue type are chosen server-side.

`ValidationReport` contains:

- `validation_id` (UUID), `issue_key`, `issue_type`, `policy_version`, `fetched_at`,
  `input_hash`, `assessment_complete`.
- `readiness`: `needs_work`, `needs_clarification`, `ready_for_review`, or
  `unsupported_type`. This describes writing readiness, never sprint commitment.
- `findings`: `rule_id`, `category`, `check_status` (`pass`, `fail`, `unknown`,
  `not_applicable`), `assessment_kind` (`deterministic`, `ai_assessed`), `field`,
  `evidence`, `reason`, and `suggestion`.
- `open_questions`, `sources`, and model usage metadata.

Readiness precedence: unsupported type first; any failed team check means
`needs_work`; otherwise unknown team checks or incomplete assessment mean
`needs_clarification`; otherwise `ready_for_review`. Advisory findings do not block.
Missing Jira edit permissions affect update eligibility, not writing readiness.

Deterministic checks cover empty content, explicit placeholders, supported types,
and payload shape. Semantic checks cover whether prose expresses value, scope,
reproducibility, and observable outcomes. The configured model adapter returns a
strict Pydantic-validated result using only policy rule IDs. Check every quoted
excerpt against supplied issue text; reject invented evidence and instructions.
Model findings remain labeled AI assessments, including passes. Empty content can
fail deterministically; nonempty unstructured prose requires semantic assessment.
Any applicable semantic rule omitted by the model becomes `unknown`; omission
cannot count as a pass. A deterministic failure takes precedence over an AI pass.

The semantic assessor has no tools. Limit it to one call, 30 seconds, and 40,000
input characters. Larger issues get deterministic results plus an explicit partial
assessment, with no silent truncation. Meter this call through the existing usage
recorder. On timeout, malformed output, or provider failure, show known findings,
mark semantic checks unknown, and offer a retry; never promote partial assessment
to ready. Return structured errors for Jira denial, authentication, or unavailability.

Reports are transient and keyed by browser conversation, with a 30-minute lifetime;
validation works without PostgreSQL. Durable update proposals copy needed review
metadata. Restart/reset invalidates transient report handles. Every proposal uses
a fresh Jira read even when it references a validation report.

## 5. User interaction and confirmation

1. **Validate:** “Validate PAY-3.” Show ticket type, policy, fetch time, findings,
   unknowns, and suggested improvements. No update proposal is required.
2. **Prepare:** “Improve its description and acceptance criteria.” Preserve the
   documented intent. Mark new wording as proposed. Ask only for facts that cannot
   safely be derived; do not turn unsupported guesses into requirements.
3. **Review:** Show a server-rendered card containing the issue link, exact complete
   old/new values for every changed field, reasons, any unresolved questions,
   field clearing or formatting replacement, and confirmation expiry. A compact
   diff may supplement but cannot replace access to complete values.
4. **Confirm:** User clicks **Confirm update to PAY-3**. The backend records approval
   and invokes execution of the frozen payload. No separate user click is needed
   between approve and execute; those remain separate backend operations.
5. **Revise:** User edits selected fields and chooses **Review revised changes**.
   Create a new immutable proposal and supersede the previous proposal. The revised
   proposal needs its own confirmation; no fields are written during revision.
6. **Cancel:** User clicks **Cancel**. Persist cancellation and disable confirmation.
7. **Result:** Show actual execution status and verified field outcomes. Success
   comes from read-back, never from model prose or a successful HTTP PUT alone.

Exact card prompt: “Apply these changes to PAY-3? Review the before and after values.
Jira will be updated only when you select Confirm update to PAY-3.”

Typing “yes,” “looks good,” or “confirm” in chat directs the user to the matching
review card; the first release accepts confirmation only through its control.
Model-rendered Markdown/HTML never creates approval controls. Multiple proposals
carry their own IDs and issue keys; a confirmation cannot apply to another card.

Validation issues need not be fixed all at once. Allow an approved partial improvement
while showing remaining quality gaps. Block invalid Jira payloads, empty summaries,
unresolved placeholders in changed content, and unresolved factual assumptions in
the proposed write. A user may acknowledge labeled policy/AI cautions on the card;
that does not convert a suggestion into an established Jira fact.

## 6. Components and contracts

```mermaid
sequenceDiagram
    actor User
    participant Chat
    participant Quality as Quality service
    participant Review as Review service / UI
    participant Updates as Update service
    participant Jira
    User->>Chat: Validate ticket
    Chat->>Quality: validate_ticket(key, trusted conversation)
    Quality->>Jira: Scoped live read
    Quality-->>Chat: Evidence and suggestions
    User->>Chat: Prepare improvements
    Chat->>Review: prepare_update(key, changes)
    Review->>Updates: Freeze live base and exact payload
    Review-->>User: Before/after card; Confirm / Revise / Cancel
    User->>Review: Explicit confirmation
    Review->>Updates: Approve frozen proposal, execute once
    Updates->>Jira: Reread; schema/permission check; scoped PUT
    Updates->>Jira: Read back
    Updates-->>User: Verified outcome or named failure
```

New modules under `quality/`: `models.py` for contracts, `policy.py` for versioned
rules, `assessor.py` for the bounded model assessment, and `service.py` for orchestration.
New `ticketing/review.py` owns browser review context, eligibility, and card views;
`ticketing/updates.py` remains the only existing-ticket write orchestrator.

Proposed Python interfaces:

- `QualityAssessor.assess(text: str, *, policy_version: str, issue_type: str,
  conversation_id: str) -> SemanticAssessment` (async; no Jira access).
- `TicketQualityService.validate_ticket(issue_key: str, *, conversation_id: str)
  -> ValidationReport` (async).
- `TicketReviewService.prepare_update(issue_key: str, changes: dict, *, creator: str,
  conversation_id: str, validation_id: str | None = None) -> ReviewCard`.
- `get_review(proposal_id: int, *, creator: str, conversation_id: str) -> ReviewCard`.
- `cancel_review(proposal_id: int, *, creator: str, conversation_id: str) -> ReviewCard`.

Browser revisions call `revise_review(proposal_id: int, changes: dict, *, creator: str,
conversation_id: str) -> ReviewCard`. This atomically persists the replacement and
supersedes the original after a fresh read; if preparation fails, retain the original
review. Proposal preparation checks any report reference belongs to the same issue
and conversation. All model-produced wording is labeled proposed in the card.

ADK tools: `validate_ticket(issue_key)` and
`prepare_ticket_update(issue_key, changes, validation_id=None)`. Derive identity
and conversation from trusted tool context, never tool arguments. Expose no approve,
execute, cancel, credentials, raw JQL, or arbitrary field-ID tool. Extend
`TurnResult`/`TurnView` with structured `validation_reports` and `update_proposal_ids`.
Collect artifacts from actual tool responses; resolve proposal IDs against the store
and owner before rendering. A model-invented ID does not become an action card.

Reuse `POST /tickets/updates`, `POST /tickets/update-proposals/{id}/approve`, and
`POST /tickets/update-approvals/{id}/execute`. Add scoped review
`GET /tickets/update-proposals/{id}` and cancel
`POST /tickets/update-proposals/{id}/cancel`. Add a browser form handler
`POST /tickets/update-proposals/{id}/confirm` that composes approve/execute and returns
a result fragment. All approval paths require the same reviewed hash and protected
user request, including the existing JSON endpoints; do not leave a bypass.

## 7. Approval, persistence and write correctness

Use the existing signed single-user approval session. Bind proposals to its configured
user and browser conversation; add a random session CSRF token to every ticket
mutation form/JSON request. Retain loopback Host/Origin checks and SameSite cookies.
A server-issued review token binds proposal ID, payload hash, user, conversation,
and review expiry. Reject missing/mismatched tokens before persisting approval.
The token is proof of the protected review context; the user still supplies the
explicit confirmation action. Never expose approval routes to the model.

New proposals expire after 24 hours. A confirmed approval retains the existing
15-minute lifetime. Edits always create a new payload hash; approval cannot transfer.
Cancellation/superseding must atomically check lifecycle state. Once execution is
claimed, disable cancellation/revision and report “Update in progress.” New proposals
in another conversation never inherit old approvals. Legacy proposals without review
binding remain inspectable for audit but require fresh preparation to execute.

Migration `006_ticket_quality_reviews.sql` adds proposal conversation, policy/input
metadata, exact encoded Jira fields, raw base values, expiry, and review state
(`pending`, `approved`, `cancelled`, `superseded`). Existing proposal identity, base,
changes and hash stay immutable. Add `ticket_update_claims` keyed uniquely by
`proposal_id`, with approval/execution references. Backfill claims for existing
executions; if multiple legacy executions reference a proposal, use its earliest
execution as the claim and retain all historical rows. Pending legacy proposals
cannot acquire a new claim without fresh review binding.

Approval creation and execution claim use transactional row locks/constraints.
Repeated confirmation returns the same approval/result. The claim is at proposal
level, so multiple approval IDs cannot produce multiple PUTs. The owner check,
state check, expiry check, hash recomputation and atomic claim run for both browser
and JSON execution. Failed, stale or uncertain executions consume the approval;
a new write attempt requires a new review.

For new proposals, hash a canonical JSON object containing issue identity/type,
raw base values, exact encoded Jira fields, and review provenance (creator,
conversation, policy version and validation input hash). The card displays readable
values derived from that frozen payload. Execution recomputes this hash from stored
data; comparing two stored hash strings alone does not establish payload integrity.

Before the PUT: reread Jira, check pilot scope, issue identity/type, edit metadata,
field types and permission. Reread raw base values for every changed field; any
change rejects the write with conflicts. Unrelated-field edits survive. Encode
and hash the exact Jira payload before review; if metadata changes the encoding,
require a new proposal. Never send the entire issue or untouched fields.

Preserve rich documents: Jira v3 uses ADF for descriptions and multiline custom
fields; single-line custom fields use strings. Use live field metadata to determine
acceptance-criteria encoding. Retain raw ADF for conflict checks and verification.
Editing a template section must preserve all other nodes; an arbitrary rich document
without an unambiguous section boundary is eligible only for a clearly displayed
whole-field replacement explicitly confirmed on the card. Preserve nodes in ordinary
section edits; never silently flatten tables, attachments, links, or marks.
[REST v3](https://developer.atlassian.com/cloud/jira/platform/rest/v3/),
[ADF structure](https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/).

Label arrays are exact replacements unless a supported add/remove operation is
introduced later. Clearing fields must be explicit and visible. Unknown keys fail
with `invalid_input` before any write; payload types/limits stay enforced server-side.

Execution statuses: existing `succeeded`, `rejected_stale`, `verification_failed`,
`failed`, and `executing`, plus `outcome_unknown`. If the PUT may have landed and the
read-back fails, record `outcome_unknown`; never retry the PUT automatically. Startup
reconciles interrupted claims by a scoped read only. Matching values yield a
reconciled success; otherwise show uncertainty requiring human inspection. Failures
known to occur before PUT are `failed`. Every exception finalizes an audit outcome
or leaves a durable interrupted record that reconciliation can discover.

The existing reread/PUT race remains: the inspected API contract provides no update
precondition used by this client. Another Jira actor can edit between those calls.
Minimize the interval, retain the audit, and disclose this limit; do not claim atomic
protection against external Jira edits.

## 8. Delivery boundaries and acceptance

Deliver validation first, then strengthen the existing write workflow, then connect
chat to review cards. Use the configured model and local stack; no additional model
provider or hosted service. Preparing updates requires PostgreSQL; validation does
not. No bulk updates, transitions, deletion, comments, assignments, priorities,
issue-type changes, automatic creation, or automated edits from validation alone.

Acceptance checks:

1. Good and poor Story/Bug/Task examples produce evidence-backed findings; clear
   free-form acceptance criteria pass without mandatory Given/When/Then.
2. A partial/unavailable assessment and missing sizing context remain unknown.
3. Validate/prepare/revise/cancel and chat “yes” produce zero Jira mutation requests.
4. Confirmation shows exact selected field values; one protected user click applies
   that payload once and produces a verified result.
5. Wrong owner/conversation, missing CSRF, changed hash, expired approval, cancelled
   or superseded proposal all cause zero PUTs.
6. Concurrent confirmations and multiple approval IDs yield at most one PUT per
   proposal. A reset/restart cannot silently approve an old proposal.
7. Changed reviewed fields reject stale proposals; edits elsewhere survive.
8. ADF content/formatting is preserved or its complete replacement is disclosed.
9. Lost PUT responses reconcile by read; failed read-back yields uncertainty with
   no automatic write retry. Model output never fabricates update success.
10. Injected ticket instructions, unsupported fields/types, invalid schema, and
    out-of-scope keys fail safely without widening access or inventing requirements.

Use fake Jira/model transports and a disposable PostgreSQL database for automated
verification during implementation. Manual live acceptance uses a user-selected
sandbox issue and the same explicit review confirmation. Store only anonymized
fixtures in the repository; issue text, review payloads and prompts stay out of logs.

## 9. Decisions and remaining product choices

Recommendation: deterministic checks plus labeled AI assessment and server-enforced
confirmation, reusing the Week 9 service. Prompt-only validation is cheaper to build
but inconsistent and cannot enforce approval. Deterministic-only validation is
repeatable but misses meaning in free-form prose. See ADR-0003 for the decision.

The proposal is implementable with the stated defaults. Review may adjust team policy,
review expiry, or whether chat confirmation is added later. None of these defaults
weakens the requirement for an explicit user confirmation of every update.
