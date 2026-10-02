"""Validated application configuration.

Secrets come from the environment (or a git-ignored `.env`); the API token is
kept as a `SecretStr`; formatted validation errors hide inputs. Callers must not
log raw validation-error inputs or authorization headers.
"""

from __future__ import annotations

import base64
import re
from datetime import date
from typing import ClassVar
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthMode:
    """Authentication strategies supported by the Jira adapter."""

    BASIC_SITE = "basic_site"
    BASIC_CENTRAL = "basic_central"
    BEARER_CENTRAL = "bearer_central"

    ALL = (BASIC_SITE, BASIC_CENTRAL, BEARER_CENTRAL)
    CENTRAL = (BASIC_CENTRAL, BEARER_CENTRAL)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCRUM_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
        protected_namespaces=("settings_",),  # 'model_*' fields are provider config
    )

    jira_site: str = Field(min_length=1)
    jira_user_email: str = Field(min_length=1)
    jira_api_token: SecretStr
    jira_auth_mode: str = AuthMode.BASIC_SITE
    jira_cloud_id: str | None = None
    jira_project_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    jira_board_id: int = Field(gt=0)
    known_issue_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
    report_timezone: str = "Asia/Hong_Kong"

    # Week 3: conversational agent and local web UI. Model settings are optional
    # so probe/sprints/search keep working without them; chat features validate
    # through require_model_settings() at startup instead.
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    model_base_url: str = "https://api.z.ai/api/anthropic"
    model_max_tokens: int = Field(default=8192, ge=1024, le=32768)
    web_host: str = "127.0.0.1"
    web_port: int = Field(default=8741, ge=1, le=65535)
    approval_user_id: str = Field(default="local-pilot", min_length=1, max_length=100)

    # Week 4: local collection storage and the credential-freshness alarm. Both
    # are optional so Week 1-3 commands keep working without a database;
    # storage commands validate through require_database_settings() instead.
    database_url: str | None = None
    token_expires_on: date | None = None

    # Week 10: semantic retrieval. Optional like the chat model — when unset,
    # reindex/related keep working through structured search only. The endpoint
    # is any OpenAI-compatible /embeddings API.
    embedding_model: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str = "https://api.z.ai/api/paas/v4"

    # Week 11: related-ticket suggestions (find_related_tickets and the
    # related-work section on drafts) can be switched off without touching the
    # retrieval index; core drafting keeps working.
    suggestions_enabled: bool = True

    LOOPBACK_HOSTS: ClassVar[tuple[str, ...]] = ("127.0.0.1", "::1", "localhost")

    @field_validator(
        "jira_site",
        "jira_user_email",
        "jira_auth_mode",
        "known_issue_key",
        "jira_project_key",
        "jira_cloud_id",
        "report_timezone",
        "model_name",
        "model_base_url",
        "web_host",
        "database_url",
        "embedding_model",
        "embedding_base_url",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("jira_api_token", mode="before")
    @classmethod
    def _strip_token(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("jira_api_token")
    @classmethod
    def _token_not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("jira_api_token must not be blank")
        return value

    @field_validator("jira_cloud_id")
    @classmethod
    def _cloud_id_is_path_segment(cls, value: str | None) -> str | None:
        if value == "":
            return None
        if value is not None and not re.fullmatch(r"[A-Za-z0-9-]+", value):
            raise ValueError("jira_cloud_id must contain only letters, digits and hyphens")
        return value

    @field_validator("report_timezone")
    @classmethod
    def _timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("report_timezone must be an installed IANA timezone") from None
        return value

    @field_validator("jira_site")
    @classmethod
    def _site_is_bare_host(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.atlassian\.net", value):
            raise ValueError(
                "jira_site must be a Jira Cloud host, e.g. 'yourteam.atlassian.net' "
                "(no https:// and no path)"
            )
        return value

    @field_validator("model_api_key", "model_name", "embedding_api_key", "embedding_model")
    @classmethod
    def _model_optional_not_blank(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            if not value.get_secret_value().strip():
                raise ValueError("must not be blank")
            return value
        if isinstance(value, str) and not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("model_base_url")
    @classmethod
    def _model_base_url_is_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("model_base_url must be an https:// URL")
        if parsed.query or parsed.fragment or value.endswith("/"):
            raise ValueError("model_base_url must not carry a query, fragment or trailing slash")
        return value

    @field_validator("embedding_base_url")
    @classmethod
    def _embedding_base_url_is_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("embedding_base_url must be an https:// URL")
        if parsed.query or parsed.fragment or value.endswith("/"):
            raise ValueError(
                "embedding_base_url must not carry a query, fragment or trailing slash"
            )
        return value

    @field_validator("database_url")
    @classmethod
    def _database_url_is_postgres(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("postgres://", "postgresql://", 1)
        parsed = urlparse(normalized)
        if parsed.scheme != "postgresql" or not parsed.netloc:
            raise ValueError("database_url must be a postgresql:// URL with a host")
        return normalized

    @field_validator("token_expires_on", mode="before")
    @classmethod
    def _blank_expiry_is_unset(cls, value: object) -> object:
        # A past date stays a *valid* config: it produces an expired alarm, not
        # a settings error. Jira has no API to read a token's expiry date.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("web_host")
    @classmethod
    def _web_host_is_loopback(cls, value: str) -> str:
        if value not in Settings.LOOPBACK_HOSTS:
            raise ValueError(
                f"web_host must be one of {Settings.LOOPBACK_HOSTS} "
                "(the pilot binds to loopback only)"
            )
        return value

    @model_validator(mode="after")
    def _check_auth_mode(self) -> Settings:
        if self.jira_auth_mode not in AuthMode.ALL:
            raise ValueError(f"jira_auth_mode must be one of {AuthMode.ALL}")
        if self.jira_auth_mode in AuthMode.CENTRAL and not self.jira_cloud_id:
            raise ValueError(
                f"jira_auth_mode={self.jira_auth_mode!r} requires jira_cloud_id "
                "(central api.atlassian.com endpoints are per-cloudId)"
            )
        if not self.known_issue_key.startswith(f"{self.jira_project_key}-"):
            raise ValueError("known_issue_key must belong to jira_project_key")
        return self

    def base_url(self) -> str:
        """API root for the configured auth mode."""
        if self.jira_auth_mode in AuthMode.CENTRAL:
            return f"https://api.atlassian.com/ex/jira/{self.jira_cloud_id}"
        return f"https://{self.jira_site}"

    def auth_headers(self) -> dict[str, str]:
        """Authorization header for the configured auth mode."""
        if self.jira_auth_mode == AuthMode.BEARER_CENTRAL:
            return {"Authorization": f"Bearer {self.jira_api_token.get_secret_value()}"}
        raw = f"{self.jira_user_email}:{self.jira_api_token.get_secret_value()}".encode()
        encoded = base64.b64encode(raw).decode()
        return {"Authorization": f"Basic {encoded}"}


def require_model_settings(settings: Settings) -> None:
    """Fail fast for chat features that need a live model.

    probe/sprints/search never call this, so the Week 1-2 CLI keeps working on a
    Jira-only environment.
    """
    missing = [
        name
        for name, value in (
            ("SCRUM_AGENT_MODEL_NAME", settings.model_name),
            ("SCRUM_AGENT_MODEL_API_KEY", settings.model_api_key),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            f"chat features require {' and '.join(missing)} in the environment or .env "
            "(see .env.example)"
        )


def require_embedding_settings(settings: Settings) -> tuple[str, str, str]:
    """Fail fast for retrieval features and return (base_url, api_key, model).

    The embedding key falls back to the chat model key: one z.ai key serves
    both endpoints, so the pilot usually configures nothing extra.
    """
    api_key = settings.embedding_api_key or settings.model_api_key
    missing = [
        name
        for name, value in (
            ("SCRUM_AGENT_EMBEDDING_MODEL", settings.embedding_model),
            ("SCRUM_AGENT_EMBEDDING_API_KEY (or MODEL_API_KEY)", api_key),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            f"retrieval features require {' and '.join(missing)} in the environment or .env"
        )
    return (
        settings.embedding_base_url,
        api_key.get_secret_value(),
        settings.embedding_model or "",
    )


def require_database_settings(settings: Settings) -> None:
    """Fail fast for storage commands that need the local Postgres database.

    probe/sprints/search/chat never call this, so they keep working without one.
    """
    if settings.database_url is None:
        raise ValueError(
            "storage commands require SCRUM_AGENT_DATABASE_URL in the environment or .env "
            "(see .env.example; start the local database with `docker compose up -d`)"
        )
