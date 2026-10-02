from __future__ import annotations

import logging
import subprocess

from scrum_agent.pilot import (
    configure_local_file_logging,
    format_checks,
    preflight,
    run_pilot_checks,
)
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


def test_optional_file_logging_is_rotating_and_idempotent(tmp_path) -> None:
    settings = make_settings(log_directory=str(tmp_path), log_max_bytes=10_000, log_backup_count=2)
    logger = logging.getLogger("scrum_agent")
    before = len(logger.handlers)

    configure_local_file_logging(settings)
    configure_local_file_logging(settings)
    logger.warning("safe test message")

    content = (tmp_path / "scrum-agent.log").read_text(encoding="utf-8")
    assert content.endswith("safe test message\n")
    assert len(logger.handlers) == before + 1


def test_pilot_check_uses_current_source_and_propagates_failure(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run(command, *, cwd, env, check):
        captured.update(command=command, cwd=cwd, env=env, check=check)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr("scrum_agent.pilot.subprocess.run", fake_run)

    assert run_pilot_checks(root=tmp_path) == 7
    assert captured["cwd"] == tmp_path
    assert captured["env"]["PYTHONPATH"].split(":")[0] == "src"
    assert "tests/test_ticketing_updates.py" in captured["command"]
