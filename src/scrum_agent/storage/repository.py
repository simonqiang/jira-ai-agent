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

    def tombstone_issue(self, issue_id: str) -> bool:
        """Mark an inaccessible issue deleted; False when nothing matched."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scrum_agent.issue_snapshots
                SET deleted_at = now()
                WHERE issue_id = %s AND deleted_at IS NULL
                """,
                (issue_id,),
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
