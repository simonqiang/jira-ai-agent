# Week 12 personal-pilot runbook

## Safety boundary

Run only on the pilot PC, bound to loopback. Keep `.env` git-ignored; never
paste token values, database URLs, issue content, or logs into source control.
`preflight` and `pilot-check` do not write Jira. Manual-live steps can create
or update only sandbox Jira issues through the normal review/approval flow.

## Start-up (automated/local)

1. Start PostgreSQL: `docker compose up -d`.
2. Set Jira, model, embedding and database variables in `.env`. Optionally set
   `SCRUM_AGENT_LOG_DIRECTORY` for rotating local logs.
3. Run `PYTHONPATH=src scrum-agent migrate`, then `PYTHONPATH=src scrum-agent preflight`.
4. Run `PYTHONPATH=src scrum-agent pilot-check`, then start `scrum-agent serve`.

## Integrated workflow (manual-live)

Run `collect`, `reindex`, and `freshness`; then search, generate a sprint
report, draft a ticket, and perform one sandbox reviewed write. Inspect Jira
and the durable execution record. Record search/report latency against the
agreed pilot dataset.

## Recovery drill (manual-safe, scratch database only)

Dump after a successful collection. Restore only to a freshly created named
scratch database, compare migration rows/table counts, then drop it. Never
restore into `scrum_agent` during this drill.

```bash
docker compose exec -T db pg_dump -U scrum_agent scrum_agent > pilot-backup.sql
docker compose exec db createdb -U scrum_agent scrum_agent_restore_check
docker compose exec -T db psql -U scrum_agent -d scrum_agent_restore_check < pilot-backup.sql
docker compose exec db dropdb -U scrum_agent scrum_agent_restore_check
rm pilot-backup.sql
```

For stopped collection or report work, rerun the command; durable records retain
interrupted state. Review rotating local logs without sharing sensitive content.

## Working-week evidence

Daily, record task type, start/end time, report-preparation minutes, latency,
defect/workaround, and suggested backlog priority. Do not claim the multi-sprint
50% report-time target from this one week. Limits: one local user, loopback only,
transient rendered chat transcript, and Jira's external-edit race on updates.
