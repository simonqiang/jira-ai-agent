# Scrum Master Jira Assistant — personal pilot

An internal assistant that reduces a Scrum Master's time spent finding work, assembling
sprint reports and preparing clear Jira tickets. Design and weekly roadmap:

- [Product requirements and architecture](docs/superpowers/specs/2026-09-27-scrum-master-agent-design.md)
- [Weekly implementation roadmap](docs/superpowers/plans/2026-09-27-weekly-delivery-roadmap.md)

Status: **Week 1 — foundation implemented; acceptance incomplete**. Live issue reads
work, but board endpoints currently return 401; anonymized examples and the report
baseline are still pending. See the [Week 1 verification note](docs/superpowers/notes/2026-09-27-week-1.md).

## Quickstart

Use Python **3.11–3.13** (the configured CI matrix). The example uses 3.13; choose an
installed supported version. Quote `'.[dev]'` so installation also works in zsh.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env   # fill in your Jira values (never commit .env)

python -m scrum_agent          # probe: fetch known issue + board metadata
pytest                         # run tests (no live Jira needed)
ruff check . && ruff format --check .
pre-commit install             # one-time: lint + gitleaks secret scanning on commit
pre-commit run --all-files     # verify hooks now (first run requires network access)
```

## Configuration

All settings come from the environment (prefix `SCRUM_AGENT_`) or `.env`; see
[`.env.example`](.env.example) for the full list. Tokens are masked in settings reprs
and formatted validation errors; raw upstream error bodies are not printed. Do not
log auth headers, raw validation-error inputs, or environment contents.

The probe prints ticket/board metadata and a source link for local inspection, but
omits assignee names. **Do not paste its raw output into commits or shared logs**;
anonymize summaries, names and links before recording evidence.

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

Find your cloud ID by opening `https://<your-site>.atlassian.net/_edge/tenant_info`
and copy its `cloudId` to the local `.env` (do not paste credentials into a URL).
See [Atlassian's cloud ID instructions](https://support.atlassian.com/jira/kb/retrieve-my-atlassian-sites-cloud-id/).
The example configuration selects `basic_central`, matching the scoped-token pilot.

The Week 1 board reads require these granular read scopes in addition to working
issue-read access:

- `read:board-scope:jira-software` — board metadata.
- `read:issue-details:jira` — also required by the board metadata endpoint.
- `read:board-scope.admin:jira-software` — board configuration (a read scope).
- `read:project:jira` — also required by board configuration.

Check these against the [Jira board API reference](https://developer.atlassian.com/cloud/jira/software/rest/api-group-board/).
An issue read succeeding does not establish board access. If board calls return
401/403, check the token's selected scopes and the user's board/filter permissions;
update or replace the token through Atlassian's UI and keep it only in `.env`.
No Jira write scopes are needed for Week 1.

### Probe acceptance

`python -m scrum_agent probe` must exit 0 and show the configured Scrum board,
filter ID, estimation type/field, status IDs per column, done-status IDs and known
issue link. Board configuration returns status **IDs**, not names; the last column
containing mapped statuses supplies the done IDs. Verify the saved board filter's
project scope in Jira: a board's location alone does not prove its issue scope.
Exit 1 means Jira/network or unsupported-board failure; exit 2 means invalid settings.

Issue reads and returned search results are restricted to the pilot project; board
reads are restricted to the configured board ID. The low-level Week 1 search helper
accepts JQL predicates only (no `ORDER BY`). Typed filtering, sorting and more complete
search behavior belong to Week 2; there is no ADK agent or write capability yet.

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
