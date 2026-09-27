"""The single authority for the trusted pilot scope: one site, one project, one board.

Centralizing these checks (roadmap Week 2) is what lets later weeks - agent chat,
reports, exports and retrieval - reuse the same authorization decisions instead
of drifting: revoked or out-of-scope data must fail closed on every path.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from scrum_agent.config import Settings
from scrum_agent.jira.errors import JiraPermissionError
from scrum_agent.jira.models import Sprint


class PilotScope(BaseModel):
    """Resource scope every read is bound to; check failures raise, never filter."""

    model_config = ConfigDict(frozen=True)

    site: str = Field(min_length=1)
    project_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    board_id: int = Field(gt=0)

    @classmethod
    def from_settings(cls, settings: Settings) -> PilotScope:
        return cls(
            site=settings.jira_site,
            project_key=settings.jira_project_key,
            board_id=settings.jira_board_id,
        )

    @property
    def issue_key_pattern(self) -> re.Pattern[str]:
        return re.compile(rf"{re.escape(self.project_key)}-[1-9][0-9]*")

    def allows_issue_key(self, issue_key: str) -> bool:
        return self.issue_key_pattern.fullmatch(issue_key) is not None

    def assert_issue_key(self, issue_key: str) -> None:
        if not self.allows_issue_key(issue_key):
            raise JiraPermissionError("Issue is outside the configured pilot project")

    def assert_board_id(self, board_id: int) -> None:
        if board_id != self.board_id:
            raise JiraPermissionError("Board is outside the configured pilot scope")

    def assert_sprint(self, sprint: Sprint) -> None:
        if sprint.origin_board_id != self.board_id:
            raise JiraPermissionError(
                "Sprint belongs to a board outside the configured pilot scope"
            )
