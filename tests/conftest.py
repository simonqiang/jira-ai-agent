"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest

from scrum_agent.config import Settings

_DUMMY = {
    "jira_site": "test.atlassian.net",
    "jira_user_email": "sm@test.example",
    "jira_api_token": "tok-test-123",
    "jira_auth_mode": "basic_site",
    "jira_project_key": "PAY",
    "jira_board_id": 42,
    "known_issue_key": "PAY-1",
}


def make_settings(**overrides: object) -> Settings:
    values = {**_DUMMY, **overrides}
    # _env_file=None keeps tests isolated from a developer's real .env
    return Settings(_env_file=None, **values)  # type: ignore[arg-type,call-arg]


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in os.environ:
        if key.startswith("SCRUM_AGENT_"):
            monkeypatch.delenv(key)


@pytest.fixture
def settings() -> Settings:
    return make_settings()
