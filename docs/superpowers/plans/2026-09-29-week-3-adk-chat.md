# Week 3 ADK Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a local, read-only ADK chat assistant that answers pilot Jira questions with server-verified citations and measured model usage.

**Architecture:** Retain the in-progress `agent/` and `web/` boundaries: ADK tools are thin adapters over `SearchService`, while FastAPI owns only local rendering and browser-session state. Complete the unfinished baseline and documentation paths, then harden the existing draft against the approved design and its full acceptance suite.

**Tech Stack:** Python 3.11+, Google ADK, Anthropic-compatible ADK model adapter, FastAPI, Jinja, HTMX, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-29-week-3-adk-chat-design.md`

## Global Constraints

- Bind the web server only to `127.0.0.1`, `::1`, or `localhost`.
- Expose only typed, read-only Jira operations; do not add raw JQL, credentials, or Jira mutation tools.
- Route every read through `SearchService`/`JiraClient` and therefore `PilotScope`.
- Treat prompts and issue text as untrusted data; never permit them to alter tools, scope, or permissions.
- Keep conversations in process only; do not persist messages, sessions, or Jira content.
- Never emit secrets in HTML, source links, logs, validation output, telemetry, or baseline artifacts.
- Tests run using fixture Jira and a deterministic fake LLM; no live Jira or provider call occurs in CI.

## Review Focus

- A malformed IPv6 Host header must not bypass the loopback-only middleware.
- A model answer that names a valid-looking but unreturned issue key must not create a trusted source link.
- A failed model run must create a clear task outcome without recording invented usage or a success baseline result.
- An empty provider usage block must remain unknown/zero as documented and must not make usage arithmetic negative.
- A baseline artifact path outside the intended local documentation directory must require an explicit safe, writable path.

---

### Task 1: Establish the Week 3 runtime contract

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/scrum_agent/config.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `Settings` fields and the draft `require_model_settings(settings)` helper.
- Produces: validated `Settings.model_name`, `model_api_key`, `model_base_url`, `model_max_tokens`, `web_host`, `web_port`, plus documented local startup.

- [ ] **Step 1: Write failing configuration and packaging tests**

Add tests that prove model settings remain optional for Week 1–2 commands, are required by `require_model_settings`, reject blank values and unsafe provider URLs, and never appear in representation/validation output.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_config.py -q`

Expected: FAIL because the added Week 3 configuration behavior is not completely covered or implemented.

- [ ] **Step 3: Complete the configuration and dependency contract**

Keep `require_model_settings(settings: Settings) -> None` as the feature gate. Pin compatible Google ADK, FastAPI, Jinja, provider-client and test dependencies after verifying imports in the fresh environment; remove any dependency that the final runtime does not import. Keep `web_host` loopback-only and `model_base_url` HTTPS-only.

- [ ] **Step 4: Document the exact local setup**

Add only the required model/web variables to `.env.example`, and update README status, dependency installation, `scrum-agent serve`, `scrum-agent baseline`, loopback-only behavior, supported provider configuration and the no-write guarantee.

- [ ] **Step 5: Verify the runtime contract**

Run: `pytest tests/test_config.py -q && ruff check src/scrum_agent/config.py tests/test_config.py`

Expected: PASS with no lint errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/scrum_agent/config.py .env.example README.md tests/test_config.py
git commit -m "feat: configure local Week 3 chat runtime"
```

### Task 2: Validate and harden the read-only ADK tool boundary

**Files:**
- Modify: `src/scrum_agent/agent/payloads.py`
- Modify: `src/scrum_agent/agent/tools.py`
- Modify: `src/scrum_agent/agent/instructions.py`
- Modify: `tests/test_agent_tools.py`

**Interfaces:**
- Consumes: `SearchService.get_issue`, `.list_sprints`, `.search_issues`, and `.search_sprint`.
- Produces: `make_tools(service: SearchService) -> list[FunctionTool]`, returning structured success or safe error payloads with server-truth source IDs.

- [ ] **Step 1: Write failing boundary tests**

Add tests for non-string issue/sprint inputs, hostile issue text, validated source shape, no unsupported tool parameters, exact typed error mapping, and no source payload on failed authorization.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_agent_tools.py -q`

Expected: FAIL only on the newly added boundary cases.

- [ ] **Step 3: Make the tool adapters satisfy the contract**

Keep tool functions limited to exact key lookup, sprint listing, typed issue search and sprint-scoped typed search. Normalize inputs before service calls; translate expected failures with `error_payload`; never return partial results after scope denial. Reconcile the instruction wording with the four safe tool capabilities and explicitly require abstention for unavailable blocker evidence.

- [ ] **Step 4: Verify the complete tool acceptance set**

Run: `pytest tests/test_agent_tools.py tests/test_checked_queries.py -q && ruff check src/scrum_agent/agent`

Expected: PASS with no scope leak or lint error.

- [ ] **Step 5: Commit**

```bash
git add src/scrum_agent/agent/payloads.py src/scrum_agent/agent/tools.py src/scrum_agent/agent/instructions.py tests/test_agent_tools.py
git commit -m "feat: harden read-only ADK Jira tools"
```

### Task 3: Complete conversation execution and cost baseline

