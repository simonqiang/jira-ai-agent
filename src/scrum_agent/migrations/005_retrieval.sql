-- Week 10 permission-aware semantic retrieval. Chunks of collected issue
-- content with their source revision, content hash and embedding provenance,
-- so a hit can be re-verified against live Jira before its text reaches the
-- model (spec §7: never mix vector spaces; no approximate index until a
-- benchmark justifies one).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS scrum_agent.issue_chunks (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issue_id       text NOT NULL REFERENCES scrum_agent.issue_snapshots(issue_id) ON DELETE CASCADE,
    issue_key      text NOT NULL,
    site           text NOT NULL,
    project_key    text NOT NULL,
    chunk_kind     text NOT NULL CHECK (chunk_kind IN ('summary', 'description')),
    chunk_index    integer NOT NULL,  -- 0 for the summary; ADF block order for the description
    heading        text,              -- nearest ADF heading above a description block
    content        text NOT NULL,
    content_hash   text NOT NULL,     -- sha256 of the exact indexed text
    source_updated timestamptz,       -- issue 'updated' revision at index time
    source_url     text NOT NULL,
    embedding      vector NOT NULL,   -- dimensions follow embedding_model
    embedding_model text NOT NULL,    -- a query is only compared within its own model's space
    indexed_at     timestamptz NOT NULL DEFAULT now(),
    content_tsv    tsvector GENERATED ALWAYS AS (
                       to_tsvector('english', coalesce(heading, '') || ' ' || content)
                   ) STORED,
    UNIQUE (issue_id, chunk_kind, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_issue_key
    ON scrum_agent.issue_chunks (issue_key);

CREATE INDEX IF NOT EXISTS idx_chunks_tsv
    ON scrum_agent.issue_chunks USING gin (content_tsv);
