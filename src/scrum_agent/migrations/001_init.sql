-- Week 4 collection storage. App tables live in their own schema; ADK's
-- DatabaseSessionService owns public.{sessions,events,app_states,user_states,
-- metadata} and creates them itself, so never create or touch those here.
CREATE SCHEMA IF NOT EXISTS scrum_agent;

CREATE TABLE IF NOT EXISTS scrum_agent.collection_runs (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL
                  CHECK (status IN ('running', 'success', 'error', 'interrupted')),
    trigger       text NOT NULL DEFAULT 'manual'
                  CHECK (trigger IN ('manual', 'scheduled')),
    issues_seen   integer NOT NULL DEFAULT 0,
    events_seen   integer NOT NULL DEFAULT 0,
    pages_fetched integer NOT NULL DEFAULT 0,
    error         text
);

CREATE TABLE IF NOT EXISTS scrum_agent.issue_snapshots (
    issue_id       text PRIMARY KEY,           -- Jira numeric issue id, as text
    issue_key      text NOT NULL UNIQUE,
    summary        text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'Unknown',
    issue_type     text NOT NULL DEFAULT 'Unknown',
    assignee       text,
    updated        timestamptz,
    fields         jsonb NOT NULL DEFAULT '{}'::jsonb
                   CHECK (jsonb_typeof(fields) = 'object'),
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_run  bigint REFERENCES scrum_agent.collection_runs(id),
    deleted_at     timestamptz   -- tombstone: issue gone (404) or access lost (403)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_live
    ON scrum_agent.issue_snapshots (status) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS scrum_agent.issue_events (
    issue_id      text NOT NULL REFERENCES scrum_agent.issue_snapshots(issue_id),
    changelog_id  text NOT NULL,   -- Jira changelog ENTRY id; its items[] have no ids
    item_index    integer NOT NULL, -- position in the entry's items[]
    field         text NOT NULL,   -- e.g. 'status', 'Sprint', 'Story Points'
    field_id      text,
    from_id       text,
    from_value    text,            -- fromString
    to_id         text,
    to_value      text,            -- toString
    author        text,            -- author display name
    occurred_at   timestamptz NOT NULL, -- entry.created
    seen_run      bigint NOT NULL REFERENCES scrum_agent.collection_runs(id),
    PRIMARY KEY (issue_id, changelog_id, item_index)  -- replay dedup key
);

CREATE INDEX IF NOT EXISTS idx_events_issue_time
    ON scrum_agent.issue_events (issue_id, occurred_at);

CREATE TABLE IF NOT EXISTS scrum_agent.board_config_versions (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id    bigint NOT NULL,
    config_hash text NOT NULL,  -- sha256 of the canonical JSON
    config      jsonb NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now(),
    seen_run    bigint REFERENCES scrum_agent.collection_runs(id)
);
-- No uniqueness on config_hash: A->B->A is a real change sequence. The
-- collector appends only when the hash differs from the latest row.

CREATE TABLE IF NOT EXISTS scrum_agent.ingestion_checkpoints (
    target          text PRIMARY KEY,  -- 'issues' in Week 4
    last_success_at timestamptz,
    last_run_id     bigint REFERENCES scrum_agent.collection_runs(id),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_runs_status_started
    ON scrum_agent.collection_runs (status, started_at);

-- Collection freshness and gap visibility, derived rather than stored.
CREATE OR REPLACE VIEW scrum_agent.collection_freshness AS
WITH last_success AS (
    SELECT max(finished_at) AS at FROM scrum_agent.collection_runs WHERE status = 'success'
)
SELECT
    ls.at                                              AS last_success_at,
    now() - ls.at                                      AS success_age,
    (SELECT count(*) FROM scrum_agent.collection_runs r
      WHERE r.status IN ('error', 'interrupted')
        AND ls.at IS NOT NULL AND r.started_at > ls.at) AS failures_since_success,
    (SELECT count(*) FROM scrum_agent.collection_runs r
      WHERE r.status = 'running')                       AS runs_in_flight,
    (SELECT count(*) FROM scrum_agent.issue_snapshots
      WHERE deleted_at IS NULL)                         AS live_issues,
    (SELECT count(*) FROM scrum_agent.issue_snapshots
      WHERE deleted_at IS NOT NULL)                     AS tombstoned_issues,
    (SELECT count(*) FROM scrum_agent.issue_events)     AS events_total
FROM last_success ls;
