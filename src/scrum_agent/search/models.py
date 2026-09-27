"""Result models for the search service."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from scrum_agent.jira.models import Issue, Sprint


class SearchResult(BaseModel):
    """A complete search outcome: zero issues is an accurate result, not an error."""

    model_config = ConfigDict(frozen=True)

    issues: tuple[Issue, ...]
    jql: str
    sprint: Sprint | None = None
    fetched_at: datetime

    @property
    def result_count(self) -> int:
        return len(self.issues)

    @property
    def is_empty(self) -> bool:
        return not self.issues
