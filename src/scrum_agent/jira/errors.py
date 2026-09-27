"""Typed errors for the Jira adapter.

Callers branch on these instead of parsing HTTP details; rate-limit handling
honors Retry-After via `JiraRateLimitedError.retry_after`.
"""

from __future__ import annotations


class JiraError(Exception):
    """Base class for Jira adapter errors."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class JiraAuthError(JiraError):
    """401 - credentials rejected (token expired/revoked, wrong email or auth mode)."""


class JiraPermissionError(JiraError):
    """403 - credentials valid but the user may not access this resource."""


class JiraNotFoundError(JiraError):
    """404 - issue, board or endpoint not found."""


class JiraRateLimitedError(JiraError):
    """429 - honor retry_after (seconds) before any retry."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class JiraApiError(JiraError):
    """Other non-success responses from Jira."""
