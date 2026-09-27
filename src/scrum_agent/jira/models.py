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
    statuses: tuple[str, ...]

    @classmethod
    def from_api(cls, payload: dict) -> BoardColumn:
        statuses = payload.get("statuses") or []
        return cls(
            name=payload.get("name") or "",
            statuses=tuple(status.get("name") or "" for status in statuses),
        )


class BoardConfiguration(_Model):
    """Board column/status mapping - the authoritative source for the done rule."""

    id: int
    name: str
    columns: tuple[BoardColumn, ...]

    @classmethod
    def from_api(cls, payload: dict) -> BoardConfiguration:
        column_config = payload.get("columnConfig") or {}
        columns = column_config.get("columns") or []
        return cls(
            id=payload["id"],
            name=payload.get("name") or "",
            columns=tuple(BoardColumn.from_api(column) for column in columns),
        )

    def statuses_by_column(self) -> dict[str, tuple[str, ...]]:
        return {column.name: column.statuses for column in self.columns}
