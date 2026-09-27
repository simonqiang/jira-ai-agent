"""Typed-filter JQL compilation (Week 2: no raw JQL from callers)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scrum_agent.search.filters import IssueFilters


def test_single_value_uses_equals() -> None:
    assert IssueFilters(statuses=["Open"]).to_jql() == 'status = "Open"'
    assert IssueFilters(issue_types=["Bug"]).to_jql() == 'issuetype = "Bug"'
    assert IssueFilters(labels=["payments"]).to_jql() == 'labels = "payments"'
    assert IssueFilters(assignees=["A. Developer"]).to_jql() == 'assignee = "A. Developer"'


def test_multiple_values_use_in() -> None:
    filters = IssueFilters(statuses=["Open", "In Progress"])
    assert filters.to_jql() == 'status in ("Open", "In Progress")'
    assert IssueFilters(issue_types=["Bug", "Task"]).to_jql() == 'issuetype in ("Bug", "Task")'
    assert IssueFilters(labels=["payments", "auth"]).to_jql() == 'labels in ("payments", "auth")'


def test_assignee_unassigned_compiles_to_is_empty() -> None:
    assert IssueFilters(assignees=["Unassigned"]).to_jql() == "assignee IS EMPTY"


def test_assignee_mixed_names_and_unassigned_form_an_or_group() -> None:
    filters = IssueFilters(assignees=["A. Developer", "Unassigned"])
    assert filters.to_jql() == '(assignee = "A. Developer" OR assignee IS EMPTY)'


def test_unresolved_and_sprint_id_compilation() -> None:
    assert IssueFilters(unresolved_only=True).to_jql() == "resolution IS EMPTY"
    assert IssueFilters(sprint_id=78).to_jql() == "sprint = 78"


def test_combined_filters_join_with_and() -> None:
    filters = IssueFilters(
        issue_types=["Bug"],
        statuses=["In Progress", "To Do"],
        labels=["payments"],
        sprint_id=78,
        unresolved_only=True,
    )
    assert filters.to_jql() == (
        'status in ("In Progress", "To Do") AND issuetype = "Bug" '
        'AND labels = "payments" AND sprint = 78 AND resolution IS EMPTY'
    )


def test_quotes_and_backslashes_in_values_are_escaped() -> None:
    filters = IssueFilters(statuses=['Bug" OR project = OTHER'])
    assert filters.to_jql() == 'status = "Bug\\" OR project = OTHER"'
    injection = IssueFilters(labels=["pay\\ments"])
    assert injection.to_jql() == 'labels = "pay\\\\ments"'


@pytest.mark.parametrize("field", ["statuses", "assignees", "issue_types", "labels"])
def test_blank_values_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        IssueFilters(**{field: ["  "]})


def test_control_characters_are_rejected() -> None:
    with pytest.raises(ValidationError):
        IssueFilters(statuses=["Open\nAND project = OTHER"])


def test_non_list_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        IssueFilters(statuses="Open")  # type: ignore[arg-type]


def test_nonpositive_sprint_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        IssueFilters(sprint_id=0)


def test_empty_filters_cannot_compile() -> None:
    with pytest.raises(ValueError, match="at least one filter"):
        IssueFilters().to_jql()


def test_is_empty_reflects_every_filter() -> None:
    assert IssueFilters().is_empty()
    assert not IssueFilters(unresolved_only=True).is_empty()
    assert not IssueFilters(sprint_id=78).is_empty()
    assert not IssueFilters(labels=["payments"]).is_empty()


def test_values_are_stripped_and_coerced_to_tuples() -> None:
    filters = IssueFilters(statuses=[" Open "])
    assert filters.statuses == ("Open",)


def test_filters_are_immutable() -> None:
    with pytest.raises(ValidationError):
        IssueFilters(statuses=["Open"]).statuses = ("Closed",)  # type: ignore[misc]
