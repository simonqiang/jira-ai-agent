# Week 12 Personal-Pilot Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide safe, repeatable local release-readiness checks, visibility and operating evidence for the personal pilot.

**Architecture:** A focused `pilot` module owns sanitized readiness assessment and optional local log-file setup. The CLI exposes `preflight` for configuration/live-read health and `pilot-check` for deterministic release-gate tests; the README/runbook keeps live Jira actions manual and non-destructive.

**Tech Stack:** Python 3.11+, Pydantic settings, standard-library logging/subprocess, pytest, Ruff, PostgreSQL/pgvector, FastAPI/ADK.

**Spec:** `docs/superpowers/specs/2026-10-03-week-12-pilot-readiness-design.md`

## Global Constraints

- Bind only to loopback and retain the existing single configured local pilot user.
- Keep credentials in environment variables or git-ignored `.env`; never print or log secrets, raw configuration, issue text, prompts or HTTP authorization headers.
- Do not make Jira writes, restore the pilot database, or depend on live credentials in automated checks.
- A release-ready status needs durable PostgreSQL, healthy freshness/expiry checks, enabled model/retrieval settings, and explicit suggestions state.
- Use `PYTHONPATH=src` for all local test commands to prevent importing another worktree's installed package.
- Keep shared identity, hosted deployment, public ingress and automatic live-Jira write tests out of scope.

## Review Focus

1. A PostgreSQL URL containing credentials must never be echoed by `preflight`; Task 1 adds a redaction test.
2. A non-loopback host or missing durable store must fail closed rather than show a warning; Task 1 adds parameterized failure tests.
3. Repeated application startup must not duplicate file log handlers; Task 2 adds a handler-idempotency test.
4. `pilot-check` must run the current worktree package, not an editable install elsewhere; Task 3 asserts `PYTHONPATH=src` in the subprocess environment.
5. Operators must not mistake a manual live-write/recovery drill for an automated check; Task 4 labels each runbook action and preserves scratch-database-only restore instructions.

---

### Task 1: Safe pilot readiness assessment

**Files:**
- Create: `src/scrum_agent/pilot.py`
- Modify: `src/scrum_agent/config.py`
- Modify: `src/scrum_agent/app.py`
- Create: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `Settings`, `require_model_settings`, `require_embedding_settings`, `require_database_settings`, and the existing freshness lookup/service.
- Produces: `ReadinessCheck(name: str, ok: bool, detail: str)`, `preflight(settings: Settings) -> tuple[ReadinessCheck, ...]`, and `_preflight(settings: Settings) -> int` CLI handler.

- [ ] **Step 1: Write failing preflight tests in `tests/test_pilot.py`**

  Cover a fully configured fake settings instance; missing database URL; disabled suggestions; a non-loopback host; and a database URL containing `user:password@`. Assert required failures produce `ok is False`, disabled suggestions is explicit, and output contains neither the password nor the full URL.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `PYTHONPATH=src pytest tests/test_pilot.py -q`
  Expected: FAIL because `scrum_agent.pilot` and the preflight CLI do not yet exist.

- [ ] **Step 3: Add safe settings and readiness interfaces**

  Add `log_directory`, `log_max_bytes`, and `log_backup_count` settings with conservative validation. Implement `ReadinessCheck` and `preflight` in `pilot.py`; report configuration names and remediation, never setting values. Add `preflight` to `_parse_args` and dispatch it from `main` in `app.py`.

- [ ] **Step 4: Run the focused tests to verify GREEN**

  Run: `PYTHONPATH=src pytest tests/test_pilot.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit the readiness slice**

  ```bash
  git add src/scrum_agent/pilot.py src/scrum_agent/config.py src/scrum_agent/app.py tests/test_pilot.py
  git commit -m "feat: add pilot readiness preflight"
  ```

### Task 2: Opt-in, rotating local logs

**Files:**
- Modify: `src/scrum_agent/pilot.py`
- Modify: `src/scrum_agent/app.py`
- Modify: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `Settings.log_directory`, `Settings.log_max_bytes`, `Settings.log_backup_count` from Task 1.
- Produces: `configure_local_file_logging(settings: Settings) -> None`, called once during CLI startup after standard stderr logging is configured.

- [ ] **Step 1: Write failing log setup tests in `tests/test_pilot.py`**

  Use `tmp_path` and a dedicated logger. Assert configured logging writes a UTF-8 rotating file, omitting `log_directory` does not create a file, and calling setup twice adds only one matching handler.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `PYTHONPATH=src pytest tests/test_pilot.py -q`
  Expected: FAIL because local file logging is not configured.

- [ ] **Step 3: Implement `configure_local_file_logging(settings: Settings) -> None`**

  Create the explicitly configured directory, attach one `RotatingFileHandler` with the configured size/count and existing timestamp/level format, and tag the handler so repeat startup is idempotent. Do not change existing stderr behavior or increase HTTP-client verbosity.

- [ ] **Step 4: Run the focused tests to verify GREEN**

  Run: `PYTHONPATH=src pytest tests/test_pilot.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit the observability slice**

  ```bash
  git add src/scrum_agent/pilot.py src/scrum_agent/app.py tests/test_pilot.py
  git commit -m "feat: add optional rotating pilot logs"
  ```

