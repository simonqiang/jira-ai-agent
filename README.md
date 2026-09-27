# Scrum Master Jira Assistant — personal pilot

An internal assistant that reduces a Scrum Master's time spent finding work, assembling
sprint reports and preparing clear Jira tickets. Design and weekly roadmap:

- [Product requirements and architecture](docs/superpowers/specs/2026-09-27-scrum-master-agent-design.md)
- [Weekly implementation roadmap](docs/superpowers/plans/2026-09-27-weekly-delivery-roadmap.md)

Status: **Week 1 — working Jira foundation** (typed connection, board metadata, probe CLI, CI).

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env   # fill in your Jira values (never commit .env)

python -m scrum_agent          # probe: fetch known issue + board metadata
pytest                         # run tests (no live Jira needed)
ruff check . && ruff format --check .
pre-commit install             # one-time: lint + gitleaks secret scanning on commit
```

## Configuration

All settings come from the environment (prefix `SCRUM_AGENT_`) or `.env`; see
[`.env.example`](.env.example) for the full list. The API token is read as a secret and
never appears in logs, reprs or errors.

### Authentication modes

| Mode | Use when | Endpoint |
|---|---|---|
| `basic_site` (default) | Classic (unscoped) API token | `https://<site>/rest/api/3/...` |
| `basic_central` | Scoped token, Basic scheme | `https://api.atlassian.com/ex/jira/<cloudId>/...` |
| `bearer_central` | Scoped token, Bearer scheme | `https://api.atlassian.com/ex/jira/<cloudId>/...` |

Atlassian's scoped API tokens must call the central `api.atlassian.com` endpoints, which
need your site's `cloudId`. If a probe returns 401/403 with `basic_site` and your token is
scoped, switch to a central mode and set `SCRUM_AGENT_JIRA_CLOUD_ID`. If the probe still
fails, consult [ADR-0001](docs/decisions/0001-pilot-scope-and-authentication.md) for the
Week 1 decision context.

## Layout

```
src/scrum_agent/         application package
  config.py              validated settings (secrets from env)
  app.py                 CLI entry point (`python -m scrum_agent`)
  jira/client.py         typed Jira Cloud client (REST v3 + board APIs)
  jira/models.py         immutable read models
  jira/errors.py         typed errors (auth/permission/not-found/rate-limit)
tests/                   unit tests against a mocked transport
docs/decisions/          decision records (authentication, metric policy)
docs/samples/            anonymized ticket-example and baseline templates
.github/workflows/       CI: lint, tests, secret scan
```

## Security rules

- `.env` and API tokens never enter source control; gitleaks runs in pre-commit and CI.
- App roles never grant additional Jira permissions (spec §1).
- Tests use mocked transports — no live credentials needed, no real issue data.
