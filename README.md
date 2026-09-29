# Scrum Master Jira Assistant — personal pilot

An internal assistant that reduces a Scrum Master's time spent finding work, assembling
sprint reports and preparing clear Jira tickets. Design and weekly roadmap:

- [Product requirements and architecture](docs/superpowers/specs/2026-09-27-scrum-master-agent-design.md)
- [Weekly implementation roadmap](docs/superpowers/plans/2026-09-27-weekly-delivery-roadmap.md)

Status: **Week 4 — continuous sprint-history collection in implementation**. Week 1's
live issue and board reads work for board 23031; Week 2 adds sprint selection, typed
filters, centralized pilot-scope checks and a checked query set; Week 3 ships the
local read-only ADK chat. Week 4 stores issue snapshots, changelog events, board
configuration versions and collection checkpoints in a local PostgreSQL database —
see the [Week 3 note](docs/superpowers/notes/2026-09-29-week-3.md) and the
[Week 2 note](docs/superpowers/notes/2026-09-27-week-2.md).

The intended runtime is a **local PC application**. Google Cloud deployment, public
webhooks and a hosted collector are not required. Google ADK may call a configured
remote model provider (such as Gemini), so model credentials and network access may
still be needed; that is separate from hosting the agent.

For the preferred local ADK path, use a Gemini API key from Google AI Studio in the
local `.env`; this does not require a Google Cloud project, `gcloud`, Vertex AI or any
deployment command. Bind the local web service to `127.0.0.1` only — do not expose it
to your LAN or the internet. See the [ADK local-authentication guidance](https://google.github.io/agents-cli/guide/authentication/).

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
accepts JQL predicates only (no `ORDER BY`). There is no ADK agent or write
capability yet.

### Search (Week 2)

```bash
python -m scrum_agent sprints                      # sprint selection: id, name, state
python -m scrum_agent sprints --state active
python -m scrum_agent search --sprint "Payments R2" --type Bug --unresolved
python -m scrum_agent search --sprint 78 --status "In Progress" --label payments
python -m scrum_agent search --assignee Unassigned --label export
python -m scrum_agent search --issue PAY-1          # single issue-key lookup
```

Filters are typed (`--status`, `--type`, `--assignee`, `--label`, `--unresolved`,
`--sprint`) and compiled to quoted, project-scoped JQL — no raw JQL input. A sprint
reference accepts an ID, an exact name or a unique substring; an ambiguous name
lists the candidates and exits 1 instead of guessing. An accurate zero is reported
as `No issues match the query (checked: 0 issues).` with exit 0 — never as silence
or an error. Search output omits assignee names; anonymize summaries and links
before recording evidence.

All scope decisions (project, board, sprint origin) are centralized in
`src/scrum_agent/auth` (`PilotScope`); every read path — client, search service and
the Week 3+ agent — must route through it, and out-of-scope data fails closed. The
Week 2 exit checks live in `tests/checked_queries.py` (empty results, ambiguous
sprint names, multi-page collection, cross-project/cross-board leakage); Week 3's
agent must answer the same set.

### Local chat (Week 3)

Set `SCRUM_AGENT_MODEL_NAME` and `SCRUM_AGENT_MODEL_API_KEY` in `.env` (the
configured endpoint must be HTTPS), then run:

```bash
scrum-agent serve
```

The server binds to `127.0.0.1` by default and rejects non-loopback host
configuration. It exposes a short-lived, single-user browser chat at
`http://127.0.0.1:8741`; restarting it or choosing **New conversation** clears
context. The agent has only typed Jira read tools. It cannot create, edit,
transition or delete Jira issues, and all results continue through `PilotScope`.

Each answer shows server-verified Jira source links and per-turn model usage.
Before using real issue content with a provider, review its privacy and retention
terms. `SCRUM_AGENT_MODEL_BASE_URL` defaults to an Anthropic-compatible endpoint;
change it only to the HTTPS API root for the selected compatible provider.

To measure a live-model cost baseline against the checked intents, run:

```bash
scrum-agent baseline
```

It writes a sanitized JSON result under `docs/superpowers/notes/`. Token counts
are recorded when the provider returns usage metadata; provider cost stays
explicitly unknown until pricing is configured, and the command never treats an
unknown value as zero cost.

### Local collection (Week 4)

The collector polls the configured project scope, reconciles every hinted issue
against an authoritative per-issue read plus its full changelog, and stores
snapshots, dedup-keyed changelog events, board-configuration versions and
collection checkpoints. Polling results are hints only; the overlap window and
per-issue reconciliation absorb Jira indexing lag and clock skew.

```bash
docker compose up -d            # local PostgreSQL (loopback only; pgvector image)
scrum-agent migrate             # apply pending SQL migrations (idempotent)
scrum-agent collect             # one idempotent collection cycle + freshness report
scrum-agent collect --full      # reconcile every issue in the project scope
scrum-agent freshness           # freshness/credential alarms; exit 1 when unhealthy
```

Notes:

- The **first full collection reads every issue in the project scope** (about
  4 minutes for ~250 issues plus changelogs); later cycles fetch only what
  changed since the last success minus a 1-hour overlap.
- Events are deduped on (issue, changelog entry, item position), so replaying a
  run never duplicates history. A crashed run stays visible as `interrupted`
  and the next run resumes cleanly; only a successful run advances the
  checkpoint.
- An issue that 404s or becomes inaccessible is tombstoned (`deleted_at`), not
  silently dropped — visible gaps, not silent ones.
- `SCRUM_AGENT_TOKEN_EXPIRES_ON` (ISO date) drives the credential-freshness
  alarm: Jira cannot report a token's expiry date, so it is configured instead.
  An expired token raises an ALARM (`freshness` exits 1); expiry within 7 days
  raises a WARNING.

**Backups** use the PC's normal tooling plus these one-liners (dump after a
successful collection for a consistent snapshot):

