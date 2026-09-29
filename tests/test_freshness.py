"""Freshness and credential-alarm tests (Week 4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from scrum_agent.sync.freshness import (
    STALE_AFTER,
    WARN_AFTER,
    credential_status,
    format_age,
    freshness_report,
)
from tests.conftest import make_settings
from tests.storage_fakes import InMemoryStorage

_NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def test_credential_status_math() -> None:
    today = date(2026, 9, 30)
    assert credential_status(None, today=today) == ("unknown", None)
    assert credential_status(date(2026, 10, 30), today=today) == ("ok", 30)
    assert credential_status(date(2026, 10, 3), today=today)[0] == "expiring"
    assert credential_status(today, today=today)[0] == "expiring"
    assert credential_status(date(2026, 9, 29), today=today) == ("expired", -1)


def test_format_age() -> None:
    assert format_age(timedelta(minutes=5)) == "5m"
    assert format_age(timedelta(hours=3, minutes=20)) == "3h 20m"
    assert format_age(timedelta(days=2, hours=4)) == "2d 4h"


def test_never_collected_is_an_alarm() -> None:
    report = freshness_report(InMemoryStorage(), make_settings(), now=_NOW)
    assert report.alarms == ("collection has never succeeded; sprint history is missing",)
    assert not report.is_healthy


class StepClock:
    """Clock that advances one step per call so run stamps strictly increase."""

    def __init__(self, start: datetime, step: timedelta = timedelta(minutes=1)) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> datetime:
        current = self.now
        self.now += self.step
        return current


def test_fresh_collection_has_no_alarms_or_warnings() -> None:
    storage = InMemoryStorage(now=lambda: _NOW)
    run_id = storage.start_run()
    storage.finish_run(run_id, status="success")
    report = freshness_report(storage, make_settings(), now=_NOW)
    assert report.alarms == ()
    assert report.warnings == ()
    assert report.is_healthy
    assert report.last_success_at == _NOW


def test_old_collection_warns_then_alarms() -> None:
    storage = InMemoryStorage(now=StepClock(_NOW))
    run_id = storage.start_run()
    storage.finish_run(run_id, status="success")

    warn_now = _NOW + WARN_AFTER + timedelta(minutes=2)  # success stamped at +1m
    report = freshness_report(storage, make_settings(), now=warn_now)
    assert report.alarms == ()
    assert any("2h 1m" in warning for warning in report.warnings)

    stale_now = _NOW + STALE_AFTER + timedelta(minutes=2)  # success stamped at +1m
    report = freshness_report(storage, make_settings(), now=stale_now)
    assert any("gaps" in alarm for alarm in report.alarms)
    assert not report.is_healthy


def test_expiring_and_expired_tokens_surface() -> None:
    storage = InMemoryStorage(now=StepClock(_NOW))
    run_id = storage.start_run()
    storage.finish_run(run_id, status="success")

    expiring = make_settings(token_expires_on="2026-10-05")
    report = freshness_report(storage, expiring, now=_NOW)
    assert report.credential_status == "expiring"
    assert report.days_until_expiry == 5
    assert report.alarms == ()
    assert any("expires in 5 day(s)" in warning for warning in report.warnings)

    expired = make_settings(token_expires_on="2026-09-29")
    report = freshness_report(storage, expired, now=_NOW)
    assert report.credential_status == "expired"
    assert any("expired" in alarm for alarm in report.alarms)
    assert not report.is_healthy


def test_failures_since_success_are_visible() -> None:
    storage = InMemoryStorage(now=StepClock(_NOW))
    run_id = storage.start_run()
    storage.finish_run(run_id, status="success")
    failed = storage.start_run()
    storage.finish_run(failed, status="error", error="boom")
    interrupted = storage.start_run()

    report = freshness_report(storage, make_settings(), now=_NOW + timedelta(minutes=10))
    # The errored run is a failure; the still-running run is in flight, not failed.
    assert report.failures_since_success == 1
    assert any("1 failed/interrupted" in warning for warning in report.warnings)
    assert storage.runs[interrupted - 1]["status"] == "running"
