"""Configuration validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scrum_agent.config import AuthMode, Settings
from tests.conftest import make_settings

_ENV_KEYS = (
    "SCRUM_AGENT_JIRA_SITE",
    "SCRUM_AGENT_JIRA_USER_EMAIL",
    "SCRUM_AGENT_JIRA_API_TOKEN",
    "SCRUM_AGENT_JIRA_BOARD_ID",
    "SCRUM_AGENT_KNOWN_ISSUE_KEY",
)


def test_required_fields_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_token_never_appears_in_repr() -> None:
    settings = make_settings()
    assert "tok-test-123" not in repr(settings)
    assert "tok-test-123" not in str(settings.model_dump())


def test_basic_site_mode_uses_site_base_url() -> None:
    settings = make_settings(jira_auth_mode=AuthMode.BASIC_SITE)
    assert settings.base_url() == "https://test.atlassian.net"
    assert settings.auth_headers()["Authorization"].startswith("Basic ")


def test_bearer_central_requires_cloud_id() -> None:
    with pytest.raises(ValidationError):
        make_settings(jira_auth_mode=AuthMode.BEARER_CENTRAL)


def test_central_modes_use_central_base_url() -> None:
    for mode in (AuthMode.BASIC_CENTRAL, AuthMode.BEARER_CENTRAL):
        settings = make_settings(jira_auth_mode=mode, jira_cloud_id="abc-123")
        assert settings.base_url() == "https://api.atlassian.com/ex/jira/abc-123"


def test_bearer_central_auth_header() -> None:
    settings = make_settings(jira_auth_mode=AuthMode.BEARER_CENTRAL, jira_cloud_id="abc-123")
    assert settings.auth_headers() == {"Authorization": "Bearer tok-test-123"}


def test_unknown_auth_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(jira_auth_mode="oauth_magic")


def test_blank_values_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(known_issue_key="   ")
    with pytest.raises(ValidationError):
        make_settings(jira_board_id=0)


def test_values_are_stripped() -> None:
    settings = make_settings(jira_site=" test.atlassian.net ")
    assert settings.base_url() == "https://test.atlassian.net"


def test_site_must_be_bare_host() -> None:
    with pytest.raises(ValidationError):
        make_settings(jira_site="https://test.atlassian.net")


@pytest.mark.parametrize(
    "overrides",
    [
        {"jira_api_token": "   "},
        {"jira_auth_mode": "basic_central", "jira_cloud_id": "   "},
        {"jira_auth_mode": "basic_central", "jira_cloud_id": "../other"},
        {"jira_project_key": "PAY OR project = OTHER"},
        {"known_issue_key": "OTHER-1"},
        {"known_issue_key": "PAY-1/../../search"},
        {"report_timezone": "Invalid/Timezone"},
        {"jira_site": "test.atlassian.net@evil.example"},
        {"jira_site": "test.atlassian.net?query"},
    ],
)
def test_invalid_configuration_fails_before_network(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_validation_error_does_not_disclose_raw_token() -> None:
    with pytest.raises(ValidationError) as caught:
        make_settings(jira_auth_mode="invalid")
    assert "tok-test-123" not in str(caught.value)
