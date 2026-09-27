"""Typed Jira Cloud client.

Uses REST v3 for issues and the enhanced JQL search endpoint (`/rest/api/3/search/jql`
with `nextPageToken` pagination), and the Jira Software board APIs for boards and
board configuration. Authentication headers and base URL come from `Settings`;
credentials are never logged.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import httpx

from scrum_agent.config import Settings
from scrum_agent.jira.errors import (
    JiraApiError,
    JiraAuthError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitedError,
)
from scrum_agent.jira.models import Board, BoardConfiguration, Issue

logger = logging.getLogger(__name__)

_SEARCH_FIELDS = ["summary", "status", "issuetype", "assignee", "updated"]


class JiraClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._http = httpx.Client(
            base_url=settings.base_url(),
            headers=settings.auth_headers(),
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- low-level ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._http.request(method, path, params=params, json=json)

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise JiraRateLimitedError(
                "Jira rate limit reached; honor Retry-After before retrying",
                retry_after=float(retry_after) if retry_after is not None else None,
            )
        if response.status_code == 401:
            raise JiraAuthError(
                "Jira rejected the credentials (401): check token validity/expiry, "
                "the account email, and that the auth mode matches the token type "
                "(scoped tokens need central endpoints and jira_cloud_id)",
                status_code=401,
            )
        if response.status_code == 403:
            raise JiraPermissionError(
                "Jira denied access (403): the account may lack permission for this resource",
                status_code=403,
            )
        if response.status_code == 404:
            raise JiraNotFoundError(f"Not found: {path}", status_code=404)
        if not response.is_success:
            raise JiraApiError(
                f"Jira error {response.status_code} on {path}: {self._error_messages(response)}",
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        return response.json()

    @staticmethod
    def _error_messages(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        parts = list(body.get("errorMessages") or [])
        warning_messages = body.get("warningMessages") or []
        parts.extend(warning_messages)
        return "; ".join(parts) or response.text[:200]

    # -- reads -------------------------------------------------------------

    def get_issue(self, issue_key: str) -> Issue:
        return Issue.from_api(self._request("GET", f"/rest/api/3/issue/{issue_key}"))

    def get_board(self, board_id: int) -> Board:
        return Board.from_api(self._request("GET", f"/rest/agile/1.0/board/{board_id}"))

    def get_board_configuration(self, board_id: int) -> BoardConfiguration:
        return BoardConfiguration.from_api(
            self._request("GET", f"/rest/agile/1.0/board/{board_id}/configuration")
        )

    def _scoped_jql(self, jql: str) -> str:
        """Hard-scope every search to the configured pilot project (spec section 10:
        indexing and queries are bounded to selected projects)."""
        return f"project = {self._settings.jira_project_key} AND ({jql})"

    def iter_search_jql(
        self,
        jql: str,
        *,
        max_results_per_page: int = 50,
        max_pages: int = 20,
    ) -> Iterator[Issue]:
        """Iterate issues matching JQL, following nextPageToken across all pages.

        The query is always restricted to the configured project, regardless of
        what the caller passes."""
        token: str | None = None
        for _ in range(max_pages):
            body: dict[str, Any] = {
                "jql": self._scoped_jql(jql),
                "maxResults": max_results_per_page,
                "fields": _SEARCH_FIELDS,
            }
            if token is not None:
                body["nextPageToken"] = token
            payload = self._request("POST", "/rest/api/3/search/jql", json=body)
            yield from (Issue.from_api(issue) for issue in payload.get("issues") or [])
            token = payload.get("nextPageToken")
            if not token:
                return
        raise JiraApiError(
            f"JQL search exceeded {max_pages} pages; narrow the query or raise the limit"
        )
