"""Typed issue filters compiled to a project-scoped JQL predicate.

Callers never pass raw JQL: every value is validated and quoted here, so filter
input cannot inject JQL keywords. The pilot client additionally wraps the
compiled predicate in `project = <KEY> AND (...)`, keeping searches bounded.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

UNASSIGNED = "Unassigned"
"""Reserved assignee filter value meaning "no assignee" (JQL `assignee IS EMPTY`)."""


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _quote_list(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(_quote(value) for value in values) + ")"


def _predicate(keyword: str, values: tuple[str, ...]) -> str:
    """One value compiles to `=`, several to an `in (...)` list."""
    if len(values) == 1:
        return f"{keyword} = {_quote(values[0])}"
    return f"{keyword} in {_quote_list(values)}"


class IssueFilters(BaseModel):
    """Structured search filters; at least one must be set before compiling."""

    model_config = ConfigDict(frozen=True)

    statuses: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    issue_types: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    sprint_id: int | None = None
    unresolved_only: bool = False

    @field_validator("statuses", "assignees", "issue_types", "labels", mode="before")
    @classmethod
    def _coerce_sequences(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        raise ValueError("filter values must be provided as a list of strings")

    @field_validator("statuses", "assignees", "issue_types", "labels")
    @classmethod
    def _reject_blank_or_control_characters(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        for item in cleaned:
            if not item:
                raise ValueError("filter values must not be blank")
            if any(ord(character) < 32 or ord(character) == 127 for character in item):
                raise ValueError("filter values must not contain control characters")
        return cleaned

    @field_validator("sprint_id")
    @classmethod
    def _sprint_id_positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("sprint_id must be a positive integer")
        return value

    def is_empty(self) -> bool:
        return not (
            self.statuses
            or self.assignees
            or self.issue_types
            or self.labels
            or self.sprint_id is not None
            or self.unresolved_only
        )

    def to_jql(self) -> str:
        """Compile to a JQL predicate (no ORDER BY); raise if no filter is set."""
        if self.is_empty():
            raise ValueError("IssueFilters must include at least one filter")
        clauses: list[str] = []
        if self.statuses:
            clauses.append(_predicate("status", self.statuses))
        if self.issue_types:
            clauses.append(_predicate("issuetype", self.issue_types))
        if self.labels:
            clauses.append(_predicate("labels", self.labels))
        if self.assignees:
            named = tuple(a for a in self.assignees if a != UNASSIGNED)
            include_unassigned = UNASSIGNED in self.assignees
            parts: list[str] = []
            if named:
                parts.append(_predicate("assignee", named))
            if include_unassigned:
                parts.append("assignee IS EMPTY")
            clauses.append(parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")")
        if self.sprint_id is not None:
            clauses.append(f"sprint = {self.sprint_id}")
        if self.unresolved_only:
            clauses.append("resolution IS EMPTY")
        return " AND ".join(clauses)
