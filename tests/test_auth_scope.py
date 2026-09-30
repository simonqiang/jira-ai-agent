"""Centralized pilot-scope checks (Week 2 deliverable: trusted scope)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scrum_agent.auth import PilotScope
from scrum_agent.jira.errors import JiraPermissionError
from tests.conftest import make_settings


def make_scope(**overrides: object) -> PilotScope:
    values = {"site": "test.atlassian.net", "project_key": "PAY", "board_id": 42, **overrides}
    return PilotScope(**values)  # type: ignore[arg-type]


def test_from_settings_copies_the_trusted_scope() -> None:
    scope = PilotScope.from_settings(make_settings())
    assert (scope.site, scope.project_key, scope.board_id) == (
        "test.atlassian.net",
        "PAY",
        42,
    )


@pytest.mark.parametrize(
    "issue_key, allowed",
    [
        ("PAY-1", True),
        ("PAY-123", True),
        ("pay-1", False),
        ("PAY-0", False),
        ("PAY-01", False),
        ("OTHER-1", False),
        ("PAY-1/../../search", False),
        ("PAY-1 OR project = OTHER", False),
        ("", False),
    ],
)
def test_issue_key_scope(issue_key: str, allowed: bool) -> None:
    scope = make_scope()
    assert scope.allows_issue_key(issue_key) is allowed
    if allowed:
        scope.assert_issue_key(issue_key)
    else:
        with pytest.raises(JiraPermissionError):
            scope.assert_issue_key(issue_key)


@pytest.mark.parametrize("board_id", [42, 41, 99, 0, -1])
def test_board_scope(board_id: int) -> None:
    scope = make_scope()
    if board_id == 42:
        scope.assert_board_id(board_id)
    else:
        with pytest.raises(JiraPermissionError):
            scope.assert_board_id(board_id)


def test_scope_is_immutable() -> None:
    with pytest.raises(ValidationError):
        make_scope().board_id = 99  # type: ignore[misc]


@pytest.mark.parametrize(
    "field, value",
    [("project_key", "pay"), ("project_key", ""), ("board_id", 0), ("site", "")],
)
def test_invalid_scope_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_scope(**{field: value})
