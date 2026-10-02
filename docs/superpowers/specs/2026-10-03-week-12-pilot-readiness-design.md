# Week 12 personal-pilot readiness design

**Date:** 2026-10-03
**Status:** approved for planning
**Scope:** the local, single-user personal pilot only

## Purpose

Week 12 prepares the integrated assistant for a working-week personal pilot.
It makes the local release configuration observable and repeatable without
weakening the existing security boundaries or automatically performing Jira
writes. Live-Jira evidence and the working-week outcome remain actions for the
pilot user, whose credentials and work context must stay outside source control.

## Constraints and success criteria

- The process binds only to loopback and uses one configured local pilot user.
- Secrets remain environment variables or a git-ignored `.env`; neither CLI
  output nor logs may reveal them.
- Persistent app state requires local PostgreSQL: ADK sessions, jobs,
  approvals, executions, reports and retrieval data must remain available
  across an application restart.
- The existing Jira-native scope enforcement, approval hashing, stale-update
  check, permission recheck and CSV/HTML injection safeguards are release
  gates, not optional checks.
- The implementation must not create, update, or restore the live pilot Jira
  project. Backup restore targets an explicitly named scratch database.
- A release-ready conclusion requires passing automated gates and no known
  authorization, report-correctness, or write-integrity defect. A working-week
  log records the remaining human evidence and observed friction.

## Decision

Add a small `pilot` operational module and two CLI commands rather than
embedding release logic in web routes or a remote deployment system.

### `scrum-agent preflight`

This command performs safe, read-only local readiness checks and prints a
sanitized status table. It checks:

1. settings can load and `web_host` is loopback;
2. a database URL is configured, and migrations can be read as applied;
3. model and embedding settings are present for the integrated chat/retrieval
   path;
4. collection freshness and token-expiry alarms are healthy;
5. the suggestion switch is shown explicitly so a disabled quality gate cannot
   be mistaken for enabled related-work behavior.

It returns exit code 0 only when every required check passes. It reports a
named remediation for each failure while never printing URLs with embedded
credentials, tokens, raw environment data, issue text, or model prompts.

### `scrum-agent pilot-check`

This command is the repeatable automated release-gate runner. It invokes the
focused in-repository test groups for scope/permissions, approval and update
integrity, model-tool behavior, export injection, and local operational checks.
It runs with `PYTHONPATH=src` so it cannot accidentally validate an installed
package from another worktree. The command reports the invoked gate names and
returns the test runner's nonzero status on failure. It does not use live Jira
credentials or modify a database.

### Local logging

Add optional settings for a log directory and rotation limits. The application
continues logging to stderr by default. When configured, it additionally writes
rotating UTF-8 log files under a supplied local directory. The log setup uses
the project’s existing standard logging and keeps third-party HTTP logs quiet.
No request bodies, authorization headers, configuration values, tokens, or
issue contents are added to logs.

## Data flow and boundaries

```text
environment / git-ignored .env
              |
           Settings
              |
     preflight --- safe local + live read-only health checks
              |                         |
     serve --- loopback web UI --- PostgreSQL durable state
              |
         pilot-check --- isolated automated tests (no live writes)
              |
     manual runbook --- live scenarios, scratch-DB recovery, pilot log
```

The web UI remains loopback-only. Its local browser session establishes the
single personal-pilot identity already used by the approval routes; this is not
a multi-user authentication system. PostgreSQL-backed ADK sessions persist
agent state across restarts. The transient rendered transcript remains a UI
cache and is documented as such rather than misrepresented as durable history.

## Operations runbook and evidence

The README links to a Week 12 runbook covering:

- secret-safe setup, migration, preflight and launch;
- collection and reindex order;
- a realistic search → report → draft → reviewed write walkthrough;
- backup and restore only into a freshly created, explicitly named scratch
  database, including row-count/migration verification and cleanup;
- collector/job failure recovery and log review;
- latency capture against the agreed pilot dataset;
- a daily working-week log for task/report preparation time, defects,
  observations, and backlog priority.

The runbook marks every action as automated, manual-safe, or manual-live. It
also records limitations: one user, loopback runtime, external-edit race on
Jira updates, live evidence not run by tests, and no claim yet of the
multi-sprint report-time reduction target.

## Tests

- Unit-test operational configuration validation and safe status rendering.
- Exercise preflight success and each required failing condition with fakes;
  assert secrets never appear in output.
- Exercise log-file setup with a temporary directory and ensure rotating files
  are created without duplicate handlers.
- Test `pilot-check` command composition by stubbing its subprocess call;
  assert it uses the current worktree source and never passes credentials.
- Continue running the existing permission, ticketing, update, agent, report,
  export and retrieval gate suites.

## Alternatives considered

1. **Run all gates as a shell script only.** Rejected because it could silently
   import an installed package from another worktree and would not provide
   safe, actionable local configuration diagnostics.
2. **Add hosted monitoring, webhooks, or shared-user login.** Rejected as out
   of scope for the PC-only pilot and incompatible with its local trust model.
3. **Have preflight execute test creates/updates in Jira.** Rejected because a
   release check must not mutate a real board without a separate user decision.

## Out of scope

No shared-team identity, OAuth 3LO, hosting, public ingress, automatic Jira
write testing, secret management service, or claim that a week of personal use
has already occurred.
