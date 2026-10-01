"""Typed queries over the collection schema (Week 4).

``PgStorage`` is the only place raw collection SQL lives. The collector and
freshness code depend on these method names; tests provide an in-memory fake
with the same interface rather than a formal protocol.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import psycopg

from scrum_agent.jira.models import ChangelogEntry

# A changelog item flattened for insert, in 001_init.sql column order after
# issue_id: (changelog_id, item_index, field, field_id, from_id, from_value,
# to_id, to_value, author, occurred_at).


def canonical_config_hash(config: dict) -> str:
    """Stable hash of a board configuration payload for change detection."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def entry_rows(entries: list[ChangelogEntry]) -> list[tuple]:
    """Flatten changelog entries into dedup-keyed event rows."""
    rows: list[tuple] = []
    for entry in entries:
        for index, item in enumerate(entry.items):
            rows.append(
                (
                    entry.id,
                    index,
                    item.field,
                    item.field_id,
                    item.from_id,
                    item.from_value,
                    item.to_id,
                    item.to_value,
                    entry.author,
                    entry.created,
                )
            )
    return rows


def _vector_literal(vector: list[float]) -> str:
    """pgvector text literal, cast to vector at the call site."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


class PgStorage:
    """Collection storage against local Postgres (sync psycopg)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    # -- collection runs ---------------------------------------------------

    def start_run(self, trigger: str = "manual") -> int:
        """Open a run; mark any stale 'running' rows interrupted first."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scrum_agent.collection_runs
                SET status = 'interrupted', finished_at = now(),
                    error = 'superseded by a newer run'
                WHERE status = 'running'
                """
            )
            cur.execute(
                "INSERT INTO scrum_agent.collection_runs (trigger) VALUES (%s) RETURNING id",
                (trigger,),
            )
            return int(cur.fetchone()["id"])

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        issues_seen: int = 0,
        events_seen: int = 0,
        pages_fetched: int = 0,
        error: str | None = None,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scrum_agent.collection_runs
                SET finished_at = now(), status = %s, issues_seen = %s,
                    events_seen = %s, pages_fetched = %s, error = %s
                WHERE id = %s
                """,
                (status, issues_seen, events_seen, pages_fetched, error, run_id),
            )

    def last_success(self) -> datetime | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT max(finished_at) AS at FROM scrum_agent.collection_runs "
                "WHERE status = 'success'"
            )
            return cur.fetchone()["at"]

    # -- issue snapshots ---------------------------------------------------

    def upsert_snapshot(
        self,
        *,
        issue_id: str,
        issue_key: str,
        summary: str,
        status: str,
        issue_type: str,
        assignee: str | None,
        updated: datetime | None,
        fields: dict,
        run_id: int,
    ) -> None:
        """Insert or refresh a snapshot; clears an existing tombstone (access
        restored) and keeps first_seen_at."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.issue_snapshots
                    (issue_id, issue_key, summary, status, issue_type, assignee,
                     updated, fields, last_seen_run)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (issue_id) DO UPDATE SET
                    issue_key = EXCLUDED.issue_key,
                    summary = EXCLUDED.summary,
                    status = EXCLUDED.status,
                    issue_type = EXCLUDED.issue_type,
                    assignee = EXCLUDED.assignee,
                    updated = EXCLUDED.updated,
                    fields = EXCLUDED.fields,
                    deleted_at = NULL,
                    last_seen_run = EXCLUDED.last_seen_run
                """,
                (
                    issue_id,
                    issue_key,
                    summary,
                    status,
                    issue_type,
                    assignee,
                    updated,
                    json.dumps(fields),
                    run_id,
                ),
            )

    def tombstone_issue(self, issue_key: str) -> bool:
        """Mark an inaccessible issue deleted; False when nothing matched."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scrum_agent.issue_snapshots
                SET deleted_at = now()
                WHERE issue_key = %s AND deleted_at IS NULL
                """,
                (issue_key,),
            )
            return cur.rowcount > 0

    # -- changelog events --------------------------------------------------

    def insert_events(self, issue_id: str, rows: list[tuple], run_id: int) -> int:
        """Insert dedup-keyed event rows; returns how many were new."""
        if not rows:
            return 0
        with self._conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO scrum_agent.issue_events
                    (issue_id, changelog_id, item_index, field, field_id,
                     from_id, from_value, to_id, to_value, author, occurred_at, seen_run)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (issue_id, changelog_id, item_index) DO NOTHING
                """,
                [(issue_id, *row, run_id) for row in rows],
            )
            return cur.rowcount

    # -- board configuration versions ---------------------------------------

    def latest_board_config(self, board_id: int) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT config_hash, config FROM scrum_agent.board_config_versions
                WHERE board_id = %s
                ORDER BY id DESC LIMIT 1
                """,
                (board_id,),
            )
            return cur.fetchone()

    def insert_board_config(
        self, *, board_id: int, config_hash: str, config: dict, run_id: int
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.board_config_versions
                    (board_id, config_hash, config, seen_run)
                VALUES (%s, %s, %s, %s)
                """,
                (board_id, config_hash, json.dumps(config), run_id),
            )

    # -- checkpoints and freshness ------------------------------------------

    def set_checkpoint(self, target: str, at: datetime, run_id: int) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.ingestion_checkpoints
                    (target, last_success_at, last_run_id, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (target) DO UPDATE SET
                    last_success_at = EXCLUDED.last_success_at,
                    last_run_id = EXCLUDED.last_run_id,
                    updated_at = now()
                """,
                (target, at, run_id),
            )

    def freshness(self) -> dict:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM scrum_agent.collection_freshness")
            return dict(cur.fetchone())

    # -- report inputs (Week 5) ----------------------------------------------

    def live_snapshots(self) -> list[dict]:
        """Every non-tombstoned snapshot: the report's persisted input set."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT issue_id, issue_key, summary, status, issue_type, assignee,
                       updated, fields
                FROM scrum_agent.issue_snapshots
                WHERE deleted_at IS NULL
                """
            )
            return cur.fetchall()

    # -- retrieval chunks (Week 10) ------------------------------------------

    def chunk_signatures(self) -> dict[str, tuple[datetime | None, str]]:
        """issue_id -> (source revision, embedding model) of indexed chunks."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT issue_id, source_updated, embedding_model
                FROM scrum_agent.issue_chunks
                """
            )
            return {row["issue_id"]: (row["source_updated"], row["embedding_model"]) for row in cur}

    def replace_chunks(
        self,
        *,
        issue_id: str,
        issue_key: str,
        site: str,
        project_key: str,
        source_updated: datetime | None,
        rows: list[tuple],
        embedding_model: str,
    ) -> None:
        """Atomically replace one issue's chunks.

        rows: (chunk_kind, chunk_index, heading, content, content_hash,
        embedding) — the delete+insert runs in one transaction, so a search
        never observes an issue with no chunks.
        """
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM scrum_agent.issue_chunks WHERE issue_id = %s", (issue_id,))
            cur.executemany(
                """
                INSERT INTO scrum_agent.issue_chunks
                    (issue_id, issue_key, site, project_key, chunk_kind, chunk_index,
                     heading, content, content_hash, source_updated, source_url,
                     embedding, embedding_model)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
                """,
                [
                    (
                        issue_id,
                        issue_key,
                        site,
                        project_key,
                        kind,
                        index,
                        heading,
                        content,
                        content_hash,
                        source_updated,
                        f"https://{site}/browse/{issue_key}",
                        _vector_literal(embedding),
                        embedding_model,
                    )
                    for kind, index, heading, content, content_hash, embedding in rows
                ],
            )

    def prune_orphan_chunks(self) -> None:
        """Drop chunks of tombstoned snapshots (issue gone or access lost)."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM scrum_agent.issue_chunks c
                USING scrum_agent.issue_snapshots s
                WHERE c.issue_id = s.issue_id AND s.deleted_at IS NOT NULL
                """
            )

    def delete_chunks(self, *, issue_key: str) -> None:
        """Invalidate an issue's chunks after a failed live recheck."""
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM scrum_agent.issue_chunks WHERE issue_key = %s", (issue_key,))

    def hybrid_search(
        self,
        *,
        query: str,
        query_embedding: list[float],
        embedding_model: str,
        limit: int,
    ) -> list[dict]:
        """Fuse ranked vector and full-text candidates (RRF, k=60), then keep
        the best chunk per issue and return the top ``limit`` issues."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                WITH vec AS (
                    SELECT id, row_number() OVER (ORDER BY embedding <=> %s::vector) AS rn
                    FROM scrum_agent.issue_chunks
                    WHERE embedding_model = %s
                ),
                txt AS (
                    SELECT c.id, row_number() OVER (
                               ORDER BY ts_rank(c.content_tsv, q.tsq) DESC
                           ) AS rn
                    FROM scrum_agent.issue_chunks c,
                         websearch_to_tsquery('english', %s) AS q(tsq)
                    WHERE q.tsq @@ c.content_tsv AND c.embedding_model = %s
                ),
                fused AS (
                    SELECT c.issue_key, c.chunk_kind, c.heading, c.content,
                           c.source_updated, c.source_url, s.summary AS title,
                           1 - (c.embedding <=> %s::vector) AS similarity,
                           coalesce(1.0 / (60 + vec.rn), 0)
                               + coalesce(1.0 / (60 + txt.rn), 0) AS score
                    FROM scrum_agent.issue_chunks c
                    JOIN scrum_agent.issue_snapshots s
                      ON s.issue_id = c.issue_id AND s.deleted_at IS NULL
                    LEFT JOIN vec ON vec.id = c.id
                    LEFT JOIN txt ON txt.id = c.id
                    WHERE vec.id IS NOT NULL OR txt.id IS NOT NULL
                )
                SELECT issue_key, chunk_kind, heading, content, source_updated,
                       source_url, title, similarity, score
                FROM (
                    SELECT DISTINCT ON (issue_key) *
                    FROM fused
                    ORDER BY issue_key, score DESC
                ) best
                ORDER BY score DESC
                LIMIT %s
                """,
                # Placeholder order: vec.embedding, vec.model, txt.query,
                # txt.model, fused.embedding (similarity), limit.
                (
                    _vector_literal(query_embedding),
                    embedding_model,
                    query,
                    embedding_model,
                    _vector_literal(query_embedding),
                    limit,
                ),
            )
            return cur.fetchall()

    def events_for_issue_ids(self, issue_ids: list[str]) -> list[dict]:
        """Ordered changelog evidence for a report's persisted issue inputs."""
        if not issue_ids:
            return []
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT issue_id, changelog_id, item_index, field, field_id,
                       from_id, from_value, to_id, to_value, occurred_at
                FROM scrum_agent.issue_events
                WHERE issue_id = ANY(%s)
                ORDER BY issue_id, occurred_at, changelog_id, item_index
                """,
                (issue_ids,),
            )
            return cur.fetchall()

    def create_draft(
        self,
        *,
        creator: str,
        issue_type: str,
        template_version: str,
        payload: dict,
        payload_hash: str,
        correlation_marker: str,
        created_at: datetime,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.ticket_drafts
                    (creator, issue_type, template_version, payload, payload_hash,
                     correlation_marker, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (
                    creator,
                    issue_type,
                    template_version,
                    json.dumps(payload),
                    payload_hash,
                    correlation_marker,
                    created_at,
                ),
            )
            return int(cur.fetchone()["id"])

    def get_draft(self, draft_id: int) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM scrum_agent.ticket_drafts WHERE id = %s", (draft_id,))
            return cur.fetchone()

    def create_approval(
        self,
        *,
        draft_id: int,
        approver: str,
        payload_hash: str,
        approved_at: datetime,
        expires_at: datetime,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.ticket_approvals
                    (draft_id, approver, payload_hash, approved_at, expires_at)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (draft_id, approver, payload_hash, approved_at, expires_at),
            )
            return int(cur.fetchone()["id"])

    def get_approval(self, approval_id: int) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM scrum_agent.ticket_approvals WHERE id = %s", (approval_id,))
            return cur.fetchone()

    def create_execution(
        self,
        *,
        approval_id: int,
        payload_hash: str,
        correlation_marker: str,
        status: str,
        started_at: datetime,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.ticket_executions
                    (approval_id, payload_hash, correlation_marker, status, started_at)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (approval_id, payload_hash, correlation_marker, status, started_at),
            )
            return int(cur.fetchone()["id"])

    def get_execution_by_approval(self, approval_id: int) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM scrum_agent.ticket_executions WHERE approval_id = %s", (approval_id,)
            )
            return cur.fetchone()

    def update_execution(
        self,
        execution_id: int,
        *,
        status: str,
        issue_key: str | None = None,
        reconciled: bool = False,
        finished_at: datetime,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scrum_agent.ticket_executions
                SET status = %s, issue_key = %s, reconciled = %s, finished_at = %s
                WHERE id = %s
                """,
                (status, issue_key, reconciled, finished_at, execution_id),
            )

    # -- reviewed issue updates (Week 9) ---------------------------------------

    def create_update_proposal(
        self,
        *,
        creator: str,
        issue_key: str,
        base: dict,
        changes: dict,
        payload_hash: str,
        created_at: datetime,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.ticket_update_proposals
                    (creator, issue_key, base, changes, payload_hash, created_at)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (
                    creator,
                    issue_key,
                    json.dumps(base),
                    json.dumps(changes),
                    payload_hash,
                    created_at,
                ),
            )
            return int(cur.fetchone()["id"])

    def get_update_proposal(self, proposal_id: int) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM scrum_agent.ticket_update_proposals WHERE id = %s", (proposal_id,)
            )
            return cur.fetchone()

    def create_update_approval(
        self,
        *,
        proposal_id: int,
        approver: str,
        payload_hash: str,
        approved_at: datetime,
        expires_at: datetime,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.ticket_update_approvals
                    (proposal_id, approver, payload_hash, approved_at, expires_at)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (proposal_id, approver, payload_hash, approved_at, expires_at),
            )
            return int(cur.fetchone()["id"])

    def get_update_approval(self, approval_id: int) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM scrum_agent.ticket_update_approvals WHERE id = %s", (approval_id,)
            )
            return cur.fetchone()

    def create_update_execution(
        self,
        *,
        approval_id: int,
        payload_hash: str,
        issue_key: str,
        status: str,
        requested: dict,
        started_at: datetime,
    ) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.ticket_update_executions
                    (approval_id, payload_hash, issue_key, status, requested, started_at)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (
                    approval_id,
                    payload_hash,
                    issue_key,
                    status,
                    json.dumps(requested),
                    started_at,
                ),
            )
            return int(cur.fetchone()["id"])

    def get_update_execution_by_approval(self, approval_id: int) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM scrum_agent.ticket_update_executions
                WHERE approval_id = %s
                """,
                (approval_id,),
            )
            return cur.fetchone()

    def finish_update_execution(
        self, execution_id: int, *, status: str, verified: dict, finished_at: datetime
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scrum_agent.ticket_update_executions
                SET status = %s, verified = %s, finished_at = %s
                WHERE id = %s
                """,
                (status, json.dumps(verified), finished_at, execution_id),
            )

    # -- report jobs (Week 5) --------------------------------------------------

    def submit_report_job(
        self, *, request_key: str, board_id: int, sprint_id: int, estimate_seconds: int
    ) -> tuple[int, bool]:
        """Insert a job keyed by request identity; (id, created) with the
        existing row's id when the same request is delivered twice."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrum_agent.report_jobs
                    (request_key, board_id, sprint_id, estimate_seconds)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (request_key) DO NOTHING
                RETURNING id
                """,
                (request_key, board_id, sprint_id, estimate_seconds),
            )
            row = cur.fetchone()
            if row is not None:
                return int(row["id"]), True
            cur.execute(
                "SELECT id FROM scrum_agent.report_jobs WHERE request_key = %s",
                (request_key,),
            )
            return int(cur.fetchone()["id"]), False

    def claim_next_report_job(self) -> dict | None:
        """Requeue jobs orphaned by a restart, then claim one queued job."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scrum_agent.report_jobs
                SET status = 'queued', error = 'requeued after restart'
                WHERE status = 'running'
                """
            )
            cur.execute(
                """
                UPDATE scrum_agent.report_jobs
                SET status = 'running', started_at = now(), attempts = attempts + 1
                WHERE id = (
                    SELECT id FROM scrum_agent.report_jobs
                    WHERE status = 'queued'
                    ORDER BY requested_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, board_id, sprint_id, attempts
                """
            )
            row = cur.fetchone()
            return dict(row) if row is not None else None

    def finish_report_job(
        self,
        job_id: int,
        *,
        status: str,
        error: str | None = None,
        report: dict | None = None,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scrum_agent.report_jobs
                SET status = %s, finished_at = now(), error = %s, report = %s
                WHERE id = %s
                """,
                (status, error, json.dumps(report) if report is not None else None, job_id),
            )

    def get_report_job(self, job_id: int) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM scrum_agent.report_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            # psycopg decodes jsonb to dict; the fake stores one directly.
            return row
