-- Week 5 asynchronous sprint reports. One table: the durable job row becomes
-- the report record on completion (spec §4 identity fields live in `report`).
-- request_key makes duplicate delivery idempotent: re-submitting the same
-- board/sprint within the same cutoff hour returns the existing job.
CREATE TABLE IF NOT EXISTS scrum_agent.report_jobs (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_key      text NOT NULL UNIQUE,
    kind             text NOT NULL DEFAULT 'sprint_report',
    board_id         bigint NOT NULL,
    sprint_id        bigint NOT NULL,
    status           text NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'done', 'error')),
    attempts         integer NOT NULL DEFAULT 0,
    estimate_seconds integer NOT NULL DEFAULT 0,
    requested_at     timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz,
    error            text,
    report           jsonb CHECK (report IS NULL OR jsonb_typeof(report) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_report_jobs_status
    ON scrum_agent.report_jobs (status, requested_at);
