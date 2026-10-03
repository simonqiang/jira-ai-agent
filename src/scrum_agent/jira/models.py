"""Typed read models for Jira responses (Week 1 scope: issue + board reads)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


def parse_jira_time(value: str) -> datetime:
    """Parse a Jira timestamp (e.g. 2026-09-27T08:00:00.000+0000) as aware UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"Jira timestamp without an offset: {value!r}")
    return parsed


def adf_text(text: str) -> dict:
    """Build an Atlassian Document Format doc from plain text.

    The exact inverse of ``jira_text`` for parser-produced text: one paragraph
    per line, empty lines become empty paragraphs, so writing a description
    read back from Jira round-trips byte-for-byte. Jira Cloud v3 rejects plain
    strings for ADF fields (for example ``description``) with HTTP 400.
    """
    lines = text.split("\n")
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": ([{"type": "text", "text": line}] if line else [])}
            for line in lines
        ],
    }


def jira_text(value: object) -> str | None:
    """Return plain text from a Jira string or Atlassian Document Format value."""
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None

    parts: list[str] = []

    def visit(node: object) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            parts.append(node["text"])
        for child in node.get("content") or ():
            visit(child)
        if node.get("type") in {"paragraph", "heading", "listItem"} and parts:
            parts.append("\n")

    visit(value)
    text = "".join(parts).strip()
    return text or None


