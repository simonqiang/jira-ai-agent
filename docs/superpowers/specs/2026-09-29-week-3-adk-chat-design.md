# Week 3 ADK Chat Design

## Purpose

Deliver a useful local, read-only assistant that lets the pilot Scrum Master ask
plain-English Jira questions and verify each answer from the Jira issues that
support it. This implements the Week 3 goal and definition of done in the
weekly delivery roadmap.

## Scope and constraints

- The service runs locally and binds only to `127.0.0.1`.
- Google ADK is the agent framework. The exact ADK release is pinned in the
  project dependencies after validating its Python API during implementation.
- The only Jira operations exposed to the model are existing typed reads:
  sprint listing/resolution, filtered issue search, and single-issue lookup.
  No write-capable client method, token scope, or tool is added.
- Every Jira read uses the existing `JiraClient` and `SearchService`, which in
  turn enforce `PilotScope`. Tool input is validated before a Jira call.
- Conversations are short-lived and held in process memory. Week 3 creates no
  persistent sessions, chat archive, embeddings, or database.
- The implementation must not send Jira credentials to a model. Issue content
  is returned only from read tools after the scope check.

## Architecture

The new `agent/` package owns the conversational boundary. It defines narrow
plain-Python ADK tools whose docstrings describe when they may be called and
whose structured return values carry issue keys, summaries, status, type and
source URLs. A factory creates the ADK agent with instructions that require
tool-backed claims, visible source citations, clarification for ambiguous
sprints, and an explicit statement when information is unavailable.

The agent runner is behind a small protocol so tests can use a deterministic
runner without network access or a provider key. The production runner invokes
ADK with a fresh per-browser-session conversation and records call outcome,
elapsed time, provider/model identifier, input tokens, output tokens and cost
when provider usage metadata supplies them. Unknown token or cost values remain
explicitly unknown; they are never fabricated.

A `web/` package owns a minimal FastAPI application and Jinja templates. It
serves a single chat page and a POST endpoint. The page accepts a question,
shows the answer and source links, retains only the current browser's
short-lived conversation ID, and offers a basic context selector for the pilot
board. The server rejects non-loopback binding through configuration.

```
Browser -> FastAPI chat route -> agent runner -> ADK agent
                                        |          |
                                        |          +-> read-only tool adapters
                                        |                     |
                                        +-> task telemetry     +-> SearchService -> JiraClient -> PilotScope
```

## Tool contracts

The agent may use only these capabilities:

1. `list_sprints(states)` returns pilot-board sprint IDs, names and states.
2. `search_issues(filters)` accepts the existing typed fields (sprint ID,
   statuses, issue types, labels, assignees and unresolved flag) and returns a
   result count plus cited issue records.
3. `get_issue(issue_key)` returns one in-scope issue record with a citation.

Tool adapters do not accept JQL, URLs, headers, arbitrary JSON or instructions
from issue text. They convert known exceptions into safe, structured states:
`ambiguous`, `not_found`, `empty`, `denied` and `unavailable`. An ambiguous
sprint includes candidate IDs and names so the assistant can ask the user to
choose. A no-match result says zero results, not missing data. A revoked or
denied request invalidates any previously displayed restricted result and is
reported as unavailable.

## Answer and citation rules

The agent instruction treats Jira issue text as data, never as authority over
tool rules, instructions, scope or permissions. It must not claim facts without
tool evidence. It cites each issue-derived conclusion using the source URL
returned by the tool and names uncertainty where Jira does not expose the
requested fact. "Explicitly blocked" means a configured blocker field/status
or an explicit issue-link/blocker signal returned by a future tool; Week 3 must
not infer it from prose alone. Until that deterministic signal exists, the
answer must say so rather than invent a blocked classification.

## Security and privacy

- Configuration keeps Jira and Gemini/API secrets as `SecretStr` and excludes
  them from responses, templates, logs and telemetry.
- A chat question is untrusted input. It is passed as user content, not merged
  into the system instruction or tool configuration.
- The server has no Jira write route or agent write tool.
- Source URLs are generated from the configured Jira site and an already
  scope-validated issue key; arbitrary links are not rendered as trusted
  source citations.

## Verification

Tests run without live Jira or model credentials. They verify tool validation
and scope delegation, all existing checked-query cases through the agent-facing
adapters, citations for returned issues, zero versus unavailable handling,
ambiguous-sprint clarification, prompt-injection resistance, revoked-access
follow-ups, and that the tool registry contains no mutation operation. FastAPI
tests verify loopback-only configuration, chat rendering, follow-up context and
source-link presentation. Telemetry tests verify one completed task record per
request and preserve unknown usage values.

The final acceptance run is the full test suite plus lint/format checks. A
manual local demo, with real credentials supplied only through `.env`, asks
“Show unresolved bugs in this sprint” and then “Which ones are explicitly
blocked?”; the latter must cite deterministic evidence or explicitly state that
the available Jira fields cannot establish it.

## Out of scope

Persisted sessions, reports, history collection, vector search, ticket drafting
or mutation, background jobs, external hosting, multi-user authentication and
claims about long-term cost or report-time reduction remain later-week work.
