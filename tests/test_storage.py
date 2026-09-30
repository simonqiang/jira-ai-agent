"""Storage-layer tests: migration contract plus (gated) real-database checks.

The integration tests need a disposable Postgres: set
``SCRUM_AGENT_TEST_DATABASE_URL`` (the database is schema-reset by the test).
They are skipped by default, so plain ``pytest`` requires no database — and
never touches live Jira.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from importlib import resources

import pytest

from scrum_agent.storage.db import migration_names
from scrum_agent.storage.repository import canonical_config_hash

_TEST_DSN = os.environ.get("SCRUM_AGENT_TEST_DATABASE_URL", "")

requires_db = pytest.mark.skipif(not _TEST_DSN, reason="SCRUM_AGENT_TEST_DATABASE_URL not set")


def test_migration_names_are_sorted_sql_files() -> None:
    names = migration_names()
    assert names == sorted(names)
    assert names[0] == "001_init.sql"


def test_initial_migration_matches_schema_contract() -> None:
    sql = resources.files("scrum_agent").joinpath("migrations").joinpath("001_init.sql").read_text()
    for table in (
        "scrum_agent.collection_runs",
        "scrum_agent.issue_snapshots",
        "scrum_agent.issue_events",
        "scrum_agent.board_config_versions",
        "scrum_agent.ingestion_checkpoints",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "CREATE OR REPLACE VIEW scrum_agent.collection_freshness" in sql
    # Dedup key: (issue, changelog entry, item position); items have no id.
    assert "PRIMARY KEY (issue_id, changelog_id, item_index)" in sql
    # Run states and the tombstone column the collector relies on.
    assert "CHECK (status IN ('running', 'success', 'error', 'interrupted'))" in sql
    assert "deleted_at" in sql


def test_report_jobs_migration_matches_schema_contract() -> None:
    sql = (
        resources.files("scrum_agent")
        .joinpath("migrations")
        .joinpath("002_report_jobs.sql")
        .read_text()
    )
    assert "CREATE TABLE IF NOT EXISTS scrum_agent.report_jobs" in sql
    assert "request_key      text NOT NULL UNIQUE" in sql  # idempotent identity
    assert "CHECK (status IN ('queued', 'running', 'done', 'error'))" in sql


@requires_db
def test_migrations_and_repository_round_trip() -> None:
    import psycopg
    from psycopg.rows import dict_row

    from scrum_agent.jira.models import ChangelogEntry, ChangelogItem
    from scrum_agent.storage.db import run_migrations
    from scrum_agent.storage.repository import PgStorage, entry_rows

    conn = psycopg.connect(
        _TEST_DSN, row_factory=dict_row, options="-c timezone=UTC", autocommit=True
    )
    with conn.cursor() as cur:
        # ADK's DatabaseSessionService owns public.{sessions,events,...}; prove
        # a pre-existing public.events table does not clash with our schema.
        cur.execute("CREATE TABLE IF NOT EXISTS public.events (id int)")
        cur.execute("DROP SCHEMA IF EXISTS scrum_agent CASCADE")
        cur.execute("DROP TABLE IF EXISTS public.schema_migrations")
    conn.commit()

    assert "001_init.sql" in run_migrations(conn)
    assert run_migrations(conn) == []  # idempotent

    storage = PgStorage(conn)
    run_id = storage.start_run()
    snapshot = dict(
        issue_id="10001",
        issue_key="PAY-1",
        summary="S",
        status="In Progress",
        issue_type="Bug",
        assignee="A. Developer",
        updated=None,
        fields={"summary": "S", "customfield_10002": 5},
        run_id=run_id,
    )
    storage.upsert_snapshot(**snapshot)
    storage.upsert_snapshot(**snapshot)  # replay: still one row
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM scrum_agent.issue_snapshots")
        assert cur.fetchone()["n"] == 1

    entry = ChangelogEntry(
        id="9001",
        created=datetime(2026, 9, 28, tzinfo=UTC),
        author="A. Developer",
        items=(ChangelogItem(field="status", from_value="To Do", to_value="Done"),),
    )
    rows = entry_rows([entry])
    assert storage.insert_events("10001", rows, run_id) == 1
    assert storage.insert_events("10001", rows, run_id) == 0  # dedup on replay
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM scrum_agent.issue_events")
        assert cur.fetchone()["n"] == 1

    assert storage.tombstone_issue("PAY-1") is True
    assert storage.tombstone_issue("PAY-1") is False  # already tombstoned
    storage.upsert_snapshot(**snapshot)  # access restored: tombstone cleared
    with conn.cursor() as cur:
        cur.execute("SELECT deleted_at FROM scrum_agent.issue_snapshots")
        assert cur.fetchone()["deleted_at"] is None

    config_hash = canonical_config_hash({"id": 42})
    storage.insert_board_config(
        board_id=42, config_hash=config_hash, config={"id": 42}, run_id=run_id
    )
    assert storage.latest_board_config(42)["config_hash"] == config_hash

    storage.set_checkpoint("issues", datetime(2026, 9, 28, 12, 0, tzinfo=UTC), run_id)
    storage.finish_run(run_id, status="success", issues_seen=1, events_seen=1)

    freshness = storage.freshness()
    assert freshness["live_issues"] == 1
    assert freshness["events_total"] == 1
    assert freshness["last_success_at"] is not None

    # Week 5 report jobs: identity dedup, claim, finish, orphan requeue.
    job_id, created = storage.submit_report_job(
        request_key="rk-1", board_id=42, sprint_id=78, estimate_seconds=4
    )
    again, created_again = storage.submit_report_job(
        request_key="rk-1", board_id=42, sprint_id=78, estimate_seconds=4
    )
    assert (job_id, created, again, created_again) == (job_id, True, job_id, False)
    assert storage.live_snapshots()[0]["issue_key"] == "PAY-1"

    claimed = storage.claim_next_report_job()
    assert claimed["id"] == job_id
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE scrum_agent.report_jobs SET status = 'running' WHERE id = %s", (job_id,)
        )
    assert storage.claim_next_report_job()["id"] == job_id  # orphan requeued

    storage.finish_report_job(job_id, status="done", report={"scope": {"total": 3}})
    row = storage.get_report_job(job_id)
    assert row["status"] == "done"
    assert row["report"] == {"scope": {"total": 3}}  # jsonb round-trips as dict
    assert storage.claim_next_report_job() is None

    conn.close()
