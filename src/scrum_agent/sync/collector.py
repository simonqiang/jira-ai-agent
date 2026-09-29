"""Idempotent sprint-history collector (Week 4).

Each run polls the configured project for issues updated since the last
successful collection (minus an overlap window), then reconciles every hinted
issue against an authoritative per-issue read and its full changelog: snapshots
are upserted, events are dedup-keyed on (issue, changelog entry, item), and the
board configuration is versioned when it changes. Polling results are hints
only — JQL ordering and Jira indexing lag are never trusted, and the overlap
window plus per-issue reconciliation absorb both.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from scrum_agent.jira.client import JiraClient
from scrum_agent.jira.errors import JiraNotFoundError, JiraPermissionError, JiraRateLimitedError
from scrum_agent.jira.models import BoardConfiguration, parse_jira_time
from scrum_agent.storage.repository import canonical_config_hash, entry_rows

T = TypeVar("T")

_COLD_START_JQL = 'updated >= "2000-01-01"'
# ponytail: fixed 1h overlap; raise it if polling ever spaces out beyond ~1h.
_OVERLAP = timedelta(hours=1)
_MAX_RETRIES = 3
_DEFAULT_RETRY_SECONDS = 30.0
# ponytail: cap Retry-After waits so a scheduled run cannot stall all day; a
# run that still hits 429 fails cleanly and the next run resumes it.
_RETRY_CAP_SECONDS = 60.0


class CollectorService:
    """Collects the pilot project's issue history into local storage."""

    def __init__(
        self,
        client: JiraClient,
        storage: Any,
        *,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._storage = storage
        self._now = now if now is not None else (lambda: datetime.now(UTC))
        self._sleep = sleep

    def run(self, *, trigger: str = "manual") -> dict:
        """Collect one cycle; returns a summary of what was stored."""
        run_id = self._storage.start_run(trigger)
        issues_seen = 0
        events_seen = 0
        try:
            issues_seen, events_seen = self._collect(run_id)
            self._storage.set_checkpoint("issues", self._now(), run_id)
        except Exception as error:
            self._storage.finish_run(run_id, status="error", error=str(error))
            raise
        self._storage.finish_run(
            run_id, status="success", issues_seen=issues_seen, events_seen=events_seen
        )
        return {
            "run_id": run_id,
            "status": "success",
            "issues_seen": issues_seen,
            "events_seen": events_seen,
        }

    # -- internals -----------------------------------------------------------

    def _collect(self, run_id: int) -> tuple[int, int]:
        board_id = self._client.scope.board_id
        config = self._with_retry(lambda: self._client.get_board_configuration(board_id))
        self._record_board_config(run_id, config)
        estimate_field_id = config.estimate_field_id

        issues_seen = 0
        events_seen = 0
        for key in self._hinted_issue_keys():
            try:
                detail = self._with_retry(
                    lambda key=key: self._client.get_issue_detail(
                        key, extra_fields=(estimate_field_id,) if estimate_field_id else ()
                    )
                )
            except (JiraNotFoundError, JiraPermissionError):
                # The issue is gone or no longer readable: a tombstoned gap, not
                # a failed run. Auth failures (401) still abort the run.
                self._storage.tombstone_issue(key)
                continue
            fields = detail.get("fields") or {}
            issue_id = str(detail["id"])
            self._storage.upsert_snapshot(
                issue_id=issue_id,
                issue_key=detail["key"],
                summary=fields.get("summary") or "",
                status=(fields.get("status") or {}).get("name") or "Unknown",
                issue_type=(fields.get("issuetype") or {}).get("name") or "Unknown",
                assignee=(fields.get("assignee") or {}).get("displayName"),
                updated=parse_jira_time(fields["updated"]) if fields.get("updated") else None,
                fields=fields,
                run_id=run_id,
            )
            issues_seen += 1

            entries = list(self._with_retry(lambda key=key: self._client.iter_issue_changelog(key)))
            events_seen += self._storage.insert_events(issue_id, entry_rows(entries), run_id)
        return issues_seen, events_seen

    def _record_board_config(self, run_id: int, config: BoardConfiguration) -> None:
        """Version the board configuration; append only on an actual change."""
        config_payload = config.model_dump(mode="json")
        config_hash = canonical_config_hash(config_payload)
        latest = self._storage.latest_board_config(config.id)
        if latest is not None and latest["config_hash"] == config_hash:
            return
        self._storage.insert_board_config(
            board_id=config.id, config_hash=config_hash, config=config_payload, run_id=run_id
        )

    def _hinted_issue_keys(self) -> list[str]:
        """Issue keys Jira reports as updated since the overlapped checkpoint."""
        last_success = self._storage.last_success()
        if last_success is None:
            jql = _COLD_START_JQL
        else:
            since = last_success.astimezone(UTC) - _OVERLAP
            # JQL datetimes use "YYYY-MM-DD HH:MM"; clock skew is absorbed by
            # the overlap window rather than trusting Jira's indexing order.
            jql = f'updated >= "{since:%Y-%m-%d %H:%M}"'
        keys: list[str] = []
        seen: set[str] = set()
        for issue in self._client.iter_search_jql(jql, max_pages=100):
            if issue.key not in seen:
                seen.add(issue.key)
                keys.append(issue.key)
        return keys

    def _with_retry(self, call: Callable[[], T]) -> T:
        """Honor Retry-After with bounded sleeps; other errors propagate."""
        for attempt in range(_MAX_RETRIES):
            try:
                return call()
            except JiraRateLimitedError as error:
                if attempt == _MAX_RETRIES - 1:
                    raise
                wait = error.retry_after
                delay = _DEFAULT_RETRY_SECONDS if wait is None else min(wait, _RETRY_CAP_SECONDS)
                self._sleep(delay)
        raise AssertionError("unreachable")
