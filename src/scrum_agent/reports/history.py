"""Evidence-backed historical sprint metrics (Week 6).

Changelog events, rather than today's issue snapshot, establish commitment,
scope change, status transitions, estimates, and rollover.  A snapshot is
used only to describe an already evidenced issue at the cutoff; it never
backfills a missing historical fact.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from scrum_agent.jira.models import BoardConfiguration, Sprint

HISTORICAL_POLICY_VERSION = "adr-0002/changelog-v1"
_SPRINT_ID = re.compile(r"(?:id=|(?:^|[,\s]))(\d+)(?=[,\]\s]|$)")


def _at(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else None


def _sprint_ids(value: str | None) -> set[int]:
    return {int(match) for match in _SPRINT_ID.findall(value or "")}


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _is_membership(event: dict, sprint_id: int) -> bool:
    return event["field"].casefold() == "sprint" and (
        sprint_id in _sprint_ids(event.get("from_id") or event.get("from_value"))
        or sprint_id in _sprint_ids(event.get("to_id") or event.get("to_value"))
    )


def _is_estimate(event: dict, config: BoardConfiguration) -> bool:
    return bool(
        config.estimate_field_id
        and (
            event.get("field_id") == config.estimate_field_id
            or event["field"] == config.estimate_field_name
        )
    )


def _event_key(event: dict) -> tuple[datetime, str, int]:
    return event["occurred_at"], str(event["changelog_id"]), event["item_index"]


def _state_at(events: list[dict], boundary: datetime, initial: Any = None) -> Any:
    value = initial
    for event in events:
        if event["occurred_at"] > boundary:
            break
        value = event.get("to_id") or event.get("to_value")
    return value


def build_historical_metrics(
    *,
    snapshot_rows: list[dict],
    event_rows: list[dict],
    config: BoardConfiguration,
    sprint: Sprint,
    cutoff_at: datetime,
) -> dict:
    """Return historical metrics or a precise unavailable/partial explanation.

    Jira does not guarantee a distinct total order for changelog entries sharing
    a timestamp.  Such entries are retained as evidence but affected metrics are
    provisional instead of pretending their arbitrary API order is authoritative.
    """
    start, closed_at = _at(sprint.start_date), _at(sprint.complete_date)
    if start is None or closed_at is None:
        return {
            "status": "unavailable",
            "policy_version": HISTORICAL_POLICY_VERSION,
            "missing_evidence": ["closed sprint startDate or completeDate is missing"],
        }
    if closed_at < start:
        return {
            "status": "unavailable",
            "policy_version": HISTORICAL_POLICY_VERSION,
            "missing_evidence": ["sprint completeDate precedes startDate"],
        }
    cutoff = min(cutoff_at, closed_at)
    snapshots = {row["issue_id"]: row for row in snapshot_rows}
    by_issue: dict[str, list[dict]] = {}
    for event in event_rows:
        if event["issue_id"] in snapshots:
            by_issue.setdefault(event["issue_id"], []).append(event)
    for events in by_issue.values():
        events.sort(key=_event_key)

    committed: list[dict] = []
    added: list[str] = []
    removed: list[str] = []
    missing: list[str] = []
    ambiguous: list[str] = []
    excluded_subtasks: list[str] = []
    done_ids = set(config.done_status_ids)

    for issue_id, snapshot in snapshots.items():
        fields = snapshot.get("fields") or {}
        if fields.get("parent"):
            excluded_subtasks.append(snapshot["issue_key"])
            continue
        events = by_issue.get(issue_id, [])
        memberships = [event for event in events if _is_membership(event, sprint.id)]
        if not memberships:
            continue
        relevant_events = [
            event
            for event in events
            if event["field"].casefold() in {"sprint", "status"} or _is_estimate(event, config)
        ]
        if any(
            left["occurred_at"] == right["occurred_at"]
            and left["changelog_id"] != right["changelog_id"]
            for left, right in zip(relevant_events, relevant_events[1:], strict=False)
        ):
            ambiguous.append(snapshot["issue_key"])
        present_at_start = False
        initial_seen = False
        for event in memberships:
            after = sprint.id in _sprint_ids(event.get("to_id") or event.get("to_value"))
            if event["occurred_at"] <= start:
                initial_seen = True
                present_at_start = after
        for event in memberships:
            if not start < event["occurred_at"] <= cutoff:
                continue
            before = sprint.id in _sprint_ids(event.get("from_id") or event.get("from_value"))
            after = sprint.id in _sprint_ids(event.get("to_id") or event.get("to_value"))
            if not before and after:
                added.append(snapshot["issue_key"])
            elif before and not after:
                removed.append(snapshot["issue_key"])
        if not initial_seen:
            if not any(
                sprint.id in _sprint_ids(event.get("from_id") or event.get("from_value"))
                for event in memberships
                if start < event["occurred_at"] <= cutoff
            ):
                continue  # known addition after start: not original commitment
            missing.append(f"{snapshot['issue_key']}: no membership state at sprint start")
            continue
        if not present_at_start:
            continue

        status_events = [event for event in events if event["field"].casefold() == "status"]
        estimate_events = [event for event in events if _is_estimate(event, config)]
        if not status_events:
            missing.append(f"{snapshot['issue_key']}: no status history")
            continue
        if not estimate_events:
            missing.append(f"{snapshot['issue_key']}: no start-time estimate history")
        start_status = _state_at(status_events, start)
        cutoff_status = _state_at(status_events, cutoff)
        if start_status is None or cutoff_status is None:
            missing.append(f"{snapshot['issue_key']}: status does not cover sprint boundary")
            continue
        entered_done = next(
            (
                event
                for event in status_events
                if start < event["occurred_at"] <= cutoff
                and (event.get("to_id") or event.get("to_value")) in done_ids
            ),
            None,
        )
        start_estimate = _number(_state_at(estimate_events, start))
        changes = [
            {
                "at": event["occurred_at"].isoformat(timespec="seconds"),
                "from": _number(event.get("from_id") or event.get("from_value")),
                "to": _number(event.get("to_id") or event.get("to_value")),
            }
            for event in estimate_events
            if start < event["occurred_at"] <= cutoff
        ]
        later_membership = any(
            event["occurred_at"] > cutoff
            and event["field"].casefold() == "sprint"
            and any(
                value > sprint.id
                for value in _sprint_ids(event.get("to_id") or event.get("to_value"))
            )
            for event in events
        )
        committed.append(
            {
                "key": snapshot["issue_key"],
                "start_status_id": start_status,
                "pre_closure_status_id": cutoff_status,
                "start_estimate": start_estimate,
                "estimate_changes": changes,
                "completed_during_sprint": bool(entered_done and cutoff_status in done_ids),
                "done_by_end": cutoff_status in done_ids,
                "reopened": bool(entered_done and cutoff_status not in done_ids),
                "rollover": bool(cutoff_status not in done_ids and later_membership),
            }
        )

    committed.sort(key=lambda issue: issue["key"])
    completed = [issue for issue in committed if issue["completed_during_sprint"]]
    estimated = [issue for issue in committed if issue["start_estimate"] is not None]
    completed_estimated = [issue for issue in completed if issue["start_estimate"] is not None]
    denominator = len(committed)
    point_denominator = sum(issue["start_estimate"] for issue in estimated)
    missing_start_estimates = len(estimated) != denominator
    incomplete = bool(missing or ambiguous)
    return {
        "status": "partial" if incomplete else "final",
        "policy_version": HISTORICAL_POLICY_VERSION,
        "boundary": {
            "start_at": start.isoformat(timespec="seconds"),
            "cutoff_at": cutoff.isoformat(timespec="seconds"),
        },
        "committed": {"count": denominator, "issues": committed},
        "scope_changes": {
            "added": sorted(set(added)),
            "removed": sorted(set(removed)),
        },
        "completion": {
            "completed_during_sprint": len(completed),
            "done_by_end": sum(issue["done_by_end"] for issue in committed),
            "reopened": sorted(issue["key"] for issue in committed if issue["reopened"]),
            "unfinished": sorted(issue["key"] for issue in committed if not issue["done_by_end"]),
            "rollover": sorted(issue["key"] for issue in committed if issue["rollover"]),
            "commitment_ratio": (len(completed) / denominator if denominator else None),
            "point_commitment_ratio": (
                sum(issue["start_estimate"] for issue in completed_estimated) / point_denominator
                if point_denominator and not missing_start_estimates
                else None
            ),
            "point_ratio_reason": (
                "unknown start-time estimate"
                if missing_start_estimates
                else ("no committed estimates" if not point_denominator else None)
            ),
        },
        "estimate_changes": {
            issue["key"]: issue["estimate_changes"]
            for issue in committed
            if issue["estimate_changes"]
        },
        "excluded_subtasks": sorted(excluded_subtasks),
        "missing_evidence": sorted(set(missing)),
        "ambiguous_ordering": sorted(set(ambiguous)),
        "evidence": {
            "event_count": len(event_rows),
            "event_ids": [
                {
                    "issue_id": event["issue_id"],
                    "changelog_id": event["changelog_id"],
                    "item_index": event["item_index"],
                    "occurred_at": event["occurred_at"].isoformat(timespec="seconds"),
                }
                for event in sorted(event_rows, key=_event_key)
            ],
        },
    }
