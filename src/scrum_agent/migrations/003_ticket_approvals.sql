-- Week 8: immutable draft payloads, human approvals and create execution audit.
CREATE TABLE IF NOT EXISTS scrum_agent.ticket_drafts (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    creator            text NOT NULL,
    issue_type         text NOT NULL,
    template_version   text NOT NULL,
    payload            jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    payload_hash       text NOT NULL,
    correlation_marker text NOT NULL UNIQUE,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scrum_agent.ticket_approvals (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    draft_id     bigint NOT NULL REFERENCES scrum_agent.ticket_drafts(id),
    approver     text NOT NULL,
    payload_hash text NOT NULL,
    approved_at  timestamptz NOT NULL,
    expires_at   timestamptz NOT NULL,
    CHECK (expires_at > approved_at)
);

CREATE TABLE IF NOT EXISTS scrum_agent.ticket_executions (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    approval_id        bigint NOT NULL UNIQUE REFERENCES scrum_agent.ticket_approvals(id),
    payload_hash       text NOT NULL,
    correlation_marker text NOT NULL,
    status             text NOT NULL CHECK (status IN ('executing', 'succeeded', 'outcome_unknown')),
    issue_key          text,
    reconciled         boolean NOT NULL DEFAULT false,
    started_at         timestamptz NOT NULL,
    finished_at        timestamptz
);
