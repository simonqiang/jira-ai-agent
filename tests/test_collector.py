"""Collector behavior tests against the fixture Jira and in-memory storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scrum_agent.jira.client import JiraClient
from scrum_agent.jira.errors import JiraAuthError
from scrum_agent.sync.collector import CollectorService
from tests.checked_queries import FakeJira
from tests.conftest import make_settings
from tests.storage_fakes import InMemoryStorage

_START = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def status_item(frm: str, to: str) -> dict:
    return {
        "field": "status",
        "fieldId": "status",
        "from": frm,
        "fromString": frm,
        "to": to,
        "toString": to,
    }


def entry(entry_id: str, created: str, items: list[dict], author: str = "A. Developer") -> dict:
    return {
        "id": entry_id,
        "created": created,
        "author": {"displayName": author},
        "items": items,
    }


def make_collector(
    jira: FakeJira | None = None, clock: FakeClock | None = None
) -> tuple[CollectorService, FakeJira, InMemoryStorage, FakeClock]:
    jira = jira if jira is not None else FakeJira()
    clock = clock if clock is not None else FakeClock()
    client = JiraClient(make_settings(), transport=httpx.MockTransport(jira.handler))
    storage = InMemoryStorage(now=clock)
    sleeps: list[float] = []
    collector = CollectorService(client, storage, now=clock, sleep=sleeps.append)
    return collector, jira, storage, clock


def test_full_ingest_writes_snapshots_events_and_board_config() -> None:
    collector, jira, storage, _ = make_collector()
    jira.changelogs["PAY-1"] = [
        entry("9001", "2026-09-20T10:30:00.000+0000", [status_item("To Do", "In Progress")])
    ]

    summary = collector.run()

    assert summary["status"] == "success"
    assert summary["issues_seen"] == 6
    assert summary["events_seen"] == 1
    assert len(storage.snapshots) == 6
    pay1 = next(s for s in storage.snapshots.values() if s["issue_key"] == "PAY-1")
    assert pay1["status"] == "In Progress"
    assert pay1["issue_type"] == "Bug"
    assert pay1["assignee"] == "A. Developer"
    assert len(storage.board_configs) == 1
    assert storage.checkpoints["issues"]["last_success_at"] == _START
    # One search page plus one complete changelog page for each reconciled issue.
    assert storage.runs[-1]["pages_fetched"] == 7


def test_pages_fetched_counts_each_paginated_changelog_response() -> None:
    collector, jira, storage, _ = make_collector()
    jira.changelogs["PAY-1"] = [
        entry(str(9000 + index), "2026-09-20T10:30:00.000+0000", [status_item("To Do", "Done")])
        for index in range(101)
    ]

    collector.run()

    # One JQL page, two changelog pages for PAY-1, and one page for each of
    # the remaining five issue changelogs.
    assert storage.runs[-1]["pages_fetched"] == 8


def test_event_rows_carry_field_provenance() -> None:
    collector, jira, storage, _ = make_collector()
    jira.changelogs["PAY-2"] = [
        entry(
            "9002",
            "2026-09-21T09:00:00.000+0000",
            [status_item("In Progress", "Done")],
            author="S. Tester",
        )
    ]

    collector.run()

    (event,) = storage.events.values()
    assert event["field"] == "status"
    assert event["from_value"] == "In Progress"
    assert event["to_value"] == "Done"
    assert event["author"] == "S. Tester"
    assert event["occurred_at"].isoformat() == "2026-09-21T09:00:00+00:00"


def test_replay_run_duplicates_nothing() -> None:
    collector, jira, storage, clock = make_collector()
    jira.changelogs["PAY-1"] = [
        entry("9001", "2026-09-20T10:30:00.000+0000", [status_item("To Do", "In Progress")]),
        entry(
            "9003",
            "2026-09-22T11:00:00.000+0000",
            [{"field": "Story Points", "fieldId": "customfield_10002", "to": "5", "toString": "5"}],
        ),
    ]

    first = collector.run()
    clock.advance(hours=2)
    second = collector.run()

    assert first["events_seen"] == 2
    assert second["events_seen"] == 0
    assert len(storage.events) == 2
    assert len(storage.snapshots) == 6


def test_delta_poll_uses_overlapped_checkpoint_window() -> None:
    collector, jira, storage, clock = make_collector()

    collector.run()
    cold_jql = jira.search_calls[0]["jql"]
    assert 'updated >= "2000-01-01"' in cold_jql

    clock.advance(hours=3)
    collector.run()
    delta_jql = jira.search_calls[-1]["jql"]
    # Run 1 succeeded at 09:00; the window overlaps back to 08:00.
    assert 'updated >= "2026-09-28 08:00"' in delta_jql


def test_estimate_field_requested_from_board_config() -> None:
    collector, jira, _, _ = make_collector()
    collector.run()
    assert any("customfield_10002" in call.get("fields", "") for call in jira.detail_calls)


@pytest.mark.parametrize("status", [404, 403])
def test_inaccessible_issue_is_tombstoned_not_failed(status: int) -> None:
    collector, jira, storage, clock = make_collector()
    collector.run()
    assert all(s["deleted_at"] is None for s in storage.snapshots.values())

    jira.detail_overrides["PAY-1"] = status
    clock.advance(hours=2)
    summary = collector.run()

    assert summary["status"] == "success"
    tombstoned = [s for s in storage.snapshots.values() if s["issue_key"] == "PAY-1"]
    assert tombstoned[0]["deleted_at"] is not None
    live = [s for s in storage.snapshots.values() if s["issue_key"] != "PAY-1"]
    assert all(s["deleted_at"] is None for s in live)


def test_auth_failure_aborts_run_without_mass_tombstoning() -> None:
    collector, jira, storage, clock = make_collector()
    collector.run()

    for key in ("PAY-1", "PAY-2", "PAY-3"):
        jira.detail_overrides[key] = 401
    clock.advance(hours=2)
    with pytest.raises(JiraAuthError):
        collector.run()

    failed = storage.runs[-1]
    assert failed["status"] == "error"
    assert failed["error"]
    assert all(s["deleted_at"] is None for s in storage.snapshots.values())
    # The previous checkpoint survives the failed run.
    assert storage.checkpoints["issues"]["last_success_at"] == _START


def test_stale_running_row_is_marked_interrupted() -> None:
    collector, _, storage, _ = make_collector()
    stale_id = storage.start_run()  # simulate a crashed previous process

    collector.run()

    assert storage.runs[stale_id - 1]["status"] == "interrupted"
    assert storage.runs[stale_id - 1]["error"]


def test_board_config_versioned_only_on_change() -> None:
    collector, jira, storage, clock = make_collector()
    original = dict(jira.board_config)
    changed = {**original, "name": "Renamed Board"}

    collector.run()
    clock.advance(hours=1)
    collector.run()  # unchanged: no new version
    assert len(storage.board_configs) == 1

    jira.board_config = changed
    clock.advance(hours=1)
    collector.run()
    assert len(storage.board_configs) == 2

    jira.board_config = original  # A->B->A is still a change
    clock.advance(hours=1)
    collector.run()
    assert len(storage.board_configs) == 3


def test_rate_limit_is_retried_with_retry_after() -> None:
    jira = FakeJira()
    inner = jira.handler
    attempts = {"count": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/issue/PAY-1") and not path.endswith("/changelog"):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(429, json={}, headers={"Retry-After": "0"})
        return inner(request)

    jira.handler = flaky
    collector, _, storage, _ = make_collector(jira)

    summary = collector.run()

    assert summary["status"] == "success"
    assert attempts["count"] == 2
    assert storage.freshness()["live_issues"] == 6


def test_rate_limit_during_changelog_iteration_is_retried() -> None:
    jira = FakeJira()
    inner = jira.handler
    attempts = {"count": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issue/PAY-1/changelog"):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(429, json={}, headers={"Retry-After": "0"})
        return inner(request)

    jira.handler = flaky
    collector, _, storage, _ = make_collector(jira)

    summary = collector.run()

    assert summary["status"] == "success"
    assert attempts["count"] == 2
    assert storage.runs[-1]["pages_fetched"] == 7
