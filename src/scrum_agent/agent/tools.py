"""Narrow, read-only ADK tools over the Week 2 search service.

Four search tools (``get_issue``, ``list_sprints``, ``search_issues``,
``search_sprint``) plus the two Week 5 report tools
(``build_sprint_report``/``get_report``), which never block the conversation:
building returns a job handle and polling returns status or the finished
report. Inputs are plain JSON primitives (ADK's argument coercion swallows
``ValidationError`` for model classes, so ``IssueFilters`` is built inside each
tool); outputs are structured payloads. No tool accepts raw JQL, credentials
or any write parameter, and no tool can widen scope: every call delegates to
``SearchService``/``ReportJobs``, which enforce ``PilotScope`` end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime

from google.adk.tools import FunctionTool

from scrum_agent.agent.payloads import (
    error_payload,
    normalize_states,
    ok_issue_payload,
    ok_search_payload,
    ok_sprints_payload,
)
from scrum_agent.reports.jobs import job_view
from scrum_agent.search.filters import IssueFilters
from scrum_agent.search.service import SearchService


def _clean(values: list[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)) or not all(isinstance(value, str) for value in values):
        raise ValueError("filter values must be provided as a list of strings")
    return tuple(value.strip() for value in values if value.strip())


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value.strip()


def make_tools(service: SearchService, jobs=None) -> list[FunctionTool]:
    """Build the read-only tools bound to ``service`` (and report ``jobs``)."""

    def get_issue(issue_key: str) -> dict:
        """Fetch one issue by its exact key (for example PAY-3).

        Use for direct key lookups. Returns the issue's key, summary, status,
        description, acceptance criteria, subtasks, linked work items, people,
        labels, due date and ratings with a fetched_at stamp, or an error
        payload (permission_denied / not_found).
        """
        try:
            return ok_issue_payload("get_issue", service.get_issue(_text(issue_key, "issue_key")))
        except Exception as exc:  # translated into a payload, never raised into ADK
            return error_payload("get_issue", exc)

    def list_sprints(states: list[str] | None = None) -> dict:
        """List the pilot board's sprints, optionally filtered by state.

        States must be a subset of future, active, closed; omit to list all.
        Use to resolve "this/current sprint" (state active) or to show
        candidates when a sprint name is ambiguous or unknown.
        """
        try:
            return ok_sprints_payload(
                "list_sprints", service.list_sprints(states=normalize_states(states))
            )
        except Exception as exc:
            return error_payload("list_sprints", exc)

    def search_issues(
        statuses: list[str] | None = None,
        assignees: list[str] | None = None,
        issue_types: list[str] | None = None,
        labels: list[str] | None = None,
        unresolved_only: bool = False,
        sprint_id: int | None = None,
    ) -> dict:
        """Search the pilot project's issues with typed filters.

        At least one filter must be given. statuses and issue_types may be
        partial or lowercase names (for example "progress" matches "In
        Progress"). assignees work best as exact full names ("Bao Ren"), and
        labels must be exact. 'Unassigned' in assignees matches unassigned
        issues. sprint_id must come from list_sprints or a previous result.
        Returns matching issues with sources, the compiled JQL and fetched_at,
        or an error payload (invalid_input if no filter is set).
        """
        try:
            filters = IssueFilters(
                statuses=_clean(statuses),
                assignees=_clean(assignees),
                issue_types=_clean(issue_types),
                labels=_clean(labels),
                unresolved_only=unresolved_only,
                sprint_id=sprint_id,
            )
            return ok_search_payload("search_issues", service.search_issues(filters))
        except Exception as exc:
            return error_payload("search_issues", exc)

    def search_sprint(
        sprint_reference: str,
        statuses: list[str] | None = None,
        assignees: list[str] | None = None,
        issue_types: list[str] | None = None,
        labels: list[str] | None = None,
        unresolved_only: bool = False,
    ) -> dict:
        """Search issues in one sprint, resolving the sprint by name or ID.

        sprint_reference is a sprint name, unique name substring or numeric ID
        (for example "Payments R2", "R3" or 78). Ambiguous references return
        error kind ambiguous_sprint with candidates: ask the user to choose,
        never pick one yourself. Other filters narrow the issues in that sprint;
        statuses and issue_types may be partial names; assignees and labels exact.
        """
        try:
            reference = _text(sprint_reference, "sprint_reference")
            filters = IssueFilters(
                statuses=_clean(statuses),
                assignees=_clean(assignees),
                issue_types=_clean(issue_types),
                labels=_clean(labels),
                unresolved_only=unresolved_only,
            )
            return ok_search_payload("search_sprint", service.search_sprint(reference, filters))
        except Exception as exc:
            return error_payload("search_sprint", exc)

    def build_sprint_report(sprint_reference: str | int) -> dict:
        """Start generating a sprint report and return a job handle immediately.

        sprint_reference is a sprint name, unique substring or numeric ID;
        ambiguous references return error kind ambiguous_sprint with candidates:
        ask the user to choose. The handle carries job_id, status and
        estimate_seconds; report generation runs in a local worker and never
        blocks this conversation. Call `get_report` with the job_id to poll.
        """
        try:
            if jobs is None:
                raise ValueError(
                    "reports require the local database (SCRUM_AGENT_DATABASE_URL); "
                    "start it with `docker compose up -d`"
                )
            handle = jobs.submit(sprint_reference)
            return {
                "ok": True,
                "tool": "build_sprint_report",
                "job_id": handle["job_id"],
                "status": handle["status"],
                "sprint_id": handle["sprint_id"],
                "sprint_name": handle["sprint_name"],
                "estimate_seconds": handle["estimate_seconds"],
                "reused": handle["reused"],
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "sources": [{"sprint_id": handle["sprint_id"]}],
            }
        except Exception as exc:
            return error_payload("build_sprint_report", exc)

    def get_report(job_id: int) -> dict:
        """Poll a report job: status while it runs, the full report when done.

        job_id comes from build_sprint_report. While status is queued/running,
        tell the user it is still generating. When done, the report carries the
        computed totals, blockers, freshness, completeness and narrative — its
        numbers are already computed; never recalculate or add to them. An
        error status carries the failure message in job.error.
        """
        try:
            if jobs is None:
                raise ValueError(
                    "reports require the local database (SCRUM_AGENT_DATABASE_URL); "
                    "start it with `docker compose up -d`"
                )
            row = jobs.storage().get_report_job(int(job_id))
            if row is None:
                raise ValueError(f"No report job {job_id} exists; build one first")
            view = job_view(row)
            report = view.pop("report", None)
            return {
                "ok": True,
                "tool": "get_report",
                "job": view,
                "report": report,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "sources": [{"sprint_id": view["sprint_id"]}],
            }
        except Exception as exc:
            return error_payload("get_report", exc)

    return [
        FunctionTool(func=get_issue),
        FunctionTool(func=list_sprints),
        FunctionTool(func=search_issues),
        FunctionTool(func=search_sprint),
        FunctionTool(func=build_sprint_report),
        FunctionTool(func=get_report),
    ]