def select_value(value: object) -> str | None:
    """Extract the display value from a Jira select field."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return value["value"] or None
    return None


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Subtask(_Model):
    key: str
    summary: str
    status: str
    priority: str | None = None

    @classmethod
    def from_api(cls, payload: dict) -> Subtask | None:
        key = payload.get("key")
        fields = payload.get("fields")
        if not isinstance(key, str) or not isinstance(fields, dict):
            return None
        return cls(
            key=key,
            summary=fields.get("summary") or "",
            status=(fields.get("status") or {}).get("name") or "Unknown",
            priority=(fields.get("priority") or {}).get("name"),
        )


class LinkedWorkItem(_Model):
    relationship: str
    key: str
    summary: str
    status: str

    @classmethod
    def from_api(cls, payload: dict) -> LinkedWorkItem | None:
        link_type = payload.get("type") or {}
        if not isinstance(link_type, dict):
            return None
        if isinstance(payload.get("outwardIssue"), dict):
            issue = payload["outwardIssue"]
            relationship = link_type.get("outward")
        elif isinstance(payload.get("inwardIssue"), dict):
            issue = payload["inwardIssue"]
            relationship = link_type.get("inward")
        else:
            return None
        fields = issue.get("fields") or {}
        key = issue.get("key")
        if not isinstance(key, str) or not isinstance(fields, dict):
            return None
        return cls(
            relationship=relationship if isinstance(relationship, str) else "linked to",
            key=key,
            summary=fields.get("summary") or "",
            status=(fields.get("status") or {}).get("name") or "Unknown",
        )


class Issue(_Model):
    key: str
    id: str
    summary: str
    status: str
    issue_type: str
    assignee: str | None = None
    updated: str | None = None
    description: str | None = None
    acceptance_criteria: str | None = None
    subtasks: tuple[Subtask, ...] = ()
    linked_work_items: tuple[LinkedWorkItem, ...] = ()
    reporter: str | None = None
    labels: tuple[str, ...] = ()
    due_date: str | None = None
    severity: str | None = None
    risk_rating: str | None = None
    issue_rating: str | None = None
    priority: str | None = None

    @classmethod
    def from_api(cls, payload: dict) -> Issue:
        fields = payload.get("fields") or {}
        assignee = fields.get("assignee") or {}
        status = fields.get("status") or {}
        issue_type = fields.get("issuetype") or {}
        reporter = fields.get("reporter") or {}
        subtasks = tuple(
            parsed
            for subtask in fields.get("subtasks") or ()
            if isinstance(subtask, dict)
            for parsed in (Subtask.from_api(subtask),)
            if parsed is not None
        )
        linked_work_items = tuple(
            parsed
            for link in fields.get("issuelinks") or ()
            if isinstance(link, dict)
            for parsed in (LinkedWorkItem.from_api(link),)
            if parsed is not None
        )
        return cls(
            key=payload["key"],
            id=str(payload["id"]),
            summary=fields.get("summary") or "",
            status=status.get("name") or "Unknown",
            issue_type=issue_type.get("name") or "Unknown",
            assignee=assignee.get("displayName"),
            updated=fields.get("updated"),
            description=jira_text(fields.get("description")),
            acceptance_criteria=jira_text(fields.get("customfield_10350")),
            subtasks=subtasks,
            linked_work_items=linked_work_items,
            reporter=reporter.get("displayName"),
            labels=tuple(label for label in fields.get("labels") or () if isinstance(label, str)),
            due_date=fields.get("duedate") if isinstance(fields.get("duedate"), str) else None,
            severity=select_value(fields.get("customfield_10199")),
            risk_rating=select_value(fields.get("customfield_10263")),
            issue_rating=select_value(fields.get("customfield_10249")),
            priority=(fields.get("priority") or {}).get("name"),
        )


class ChangelogItem(_Model):
    """One field change within a changelog entry (items carry no id of their own)."""

    field: str
    field_id: str | None = None
    from_id: str | None = None
    from_value: str | None = None
    to_id: str | None = None
    to_value: str | None = None

    @classmethod
    def from_api(cls, payload: dict) -> ChangelogItem:
        return cls(
            field=payload["field"],
            field_id=payload.get("fieldId"),
            from_id=payload.get("from"),
            from_value=payload.get("fromString"),
            to_id=payload.get("to"),
            to_value=payload.get("toString"),
        )


class ChangelogEntry(_Model):
    """One dated changelog event: identity, author, when, and the item changes."""

    id: str
    created: datetime
    author: str | None = None
    items: tuple[ChangelogItem, ...]

    @classmethod
    def from_api(cls, payload: dict) -> ChangelogEntry:
        return cls(
            id=str(payload["id"]),
            created=parse_jira_time(payload["created"]),
            author=(payload.get("author") or {}).get("displayName"),
            items=tuple(ChangelogItem.from_api(item) for item in payload.get("items") or []),
        )


class Board(_Model):
    id: int
    name: str
    type: str
    project_name: str | None = None

    @classmethod
    def from_api(cls, payload: dict) -> Board:
        location = payload.get("location") or {}
        return cls(
            id=payload["id"],
            name=payload.get("name") or "",
            type=payload.get("type") or "",
            project_name=location.get("projectName") or location.get("name"),
        )


class BoardColumn(_Model):
    name: str
    # Board configuration returns status IDs, not status names.
    statuses: tuple[str, ...]

    @classmethod
    def from_api(cls, payload: dict) -> BoardColumn:
        statuses = payload.get("statuses") or []
        return cls(
            name=payload.get("name") or "",
            statuses=tuple(str(status["id"]) for status in statuses),
        )


class Sprint(_Model):
    id: int
    name: str
    state: str  # Jira Software states: future, active or closed.
    origin_board_id: int
    start_date: str | None = None
    end_date: str | None = None
    complete_date: str | None = None
    goal: str | None = None

    @classmethod
    def from_api(cls, payload: dict) -> Sprint:
        return cls(
            id=payload["id"],
            name=payload.get("name") or "",
            state=payload.get("state") or "",
            origin_board_id=payload["originBoardId"],
            start_date=payload.get("startDate"),
            end_date=payload.get("endDate"),
            complete_date=payload.get("completeDate"),
            goal=payload.get("goal"),
        )

    @property
    def is_closed(self) -> bool:
        return self.state == "closed"


class BoardConfiguration(_Model):
    """Board column/status mapping - the authoritative source for the done rule."""

    id: int
    name: str
    columns: tuple[BoardColumn, ...]
    filter_id: str | None = None
    estimation_type: str | None = None
    estimate_field_id: str | None = None
    estimate_field_name: str | None = None

    @classmethod
    def from_api(cls, payload: dict) -> BoardConfiguration:
        column_config = payload.get("columnConfig") or {}
        columns = column_config.get("columns") or []
        estimation = payload.get("estimation") or {}
        estimate_field = estimation.get("field") or {}
        filter_id = (payload.get("filter") or {}).get("id")
        return cls(
            id=payload["id"],
            name=payload.get("name") or "",
            columns=tuple(BoardColumn.from_api(column) for column in columns),
            filter_id=str(filter_id) if filter_id is not None else None,
            estimation_type=estimation.get("type"),
            estimate_field_id=estimate_field.get("fieldId"),
            estimate_field_name=estimate_field.get("displayName"),
        )

    @property
    def done_status_ids(self) -> tuple[str, ...]:
        """Jira's done rule: the last column containing mapped statuses."""
        return next((column.statuses for column in reversed(self.columns) if column.statuses), ())

    def statuses_by_column(self) -> dict[str, tuple[str, ...]]:
        return {column.name: column.statuses for column in self.columns}
