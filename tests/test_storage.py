"""Storage-layer tests: migration contract plus (gated) real-database checks.

The integration tests need a disposable Postgres: set
``SCRUM_AGENT_TEST_DATABASE_URL`` (the database is schema-reset by the test).
They are skipped by default, so plain ``pytest`` requires no database — and
never touches live Jira.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
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


def test_ticket_approvals_migration_matches_schema_contract() -> None:
    sql = (
        resources.files("scrum_agent")
        .joinpath("migrations")
        .joinpath("003_ticket_approvals.sql")
        .read_text()
    )
    for table in (
        "scrum_agent.ticket_drafts",
        "scrum_agent.ticket_approvals",
        "scrum_agent.ticket_executions",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    # Payloads are frozen objects; the marker and the approval pin replay safety.
    assert "CHECK (jsonb_typeof(payload) = 'object')" in sql
    assert "correlation_marker text NOT NULL UNIQUE" in sql
    assert "CHECK (expires_at > approved_at)" in sql
    assert "UNIQUE REFERENCES scrum_agent.ticket_approvals" in sql
    assert "CHECK (status IN ('executing', 'succeeded', 'outcome_unknown'))" in sql


@requires_db
def test_ticket_approval_records_round_trip() -> None:
    import psycopg
    from psycopg.rows import dict_row

    from scrum_agent.storage.db import run_migrations
    from scrum_agent.storage.repository import PgStorage

    conn = psycopg.connect(
        _TEST_DSN, row_factory=dict_row, options="-c timezone=UTC", autocommit=True
    )
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS scrum_agent CASCADE")
        cur.execute("DROP TABLE IF EXISTS public.schema_migrations")
    assert "003_ticket_approvals.sql" in run_migrations(conn)

    storage = PgStorage(conn)
    moment = datetime(2026, 10, 1, tzinfo=UTC)
    draft_id = storage.create_draft(
        creator="local-pilot",
        issue_type="Bug",
        template_version="2026-10",
        payload={"summary": "S"},
        payload_hash="hash-1",
        correlation_marker="scrum-agent-req-m1",
        created_at=moment,
    )
    draft = storage.get_draft(draft_id)
    assert draft["payload"] == {"summary": "S"}  # jsonb round-trips as a dict
    assert draft["payload_hash"] == "hash-1"

    approval_id = storage.create_approval(
        draft_id=draft_id,
        approver="local-pilot",
        payload_hash="hash-1",
        approved_at=moment,
        expires_at=moment + timedelta(minutes=15),
    )
    assert storage.get_approval(approval_id)["payload_hash"] == "hash-1"

    execution_id = storage.create_execution(
        approval_id=approval_id,
        payload_hash="hash-1",
        correlation_marker="scrum-agent-req-m1",
        status="executing",
        started_at=moment,
    )
    assert storage.get_execution_by_approval(approval_id)["status"] == "executing"
    storage.update_execution(
        execution_id, status="succeeded", issue_key="PAY-9", reconciled=True, finished_at=moment
    )
    done = storage.get_execution_by_approval(approval_id)
    assert (done["status"], done["issue_key"], done["reconciled"]) == ("succeeded", "PAY-9", True)

    # One execution per approval: a replay cannot open a second create attempt.
    with pytest.raises(psycopg.errors.UniqueViolation):
        storage.create_execution(
            approval_id=approval_id,
            payload_hash="hash-1",
            correlation_marker="scrum-agent-req-m1",
            status="executing",
            started_at=moment,
        )


def test_ticket_updates_migration_matches_schema_contract() -> None:
    sql = (
        resources.files("scrum_agent")
        .joinpath("migrations")
        .joinpath("004_ticket_updates.sql")
        .read_text()
    )
    for table in (
        "scrum_agent.ticket_update_proposals",
        "scrum_agent.ticket_update_approvals",
        "scrum_agent.ticket_update_executions",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "CHECK (jsonb_typeof(base) = 'object')" in sql
    assert "CHECK (expires_at > approved_at)" in sql
    assert "UNIQUE REFERENCES scrum_agent.ticket_update_approvals" in sql
    assert "rejected_stale" in sql and "verification_failed" in sql


@requires_db
def test_ticket_update_records_round_trip() -> None:
    import psycopg
    from psycopg.rows import dict_row

    from scrum_agent.storage.db import run_migrations
    from scrum_agent.storage.repository import PgStorage

    conn = psycopg.connect(
        _TEST_DSN, row_factory=dict_row, options="-c timezone=UTC", autocommit=True
    )
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS scrum_agent CASCADE")
        cur.execute("DROP TABLE IF EXISTS public.schema_migrations")
    assert "004_ticket_updates.sql" in run_migrations(conn)

    storage = PgStorage(conn)
    moment = datetime(2026, 10, 2, tzinfo=UTC)
    proposal_id = storage.create_update_proposal(
        creator="local-pilot",
        issue_key="PAY-3",
        base={"acceptance_criteria": None},
        changes={"acceptance_criteria": "Given, When, Then."},
        payload_hash="hash-9",
        created_at=moment,
    )
    proposal = storage.get_update_proposal(proposal_id)
    assert proposal["base"] == {"acceptance_criteria": None}  # jsonb round-trips as a dict
    assert proposal["changes"]["acceptance_criteria"].startswith("Given")

    approval_id = storage.create_update_approval(
        proposal_id=proposal_id,
        approver="local-pilot",
        payload_hash="hash-9",
        approved_at=moment,
        expires_at=moment + timedelta(minutes=15),
    )
    assert storage.get_update_approval(approval_id)["payload_hash"] == "hash-9"

    execution_id = storage.create_update_execution(
        approval_id=approval_id,
        payload_hash="hash-9",
        issue_key="PAY-3",
        status="executing",
        requested={"acceptance_criteria": "Given, When, Then."},
        started_at=moment,
    )
    assert storage.get_update_execution_by_approval(approval_id)["status"] == "executing"
    storage.finish_update_execution(
        execution_id,
        status="succeeded",
        verified={"acceptance_criteria": {"match": True}},
        finished_at=moment,
    )
    done = storage.get_update_execution_by_approval(approval_id)
    assert done["status"] == "succeeded"
    assert done["verified"]["acceptance_criteria"]["match"] is True

    # One execution per approval: a stale diff can never re-execute later.
    with pytest.raises(psycopg.errors.UniqueViolation):
        storage.create_update_execution(
            approval_id=approval_id,
            payload_hash="hash-9",
            issue_key="PAY-3",
            status="executing",
            requested={},
            started_at=moment,
        )


def test_retrieval_migration_matches_schema_contract() -> None:
    sql = (
        resources.files("scrum_agent")
        .joinpath("migrations")
        .joinpath("005_retrieval.sql")
        .read_text()
    )
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE TABLE IF NOT EXISTS scrum_agent.issue_chunks" in sql
    # Source revision, content hash and embedding provenance per chunk (spec §7).
    for column in (
        "source_updated",
        "content_hash",
        "source_url",
        "embedding_model",
        "content_tsv",
    ):
        assert column in sql
    # One row per (issue, kind, position); replacements never duplicate.
    assert "UNIQUE (issue_id, chunk_kind, chunk_index)" in sql
    # No approximate index until a benchmark justifies one (spec §7).
    assert "ivfflat" not in sql and "hnsw" not in sql


@requires_db
def test_retrieval_chunks_round_trip() -> None:
    import psycopg
    from psycopg.rows import dict_row

    from scrum_agent.storage.db import run_migrations
    from scrum_agent.storage.repository import PgStorage, _vector_literal

    conn = psycopg.connect(
        _TEST_DSN, row_factory=dict_row, options="-c timezone=UTC", autocommit=True
    )
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS scrum_agent CASCADE")
        cur.execute("DROP TABLE IF EXISTS public.schema_migrations")
    assert "005_retrieval.sql" in run_migrations(conn)

    storage = PgStorage(conn)
    run_id = storage.start_run()
    moment = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
    for key, summary in (("PAY-1", "Payment retry fails silently"), ("PAY-2", "Refund missing")):
        storage.upsert_snapshot(
            issue_id=f"100{key}",
            issue_key=key,
            summary=summary,
            status="Open",
            issue_type="Bug",
            assignee=None,
            updated=moment,
            fields={},
            run_id=run_id,
        )

    def chunk_rows(issue_key: str, vectors: list[list[float]]) -> list[tuple]:
        return [
            ("summary", index, None, f"{issue_key} summary", f"hash-{issue_key}-{index}", vector)
            for index, vector in enumerate(vectors)
        ]

    storage.replace_chunks(
        issue_id="100PAY-1",
        issue_key="PAY-1",
        site="test.atlassian.net",
        project_key="PAY",
        source_updated=moment,
        rows=chunk_rows("PAY-1", [[1.0, 0.0, 0.0]]),
        embedding_model="test-model",
    )
    storage.replace_chunks(
        issue_id="100PAY-2",
        issue_key="PAY-2",
        site="test.atlassian.net",
        project_key="PAY",
        source_updated=moment,
        rows=chunk_rows("PAY-2", [[0.0, 1.0, 0.0]]),
        embedding_model="test-model",
    )
    signatures = storage.chunk_signatures()
    assert signatures == {
        "100PAY-1": (moment, "test-model"),
        "100PAY-2": (moment, "test-model"),
    }

    # Vector ranking: the query nearest PAY-1's vector finds PAY-1 first, and
    # each issue appears at most once even with several chunks.
    storage.replace_chunks(
        issue_id="100PAY-1",
        issue_key="PAY-1",
        site="test.atlassian.net",
        project_key="PAY",
        source_updated=moment,
        rows=chunk_rows("PAY-1", [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]]),
        embedding_model="test-model",
    )
    rows = storage.hybrid_search(
        query="retry summary",
        query_embedding=[0.95, 0.05, 0.0],
        embedding_model="test-model",
        limit=5,
    )
    assert [row["issue_key"] for row in rows] == ["PAY-1", "PAY-2"]  # fused order, deduped
    # similarity is the raw cosine of the issue's best chunk ([0.9, 0.1, 0.0]).
    assert rows[0]["similarity"] == pytest.approx(0.9984, abs=1e-3)
    assert rows[0]["source_url"] == "https://test.atlassian.net/browse/PAY-1"

    # A different model's vectors are never mixed into the results.
    assert (
        storage.hybrid_search(
            query="retry summary",
            query_embedding=[1.0, 0.0, 0.0],
            embedding_model="other-model",
            limit=5,
        )
        == []
    )

    # Failed rechecks and tombstones invalidate chunks.
    storage.delete_chunks(issue_key="PAY-1")
    assert set(storage.chunk_signatures()) == {"100PAY-2"}
    storage.tombstone_issue("PAY-2")
    storage.prune_orphan_chunks()
    assert storage.chunk_signatures() == {}
    assert _vector_literal([1.0]) == "[1.0]"
