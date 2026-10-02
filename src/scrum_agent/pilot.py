"""Safe operational checks for the single-user local pilot."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from scrum_agent.config import Settings, require_embedding_settings, require_model_settings


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    ok: bool
    detail: str


def preflight(settings: Settings) -> tuple[ReadinessCheck, ...]:
    """Return sanitized local readiness checks without contacting Jira or printing settings."""
    checks = [
        ReadinessCheck(
            "loopback binding",
            settings.web_host in Settings.LOOPBACK_HOSTS,
            "loopback-only binding configured"
            if settings.web_host in Settings.LOOPBACK_HOSTS
            else "set SCRUM_AGENT_WEB_HOST to a loopback host",
        ),
        ReadinessCheck(
            "durable storage",
            settings.database_url is not None,
            "PostgreSQL configured" if settings.database_url else "set SCRUM_AGENT_DATABASE_URL",
        ),
    ]
    try:
        require_model_settings(settings)
        checks.append(ReadinessCheck("chat model", True, "model configuration present"))
    except ValueError as error:
        checks.append(ReadinessCheck("chat model", False, str(error)))
    try:
        require_embedding_settings(settings)
        checks.append(
            ReadinessCheck("retrieval embeddings", True, "embedding configuration present")
        )
    except ValueError as error:
        checks.append(ReadinessCheck("retrieval embeddings", False, str(error)))
    checks.append(
        ReadinessCheck(
            "suggestions",
            True,
            "enabled" if settings.suggestions_enabled else "disabled",
        )
    )
    return tuple(checks)


def format_checks(checks: tuple[ReadinessCheck, ...]) -> str:
    """Render checks without config values, credentials, or issue content."""
    lines = []
    for check in checks:
        if check.name == "suggestions":
            lines.append(f"Suggestions: {check.detail}")
        else:
            status = "OK" if check.ok else "FAIL"
            lines.append(f"{status} {check.name}: {check.detail}")
    return "\n".join(lines)


def configure_local_file_logging(settings: Settings) -> None:
    """Add one optional rotating local log handler without changing stderr logging."""
    if not settings.log_directory:
        return
    logger = logging.getLogger("scrum_agent")
    if any(getattr(handler, "_scrum_agent_pilot_log", False) for handler in logger.handlers):
        return
    directory = Path(settings.log_directory)
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "scrum-agent.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    handler._scrum_agent_pilot_log = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