```bash
docker compose exec -T db pg_dump -U scrum_agent scrum_agent > backup.sql
docker compose exec -T db psql -U scrum_agent -d scrum_agent < backup.sql
```

**Unattended polling while the PC is on** (optional): schedule `scrum-agent
collect` — for example launchd on macOS:

```bash
# crontab -e : collect every 30 minutes while the Mac is on
*/30 * * * * cd /path/to/this/worktree && .venv/bin/python -m scrum_agent collect >> /tmp/scrum_agent_collect.log 2>&1
```

**Integration tests against a real database** (optional; skipped by default):
point `SCRUM_AGENT_TEST_DATABASE_URL` at a *disposable* database — the test
resets its schema — for example the bundled second database:

```bash
docker compose exec db createdb -U scrum_agent scrum_agent_test   # once
SCRUM_AGENT_TEST_DATABASE_URL=postgresql://scrum_agent:scrum_agent@127.0.0.1:5432/scrum_agent_test pytest tests/test_storage.py
```

With `SCRUM_AGENT_DATABASE_URL` set, ADK chat sessions persist in PostgreSQL
(keyed by app/user/session — single pilot user) and survive an app restart; the
rendered web transcript remains in-process.

## Layout

```
src/scrum_agent/         application package
  config.py              validated settings (secrets from env)
  app.py                 CLI entry point (`python -m scrum_agent`)
  auth/scope.py          centralized pilot-scope access checks (Week 2)
  jira/client.py         typed Jira Cloud client (REST v3 + board/sprint APIs)
  jira/models.py         immutable read models
  jira/errors.py         typed errors (auth/permission/not-found/rate-limit)
  search/filters.py      typed filters compiled to quoted, project-scoped JQL
  search/service.py      issue lookup, sprint selection and search results
  agent/                 narrow read-only ADK tools, chat runner and usage tracking (Week 3)
  web/                   loopback-only FastAPI/Jinja chat UI (Week 3)
  storage/               Postgres connection, migration runner, PgStorage queries (Week 4)
  migrations/            numbered SQL migrations (schema_migrations tracks applied)
  sync/                  idempotent collector + freshness/credential alarms (Week 4)
  search/errors.py       ambiguity/not-found errors that prompt, not guess
tests/                   unit tests against a mocked transport
tests/checked_queries.py the Week 2 checked query set + fixture Jira server
docs/decisions/          decision records (authentication, metric policy)
docs/samples/            anonymized ticket-example and baseline templates
.github/workflows/       CI: lint, tests, secret scan
```

## Security rules

- `.env` and API tokens never enter source control; gitleaks runs in pre-commit and CI.
- App roles never grant additional Jira permissions (spec §1).
- Tests use mocked transports — no live credentials needed, no real issue data.
