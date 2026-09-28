"""Structured tool payloads: the model only ever sees these shapes.

Success payloads carry the data, the sources needed to cite it, the UTC
``fetched_at`` freshness stamp and the compiled JQL for transparency. Errors
are translated from the typed Week 2 taxonomy into ``error.kind`` values the
instruction teaches the model to handle (ask, abstain or stop); an exception
never escapes a tool into the ADK run loop.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from scrum_agent.jira.errors import (
    JiraApiError,
    JiraAuthError,
    JiraError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitedError,
)
from scrum_agent.jira.models import Issue, Sprint
from scrum_agent.search.errors import AmbiguousSprintError, SearchError, SprintNotFoundError
from scrum_agent.search.models import SearchResult

_VALID_SPRINT_STATES = ("future", "active", "closed")


def _issue_payload(issue: Issue) -> dict:
    return {
        "key": issue.key,
        "summary": issue.summary,
        "status": issue.status,
        "issue_type": issue.issue_type,
        "assignee": issue.assignee,
        "updated": issue.updated,
    }


def _sprint_payload(sprint: Sprint) -> dict:
    return {"id": sprint.id, "name": sprint.name, "state": sprint.state}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ok_search_payload(tool: str, result: SearchResult) -> dict:
    """Serialize a ``SearchResult`` with sources and freshness."""
    return {
        "ok": True,
        "tool": tool,
        "count": result.result_count,
        "issues": [_issue_payload(issue) for issue in result.issues],
        "sprint": _sprint_payload(result.sprint) if result.sprint is not None else None,
        "jql": result.jql,
        "fetched_at": result.fetched_at.isoformat(timespec="seconds"),
        "sources": ([{"sprint_id": result.sprint.id}] if result.sprint is not None else [])
        + [{"issue_key": issue.key} for issue in result.issues],
    }


def ok_issue_payload(tool: str, issue: Issue) -> dict:
    return {
        "ok": True,
        "tool": tool,
        "count": 1,
        "issues": [_issue_payload(issue)],
        "sprint": None,
        "jql": None,
        "fetched_at": _now_iso(),
        "sources": [{"issue_key": issue.key}],
    }


def ok_sprints_payload(tool: str, sprints: tuple[Sprint, ...]) -> dict:
    return {
        "ok": True,
        "tool": tool,
        "count": len(sprints),
        "sprints": [_sprint_payload(sprint) for sprint in sprints],
        "fetched_at": _now_iso(),
        "sources": [{"sprint_id": sprint.id} for sprint in sprints],
    }


def error_payload(tool: str, exc: Exception) -> dict:
    """Translate any exception into a structured, model-actionable payload.

    Order matters: sprint errors before ``SearchError`` siblings, Jira
    subtypes before ``JiraError``. Messages are the typed errors' own
    actionable text; tracebacks and raw payloads never appear.
    """
    error: dict = {"kind": "unexpected", "message": "The tool failed unexpectedly."}
    if isinstance(exc, AmbiguousSprintError):
        error = {
            "kind": "ambiguous_sprint",
            "message": str(exc),
            "candidates": [_sprint_payload(sprint) for sprint in exc.candidates],
        }
    elif isinstance(exc, SprintNotFoundError):
        error = {"kind": "sprint_not_found", "message": str(exc)}
    elif isinstance(exc, JiraPermissionError):
        error = {"kind": "permission_denied", "message": str(exc)}
    elif isinstance(exc, JiraAuthError):
        error = {"kind": "auth_failed", "message": str(exc)}
    elif isinstance(exc, JiraRateLimitedError):
        error = {
            "kind": "rate_limited",
            "message": str(exc),
            "retry_after_seconds": exc.retry_after,
        }
    elif isinstance(exc, JiraNotFoundError):
        error = {"kind": "not_found", "message": str(exc)}
    elif isinstance(exc, (JiraApiError, JiraError)):
        error = {"kind": "upstream_error", "message": str(exc)}
    elif isinstance(exc, SearchError):
        error = {"kind": "upstream_error", "message": str(exc)}
    elif isinstance(exc, (ValidationError, ValueError)):
        error = {"kind": "invalid_input", "message": str(exc)}
    return {"ok": False, "tool": tool, "error": error}


def normalize_states(states: list[str] | None) -> tuple[str, ...]:
    """Validate a sprint-state filter; raise ``ValueError`` on anything else."""
    cleaned = tuple(state.strip() for state in states or () if state.strip())
    unknown = [state for state in cleaned if state not in _VALID_SPRINT_STATES]
    if unknown:
        raise ValueError(
            f"sprint states must be a subset of {_VALID_SPRINT_STATES}; got {unknown}"
        )
    return cleaned
