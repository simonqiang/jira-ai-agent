"""Validated application configuration.

Secrets come from the environment (or a git-ignored `.env`); the API token is
kept as a `SecretStr`; formatted validation errors hide inputs. Callers must not
log raw validation-error inputs or authorization headers.
"""

from __future__ import annotations

import base64
import re
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

    @field_validator(
        "jira_site",
        "jira_user_email",
        "jira_auth_mode",
        "known_issue_key",
        "jira_project_key",
        "jira_cloud_id",
        "report_timezone",
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
