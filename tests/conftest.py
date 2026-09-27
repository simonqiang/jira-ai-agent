"""Shared test fixtures."""

from __future__ import annotations

import pytest

from scrum_agent.config import Settings

_DUMMY = {
    "jira_site": "test.atlassian.net",
    "jira_user_email": "sm@test.example",
    "jira_api_token": "tok-test-123",
    "jira_board_id": 42,
    "known_issue_key": "PAY-1",
}


def make_settings(**overrides: object) -> Settings:
    values = {**_DUMMY, **overrides}
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()
