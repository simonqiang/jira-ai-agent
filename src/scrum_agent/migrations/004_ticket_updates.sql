-- Week 9: reviewed issue updates — diffed proposals, approvals and verified writes.
CREATE TABLE IF NOT EXISTS scrum_agent.ticket_update_proposals (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    creator       text NOT NULL,
    issue_key     text NOT NULL,
    base          jsonb NOT NULL CHECK (jsonb_typeof(base) = 'object'),
    changes       jsonb NOT NULL CHECK (jsonb_typeof(changes) = 'object'),
    payload_hash  text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scrum_agent.ticket_update_approvals (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proposal_id   bigint NOT NULL REFERENCES scrum_agent.ticket_update_proposals(id),
    approver      text NOT NULL,
    payload_hash  text NOT NULL,
    approved_at   timestamptz NOT NULL,
    expires_at    timestamptz NOT NULL,
    CHECK (expires_at > approved_at)
);

CREATE TABLE IF NOT EXISTS scrum_agent.ticket_update_executions (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    approval_id   bigint NOT NULL UNIQUE REFERENCES scrum_agent.ticket_update_approvals(id),
    payload_hash  text NOT NULL,
    issue_key     text NOT NULL,
    status        text NOT NULL
                  CHECK (status IN ('executing', 'rejected_stale', 'succeeded',
                                    'verification_failed', 'failed')),
    requested     jsonb NOT NULL,
    verified      jsonb,
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz
);
