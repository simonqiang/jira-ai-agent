"""Deterministic current-sprint report metrics (Week 5).

Numbers are computed in Python from the persisted collection inputs (Week 4
snapshots), never from live search samples or model output. Access is
revalidated by the caller: only issues present in the live scoped search
survive into details and totals; the rest are counted as excluded. Sprint-goal
achievement is never inferred — it stays human-confirmed or unknown (ADR-0002).
Historical commitment metrics need changelog analysis (Week 6) and are reported
as unavailable, never approximated from current state.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from scrum_agent.jira.models import BoardConfiguration, Sprint
from scrum_agent.sync.freshness import FreshnessReport, format_age

METRIC_POLICY_VERSION = "adr-0002/current-state"
_SPRINT_ID_IN_FIELD = re.compile(r"id=(\d+)")
_BLOCKED_LABEL = "blocked"


def sprint_ids_in_fields(fields: dict) -> set[int]:
    """Sprint IDs recorded in a snapshot's sprint membership field.

    Accepts both Jira shapes: the legacy greenhopper string array
    (`"sprint": ["com...Sprint@1[id=78,...]"]`) and the object array
    (`"customfield_10020": [{"id": 78, ...}]`) the pilot site returns.
    """
    ids: set[int] = set()
    for key in ("sprint", "customfield_10020"):
        raw = fields.get(key)
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if isinstance(value, str):
                ids.update(int(match) for match in _SPRINT_ID_IN_FIELD.findall(value))
            elif isinstance(value, dict) and isinstance(value.get("id"), int):
                ids.add(value["id"])
    return ids


def is_blocked(fields: dict) -> str | None:
    """The explicit blocker evidence an issue carries, or None."""
    labels = [str(label).casefold() for label in fields.get("labels") or ()]
    if _BLOCKED_LABEL in labels:
        return f"label '{_BLOCKED_LABEL}'"
    for link in fields.get("issuelinks") or ():
        if not isinstance(link, dict):
            continue
        link_type = (link.get("type") or {}).get("name") or ""
        if link_type.casefold() == "blocks" and ("outwardIssue" in link or "inwardIssue" in link):
            return "blocks-issue link"
    return None


def estimate_of(fields: dict, estimate_field_id: str | None) -> float | None:
    """The configured estimate value; missing stays unknown, never zero."""
    if not estimate_field_id:
        return None
    value = fields.get(estimate_field_id)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_report(
    *,
    snapshot_rows: list[dict],
    accessible_keys: set[str],
    config: BoardConfiguration,
    sprint: Sprint,
    site: str,
    timezone: str,
    freshness: FreshnessReport,
    cutoff_at: datetime | None = None,
) -> dict:
    """Compute the current-sprint report from persisted inputs.

    ``snapshot_rows`` are the live (non-tombstoned) snapshots; ``accessible_keys``
    is the authorized set from the live revalidation search — anything stored
    but no longer visible is excluded from details AND totals.
    """
    cutoff = cutoff_at or datetime.now(UTC)
    members = [row for row in snapshot_rows if sprint.id in sprint_ids_in_fields(row["fields"])]
    excluded = sorted(
        row["issue_key"] for row in members if row["issue_key"] not in accessible_keys
    )
    issues = sorted(
        (row for row in members if row["issue_key"] in accessible_keys),
        key=lambda row: row["issue_key"],
    )

    done_ids = set(config.done_status_ids)
    status_counts: dict[str, int] = {}
    by_type: dict[str, int] = {}
    done_count = 0
    estimate_total = 0.0
    done_estimate_total = 0.0
    with_estimate = 0
    missing_estimate: list[str] = []
    unassigned_not_done: list[str] = []
    blockers: list[dict] = []
    issue_rows: list[dict] = []

    for row in issues:
        fields = row["fields"] or {}
        status_name = row["status"] or "Unknown"
        status_counts[status_name] = status_counts.get(status_name, 0) + 1
        issue_type = row["issue_type"] or "Unknown"
        by_type[issue_type] = by_type.get(issue_type, 0) + 1
        status_id = str((fields.get("status") or {}).get("id") or "")
        is_done = status_id in done_ids
        if is_done:
            done_count += 1
        elif not row["assignee"]:
            unassigned_not_done.append(row["issue_key"])

        estimate = estimate_of(fields, config.estimate_field_id)
        if estimate is None:
            missing_estimate.append(row["issue_key"])
        else:
            with_estimate += 1
            estimate_total += estimate
            if is_done:
                done_estimate_total += estimate

        evidence = is_blocked(fields)
        if evidence and not is_done:
            blockers.append({"key": row["issue_key"], "evidence": evidence})

        issue_rows.append(
            {
                "key": row["issue_key"],
                "summary": row["summary"] or "",
                "issue_type": issue_type,
                "status": status_name,
                "done": is_done,
                "assignee": row["assignee"],
                "estimate": estimate,
                "blocked_evidence": evidence,
            }
        )

    total = len(issue_rows)
    notes: list[str] = []
    if excluded:
        notes.append(
            f"{len(excluded)} stored issue(s) no longer authorized/visible were "
            f"excluded: {', '.join(excluded)}"
        )
    for alarm in freshness.alarms:
        notes.append(f"freshness alarm: {alarm}")
    for warning in freshness.warnings:
        notes.append(f"freshness warning: {warning}")
    completeness = "partial" if notes else "complete"

    report: dict[str, Any] = {
        "kind": "sprint_report",
        "site": site,
        "board_id": config.id,
        "sprint": {
            "id": sprint.id,
            "name": sprint.name,
            "state": sprint.state,
            "goal": sprint.goal,
            "start_date": sprint.start_date,
            "end_date": sprint.end_date,
            "complete_date": sprint.complete_date,
        },
        "timezone": timezone,
        "cutoff_at": cutoff.isoformat(timespec="seconds"),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "metric_policy_version": METRIC_POLICY_VERSION,
        "scope": {"total": total, "by_type": dict(sorted(by_type.items()))},
        "status_counts": dict(sorted(status_counts.items())),
        "done": {
            "count": done_count,
            "not_done_count": total - done_count,
            "done_status_ids": sorted(done_ids),
        },
        "estimates": {
            "field": config.estimate_field_id,
            "field_name": config.estimate_field_name,
            "with_estimate": with_estimate,
            "missing_estimate": len(missing_estimate),
            "missing_estimate_keys": missing_estimate,
            "total": estimate_total,
            "done_total": done_estimate_total,
            "not_done_total": estimate_total - done_estimate_total,
        },
        "blockers": blockers,
        "attention": {
            "unassigned_not_done": sorted(unassigned_not_done),
            "missing_estimate": missing_estimate,
        },
        "excluded": {"count": len(excluded), "keys": excluded},
        "freshness": {
            "last_success_at": (
                freshness.last_success_at.isoformat(timespec="seconds")
                if freshness.last_success_at
                else None
            ),
            "success_age": format_age(freshness.success_age) if freshness.success_age else None,
            "alarms": list(freshness.alarms),
            "warnings": list(freshness.warnings),
        },
        "completeness": completeness,
        "completeness_notes": notes,
        "issues": issue_rows,
    }
    report["narrative"] = build_narrative(report)
    return report


def build_narrative(report: dict) -> str:
    """Short evidence-backed summary; every claim traces to computed fields."""
    sprint = report["sprint"]
    scope = report["scope"]
    done = report["done"]
    estimates = report["estimates"]
    lines: list[str] = []

    goal = sprint.get("goal") or "not set in Jira"
    lines.append(
        f"Sprint {sprint['name']} (sprint {sprint['id']}, {sprint['state']}): {total_line(report)}"
    )
    lines.append(f"Sprint Goal: {goal} — achievement: unknown (human-confirmed, never inferred).")
    lines.append(
        f"Current scope: {scope['total']} issue(s) "
        f"({', '.join(f'{name} {count}' for name, count in scope['by_type'].items()) or 'none'}); "
        f"{done['count']} in a done column, {done['not_done_count']} not done."
    )
    field_name = estimates["field_name"] or estimates["field"] or "the estimate field"
    lines.append(
        f"Estimates ({field_name}): {estimates['total']:g} point(s) across "
        f"{estimates['with_estimate']} issue(s); done {estimates['done_total']:g}, "
        f"not done {estimates['not_done_total']:g}; "
        f"{estimates['missing_estimate']} without an estimate (unknown, not zero)."
    )
    if report["blockers"]:
        listed = "; ".join(f"{item['key']} ({item['evidence']})" for item in report["blockers"])
        lines.append(f"Blockers (explicit only): {listed}.")
    else:
        lines.append("Blockers (explicit only): none flagged by label or blocks-issue link.")
    decisions = decision_line(report)
    if decisions:
        lines.append(f"Decisions needed: {decisions}")
    lines.append(
        f"Completeness: {report['completeness']}"
        + (f" — {'; '.join(report['completeness_notes'])}" if report["completeness_notes"] else "")
        + "."
    )
    lines.append(
        "Commitment/scope-change history: unavailable in this report "
        "(changelog analysis arrives with Week 6); never inferred from current state."
    )
    return "\n".join(lines)


def total_line(report: dict) -> str:
    counts = ", ".join(f"{status}: {count}" for status, count in report["status_counts"].items())
    return f"status {counts or 'no issues'}"


def decision_line(report: dict) -> str:
    """Deterministic decision prompts, derived only from computed gaps."""
    prompts: list[str] = []
    attention = report["attention"]
    if attention["unassigned_not_done"]:
        prompts.append(
            "assign " + ", ".join(attention["unassigned_not_done"]) + " (unfinished, unassigned)"
        )
    if attention["missing_estimate"]:
        prompts.append(
            "estimate " + ", ".join(attention["missing_estimate"]) + " (no estimate recorded)"
        )
    return "; ".join(prompts)
