"""Validated application configuration.

Secrets come from the environment (or a git-ignored `.env`); the API token is
kept as a `SecretStr` so it never appears in reprs, logs or error messages.
"""

from __future__ import annotations

import base64

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
    )

    jira_site: str = Field(min_length=1)
    jira_user_email: str = Field(min_length=1)
    jira_api_token: SecretStr
    jira_auth_mode: str = AuthMode.BASIC_SITE
    jira_cloud_id: str | None = None
    jira_board_id: int = Field(gt=0)
    known_issue_key: str = Field(min_length=1)
    report_timezone: str = "Asia/Hong_Kong"

    @field_validator(
        "jira_site", "jira_user_email", "jira_auth_mode", "known_issue_key", mode="before"
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

    @field_validator("jira_site")
    @classmethod
    def _site_is_bare_host(cls, value: str) -> str:
        if "://" in value or "/" in value:
            raise ValueError(
                "jira_site must be the bare host, e.g. 'yourteam.atlassian.net' "
                "(no https:// and no path)"
            )
        return value

    @model_validator(mode="after")
    def _check_auth_mode(self) -> Settings:
        if self.jira_auth_mode not in AuthMode.ALL:
            raise ValueError(
                f"jira_auth_mode must be one of {AuthMode.ALL}, got {self.jira_auth_mode!r}"
            )
        if self.jira_auth_mode in AuthMode.CENTRAL and not self.jira_cloud_id:
            raise ValueError(
                f"jira_auth_mode={self.jira_auth_mode!r} requires jira_cloud_id "
                "(central api.atlassian.com endpoints are per-cloudId)"
            )
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
