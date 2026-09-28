"""Narrow, read-only ADK tools over the Week 2 search service.

Four tools only: ``get_issue``, ``list_sprints``, ``search_issues`` and
``search_sprint``. Inputs are plain JSON primitives (ADK's argument coercion
swallows ``ValidationError`` for model classes, so ``IssueFilters`` is built
inside each tool); outputs are the structured payloads from ``payloads.py``.
No tool accepts raw JQL, credentials or any write parameter, and no tool can
widen scope: every call delegates to ``SearchService``, which enforces
``PilotScope`` end to end.
"""

from __future__ import annotations

from google.adk.tools import FunctionTool

from scrum_agent.agent.payloads import (
    error_payload,
    normalize_states,
    ok_issue_payload,
    ok_search_payload,
    ok_sprints_payload,
)
from scrum_agent.search.filters import IssueFilters
from scrum_agent.search.service import SearchService


def _clean(values: list[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)) or not all(
        isinstance(value, str) for value in values
    ):
        raise ValueError("filter values must be provided as a list of strings")
    return tuple(value.strip() for value in values if value.strip())


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value.strip()


def make_tools(service: SearchService) -> list[FunctionTool]:
    """Build the four read-only tools bound to ``service``."""

    def get_issue(issue_key: str) -> dict:
        """Fetch one issue by its exact key (for example PAY-3).

        Use for direct key lookups. Returns the issue's key, summary, status,
        issue type, assignee and last-updated time with a fetched_at stamp,
        or an error payload (permission_denied / not_found).
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

        At least one filter must be given. 'Unassigned' in assignees matches
        unassigned issues. sprint_id must come from list_sprints or a previous
        result. Returns matching issues with sources, the compiled JQL and
        fetched_at, or an error payload (invalid_input if no filter is set).
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
        never pick one yourself. Other filters narrow the issues in that sprint.
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
            return ok_search_payload(
                "search_sprint", service.search_sprint(reference, filters)
            )
        except Exception as exc:
            return error_payload("search_sprint", exc)

    return [
        FunctionTool(func=get_issue),
        FunctionTool(func=list_sprints),
        FunctionTool(func=search_issues),
        FunctionTool(func=search_sprint),
    ]