### Task 3: Deterministic automated release-gate command

**Files:**
- Modify: `src/scrum_agent/pilot.py`
- Modify: `src/scrum_agent/app.py`
- Modify: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `subprocess.run`, repository root resolved from `app.py`, and the gate test module list fixed in the design.
- Produces: `run_pilot_checks(*, root: Path) -> int` and `_pilot_check() -> int` CLI handler.

- [ ] **Step 1: Write failing pilot-check tests in `tests/test_pilot.py`**

  Stub the subprocess boundary. Assert the command runs pytest over `test_config`, `test_webapp`, `test_ticketing`, `test_ticketing_updates`, `test_reports`, `test_retrieval`, `test_eval_week11`, and focused pilot tests; assert its environment has `PYTHONPATH=src`, does not add secret values, and propagates a failing return code.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `PYTHONPATH=src pytest tests/test_pilot.py -q`
  Expected: FAIL because `run_pilot_checks` and the CLI subcommand do not exist.

- [ ] **Step 3: Implement the pilot-check runner and CLI dispatch**

  Run the fixed gate test list with `sys.executable -m pytest`, current-root working directory and a `PYTHONPATH` prefixed by `src`; print only test-group names and return the subprocess exit code. Do not load `Settings` or call Jira for this command.

- [ ] **Step 4: Run the focused tests and selected release gates to verify GREEN**

  Run: `PYTHONPATH=src pytest tests/test_pilot.py tests/test_config.py tests/test_webapp.py tests/test_ticketing.py tests/test_ticketing_updates.py tests/test_reports.py tests/test_retrieval.py tests/test_eval_week11.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit the gate runner**

  ```bash
  git add src/scrum_agent/pilot.py src/scrum_agent/app.py tests/test_pilot.py
  git commit -m "feat: add pilot release gate command"
  ```

### Task 4: Personal-pilot operations evidence

**Files:**
- Create: `docs/superpowers/runbooks/week-12-personal-pilot.md`
- Create: `docs/superpowers/notes/2026-10-03-week-12.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `scrum-agent preflight`, `scrum-agent pilot-check`, existing `migrate`, `collect`, `reindex`, `freshness`, `report`, and `serve` commands.
- Produces: an operator runbook and a fill-in Week 12 closeout record.

- [ ] **Step 1: Write the runbook and observation template**

  Document prerequisite secrets, safe startup, preflight, automated gates, integrated workflow, log review, latency capture, scratch-only backup/restore, failure recovery, daily working-week observations and next-backlog scoring. Label manual-live Jira creation/update, manual-safe scratch restore and automated commands separately.

- [ ] **Step 2: Update the README**

  Add a concise Week 12 section linking the runbook and documenting `preflight`, `pilot-check`, optional rotating logs, and the durable-session/transient-transcript boundary.

- [ ] **Step 3: Validate documentation references and static checks**

  Run: `rg -n "preflight|pilot-check|week-12-personal-pilot" README.md docs/superpowers/runbooks docs/superpowers/notes`
  Expected: Every documented command and linked artifact is present.

- [ ] **Step 4: Commit the operating material**

  ```bash
  git add README.md docs/superpowers/runbooks/week-12-personal-pilot.md docs/superpowers/notes/2026-10-03-week-12.md
  git commit -m "docs: add Week 12 pilot runbook"
  ```

### Task 5: Final release-readiness verification

**Files:**
- Verify: `src/scrum_agent/pilot.py`, `src/scrum_agent/config.py`, `src/scrum_agent/app.py`, `tests/test_pilot.py`, `README.md`, and Week 12 docs.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified branch evidence; no additional behavior.

- [ ] **Step 1: Run the complete automated suite from this worktree**

  Run: `PYTHONPATH=src pytest -q`
  Expected: PASS with no external Jira calls.

- [ ] **Step 2: Run static checks**

  Run: `PYTHONPATH=src ruff check src tests`
  Expected: `All checks passed!`

- [ ] **Step 3: Exercise CLI help and the deterministic release-gate command**

  Run: `PYTHONPATH=src python -m scrum_agent --help && PYTHONPATH=src python -m scrum_agent pilot-check`
  Expected: help lists both Week 12 commands; gate command exits 0 after its fixed suite.

- [ ] **Step 4: Record final status in the Week 12 note**

  State which automated gates passed and retain manual-live/working-week evidence as open rather than claiming it occurred.

- [ ] **Step 5: Commit final evidence updates**

  ```bash
  git add docs/superpowers/notes/2026-10-03-week-12.md
  git commit -m "docs: record Week 12 readiness evidence"
  ```

## Self-review

- **Spec coverage:** Tasks 1–3 implement the safe readiness, logging and gate commands; Task 4 documents manual operation/recovery/evidence; Task 5 verifies every automated criterion. Live work-week observation and Jira writes remain explicitly manual as required.
- **Type consistency:** Task 1 defines the types/settings Task 2 consumes; Task 3 is deliberately independent of settings; Task 4 documents the exact CLI names.
- **Review focus:** Every listed risk is assigned to an explicit test or documentation task.
- **Scope:** No database migration, shared authentication, deployment or Jira mutation is added.
