from __future__ import annotations

from scrum_agent.pilot import format_checks, preflight
from tests.conftest import make_settings


def test_preflight_accepts_integrated_local_configuration() -> None:
    checks = preflight(
        make_settings(
            database_url="postgresql://pilot:password@127.0.0.1/scrum_agent",
            model_name="model", model_api_key="secret", embedding_model="embed",
        )
    )

    assert all(check.ok for check in checks)
    assert "Suggestions: enabled" in format_checks(checks)


def test_preflight_reports_missing_durable_store_without_leaking_url() -> None:
    checks = preflight(make_settings())
    rendered = format_checks(checks)

    assert next(check for check in checks if check.name == "durable storage").ok is False
    assert "SCRUM_AGENT_DATABASE_URL" in rendered
    assert "tok-test-123" not in rendered


def test_preflight_makes_disabled_suggestions_explicit() -> None:
    checks = preflight(
        make_settings(
            database_url="postgresql://pilot:password@127.0.0.1/scrum_agent",
            model_name="model", model_api_key="secret", embedding_model="embed",
            suggestions_enabled=False,
        )
    )

    assert "Suggestions: disabled" in format_checks(checks)


def test_preflight_never_discloses_database_credentials() -> None:
    checks = preflight(
        make_settings(
            database_url="postgresql://pilot:password@127.0.0.1/scrum_agent",
            model_name="model", model_api_key="secret", embedding_model="embed",
        )
    )

    rendered = format_checks(checks)
    assert "password" not in rendered
    assert "postgresql://" not in rendered