**Files:**
- Modify: `src/scrum_agent/agent/chat.py`
- Modify: `src/scrum_agent/agent/usage.py`
- Create: `src/scrum_agent/agent/baseline.py`
- Modify: `src/scrum_agent/agent/__init__.py`
- Modify: `src/scrum_agent/app.py`
- Modify: `tests/agent_fakes.py`
- Modify: `tests/test_agent_chat.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: `make_tools`, `UsageRecorder`, `ChatService.run_turn(session_id, user_text)`.
- Produces: `run_baseline(settings: Settings, out_path: str | None) -> Coroutine[Any, Any, dict]` and `TurnResult` with server-collected sources and usage delta.

- [ ] **Step 1: Write failing execution and baseline tests**

Add tests for a baseline artifact containing model/provider metadata, per-task answer status, calls/tokens/cost-or-unknown and totals; test default output location, explicit valid output path, model configuration failure and a runner/model failure. Add CLI tests that the `baseline` and `serve` subcommands return their documented exit codes without opening a live connection in tests.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_agent_chat.py tests/test_app.py -q`

Expected: FAIL because `scrum_agent.agent.baseline` and its CLI behavior are absent.

- [ ] **Step 3: Implement the baseline runner and finish usage semantics**

Implement `run_baseline` using a new short-lived session for each checked intent. Persist a sanitized JSON artifact under `docs/superpowers/notes/` by default; reject paths outside the worktree or use a clearly safe explicit path. Record tool-backed answer checks, elapsed time, model calls, prompt/output/total tokens, and `cost: null` when no provider price/usage is available. Do not mark a failed or unavailable task as correct. Ensure `UsageRecorder` handles absent metadata without negative deltas.

- [ ] **Step 4: Verify chat, baseline and CLI behavior**

Run: `pytest tests/test_agent_chat.py tests/test_app.py -q && ruff check src/scrum_agent/agent src/scrum_agent/app.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/scrum_agent/agent src/scrum_agent/app.py tests/agent_fakes.py tests/test_agent_chat.py tests/test_app.py
git commit -m "feat: record Week 3 agent cost baseline"
```

### Task 4: Harden and finish the local chat UI

**Files:**
- Modify: `src/scrum_agent/web/__init__.py`
- Modify: `src/scrum_agent/web/linkify.py`
- Modify: `src/scrum_agent/web/templates/chat.html`
- Modify: `src/scrum_agent/web/templates/_turn.html`
- Modify: `src/scrum_agent/web/static/chat.css`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `ChatService.run_turn` and `TurnResult.sources`.
- Produces: `create_app(settings: Settings, chat: ChatService) -> FastAPI`, an isolated short-lived browser conversation with visible server-verified sources.

- [ ] **Step 1: Write failing web-security and context tests**

Add tests for bracketed IPv6 host/origin parsing, source-link rendering only from `TurnResult.sources`, output escaping, conversation reset/isolation, and a visible fixed pilot-context selector/status that cannot alter scope.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_webapp.py -q`

Expected: FAIL on the new Host/origin, citation-source and context-display cases.

- [ ] **Step 3: Implement minimal UI hardening**

Correct loopback origin parsing for IPv4, hostname and bracketed IPv6. Linkify model answer text only after escaping it, but build the Sources list exclusively from validated source IDs produced by the server. Render the fixed project/board context as informational, not user-configurable scope. Preserve HttpOnly/SameSite cookies, reset behavior, size limits and no public API docs.

- [ ] **Step 4: Verify UI behavior**

Run: `pytest tests/test_webapp.py -q && ruff check src/scrum_agent/web tests/test_webapp.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/scrum_agent/web tests/test_webapp.py
git commit -m "feat: complete local Week 3 chat UI"
```

### Task 5: Perform end-to-end acceptance and record delivery status

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-09-27-weekly-delivery-roadmap.md`
- Create: `docs/superpowers/notes/2026-09-29-week-3.md`
- Test: `tests/test_agent_tools.py`
- Test: `tests/test_agent_chat.py`
- Test: `tests/test_webapp.py`

**Interfaces:**
- Consumes: all completed Week 3 components.
- Produces: an accurate implementation note and roadmap status backed by reproducible test and local-demo instructions.

- [ ] **Step 1: Write failing acceptance tests for remaining uncovered cases**

Add the Review Focus tests not already covered: model-supplied unverified key must not appear in Sources; malformed loopback forms must fail closed; unavailable model usage must remain explicitly unknown in baseline output.

- [ ] **Step 2: Run the targeted acceptance tests to verify they fail**

Run: `pytest tests/test_agent_tools.py tests/test_agent_chat.py tests/test_webapp.py -q`

Expected: FAIL only on the newly added acceptance cases.

- [ ] **Step 3: Make the smallest changes that satisfy acceptance**

Address only failing contract gaps. Do not add Jira writes, persistence, reports, background collection or any Week 4 capability.

- [ ] **Step 4: Run the full verification suite**

Run: `pytest -q && ruff check . && ruff format --check .`

Expected: all tests pass and Ruff reports no lint or formatting errors.

- [ ] **Step 5: Document verified status**

Write the Week 3 note with test counts, exact commands, known limits (live-model cost baseline and manual Jira demo still require local secrets) and source/citation behavior. Update the roadmap checkboxes only where the implementation and evidence meet each item; update README status consistently.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/superpowers/plans/2026-09-27-weekly-delivery-roadmap.md docs/superpowers/notes/2026-09-29-week-3.md tests
git commit -m "docs: record Week 3 delivery verification"
```
