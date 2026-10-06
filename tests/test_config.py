"""Configuration validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scrum_agent.config import AuthMode, Settings, require_database_settings, require_model_settings
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


def test_model_secret_never_appears_in_repr_or_dump() -> None:
    settings = make_settings(model_name="glm-test", model_api_key="model-test-secret")
    assert "model-test-secret" not in repr(settings)
    assert "model-test-secret" not in str(settings.model_dump())


def test_model_settings_are_optional_until_chat_requires_them() -> None:
    settings = make_settings()
    assert settings.model_name is None
    assert settings.model_api_key is None
    with pytest.raises(ValueError, match="SCRUM_AGENT_MODEL_NAME"):
        require_model_settings(settings)


def test_model_settings_allow_chat_when_both_are_present() -> None:
    settings = make_settings(model_name="glm-test", model_api_key="model-test-secret")
    assert require_model_settings(settings) is None


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


def test_database_settings_are_optional() -> None:
    settings = make_settings()
    assert settings.database_url is None
    assert settings.token_expires_on is None
    with pytest.raises(ValueError, match="SCRUM_AGENT_DATABASE_URL"):
        require_database_settings(settings)


def test_database_url_normalizes_legacy_scheme_and_strips() -> None:
    settings = make_settings(database_url=" postgres://scrum_agent:pw@127.0.0.1:5432/db ")
    assert settings.database_url == "postgresql://scrum_agent:pw@127.0.0.1:5432/db"
    assert require_database_settings(settings) is None


def test_database_url_accepts_postgresql_scheme() -> None:
    settings = make_settings(database_url="postgresql://scrum_agent:pw@localhost/db")
    assert settings.database_url == "postgresql://scrum_agent:pw@localhost/db"


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "mysql://scrum_agent@127.0.0.1/db"},
        {"database_url": "postgresql://"},  # no host
        {"database_url": "not-a-url"},
        {"database_url": "   "},
    ],
)
def test_invalid_database_url_fails_validation(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_token_expiry_accepts_iso_date_and_blank() -> None:
    assert make_settings(token_expires_on="2026-12-01").token_expires_on.isoformat() == "2026-12-01"
    assert make_settings(token_expires_on="").token_expires_on is None
    # A past date is valid configuration: it raises the expired alarm later.
    assert make_settings(token_expires_on="2020-01-01").token_expires_on.isoformat() == "2020-01-01"


def test_invalid_token_expiry_fails_validation() -> None:
    with pytest.raises(ValidationError):
        make_settings(token_expires_on="not-a-date")


# -- Week 10 embedding settings ------------------------------------------------


def test_embedding_settings_are_optional_by_default() -> None:
    settings = make_settings()
    assert settings.embedding_model is None
    assert settings.embedding_api_key is None
    assert settings.embedding_base_url == "https://api.z.ai/api/paas/v4"


def test_suggestions_enabled_by_default_and_env_switchable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert make_settings().suggestions_enabled is True
    monkeypatch.setenv("SCRUM_AGENT_SUGGESTIONS_ENABLED", "false")
    assert make_settings().suggestions_enabled is False


def test_embedding_base_url_must_be_https_without_trailing_slash() -> None:
    with pytest.raises(ValidationError):
        make_settings(embedding_base_url="http://api.example.com")
    with pytest.raises(ValidationError):
        make_settings(embedding_base_url="https://api.example.com/")
    settings = make_settings(embedding_base_url="https://api.example.com/v4")
    assert settings.embedding_base_url == "https://api.example.com/v4"


def test_blank_embedding_values_are_unset() -> None:
    # Blank matches unset so a copied .env.example (optional keys left blank)
    # still loads; features degrade to unconfigured instead of erroring.
    settings = make_settings(embedding_model="  ", embedding_api_key="   ")
    assert settings.embedding_model is None
    assert settings.embedding_api_key is None


def test_require_embedding_settings_falls_back_to_the_model_key() -> None:
    from scrum_agent.config import require_embedding_settings

    settings = make_settings(embedding_model="embedding-3", model_api_key="tok-test-123")
    base_url, api_key, model = require_embedding_settings(settings)
    assert (base_url, model) == ("https://api.z.ai/api/paas/v4", "embedding-3")
    assert api_key == "tok-test-123"  # falls back to the chat model key

    with pytest.raises(ValueError, match="EMBEDDING_MODEL"):
        require_embedding_settings(make_settings())


def test_embedding_key_is_never_disclosed_in_repr() -> None:
    settings = make_settings(embedding_api_key="sk-embedding-secret")
    assert "sk-embedding-secret" not in repr(settings)
    assert "sk-embedding-secret" not in str(settings.model_dump())
