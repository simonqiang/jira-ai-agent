"""Typed read models for Jira responses (Week 1 scope: issue + board reads)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Issue(_Model):
    key: str
    id: str
    summary: str
    status: str
    issue_type: str
    assignee: str | None = None
    updated: str | None = None

    @classmethod
    def from_api(cls, payload: dict) -> Issue:
        fields = payload.get("fields") or {}
        assignee = fields.get("assignee") or {}
        status = fields.get("status") or {}
        issue_type = fields.get("issuetype") or {}
        return cls(
            key=payload["key"],
            id=str(payload["id"]),
            summary=fields.get("summary") or "",
            status=status.get("name") or "Unknown",
            issue_type=issue_type.get("name") or "Unknown",
            assignee=assignee.get("displayName"),
            updated=fields.get("updated"),
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
