"""Collection freshness and credential-expiry alarms (Week 4).

A missing or stale collection must surface as an explicit alarm, never as
silent gaps in history; the same applies to the Jira token, whose expiry date
Jira cannot report (ADR-0001) and which is therefore configured instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

EXPIRY_WARN_DAYS = 7
# ponytail: fixed thresholds matching an hourly-to-daily collection cadence;
# revisit if the polling interval changes materially.
STALE_AFTER = timedelta(hours=24)
WARN_AFTER = timedelta(hours=2)


@dataclass(frozen=True)
class FreshnessReport:
    last_success_at: datetime | None
    success_age: timedelta | None
    credential_status: str  # ok | expiring | expired | unknown
    days_until_expiry: int | None
    live_issues: int
    tombstoned_issues: int
    events_total: int
    failures_since_success: int
    alarms: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_healthy(self) -> bool:
        return not self.alarms


def format_age(age: timedelta) -> str:
    """Human age like '3h 20m' or '2d 4h'."""
    total_minutes = int(age.total_seconds()) // 60
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def credential_status(expires_on: date | None, *, today: date) -> tuple[str, int | None]:
    """(status, days_until_expiry) from the configured token expiry date."""
    if expires_on is None:
        return "unknown", None
    days = (expires_on - today).days
    if days < 0:
        return "expired", days
    if days <= EXPIRY_WARN_DAYS:
        return "expiring", days
    return "ok", days


def freshness_report(
    storage: Any,
    settings: Any,
    *,
    now: datetime | None = None,
) -> FreshnessReport:
    """Derive freshness/alarms from stored run history and token config."""
    now = now if now is not None else datetime.now(UTC)
    data = storage.freshness()
    last_success = data.get("last_success_at")
    # Age is computed against the caller's clock, not the stored interval, so
    # the same thresholds hold for both the SQL view and the in-memory fake.
    age = (now - last_success) if last_success is not None else None

    status, days = credential_status(settings.token_expires_on, today=now.date())

    alarms: list[str] = []
    warnings: list[str] = []
    if last_success is None:
        alarms.append("collection has never succeeded; sprint history is missing")
    elif age is not None:
        if age > STALE_AFTER:
            alarms.append(
                f"last successful collection is {format_age(age)} old; history may have gaps"
            )
        elif age > WARN_AFTER:
            warnings.append(f"last successful collection is {format_age(age)} old")
    if status == "expired":
        alarms.append("the Jira token is expired; collection cannot succeed")
    elif status == "expiring":
        warnings.append(f"the Jira token expires in {days} day(s)")
    failures = int(data.get("failures_since_success") or 0)
    if failures:
        warnings.append(f"{failures} failed/interrupted run(s) since the last success")

    return FreshnessReport(
        last_success_at=last_success,
        success_age=age,
        credential_status=status,
        days_until_expiry=days,
        live_issues=int(data.get("live_issues") or 0),
        tombstoned_issues=int(data.get("tombstoned_issues") or 0),
        events_total=int(data.get("events_total") or 0),
        failures_since_success=failures,
        alarms=tuple(alarms),
        warnings=tuple(warnings),
    )
