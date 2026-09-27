"""Typed Jira Cloud client.

Uses REST v3 for issues and the enhanced JQL search endpoint (`/rest/api/3/search/jql`
with `nextPageToken` pagination), and the Jira Software board APIs for boards and
board configuration. Authentication headers and base URL come from `Settings`;
credentials are never logged.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

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

_T = TypeVar("_T")

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
        try:
            response = self._http.request(method, path, params=params, json=json)
        except httpx.RequestError:
            raise JiraApiError("Cannot reach Jira; check network access and retry") from None

        if response.status_code == 429:
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
                if not math.isfinite(retry_after) or retry_after < 0:
                    retry_after = None
            except ValueError:
                retry_after = None
            raise JiraRateLimitedError(
                "Jira rate limit reached; honor Retry-After before retrying",
                retry_after=retry_after,
            )
        if response.status_code == 401:
            raise JiraAuthError(
                "Jira rejected the credentials (401): check token validity/expiry, "
                "the account email, required endpoint scopes, and the token's auth mode "
                "(scoped tokens need central endpoints and jira_cloud_id)",
                status_code=401,
            )
        if response.status_code == 403:
            raise JiraPermissionError(
                "Jira denied access (403): check resource permissions and token scopes",
                status_code=403,
            )
        if response.status_code == 404:
            raise JiraNotFoundError(f"Not found: {path}", status_code=404)
        if not response.is_success:
            raise JiraApiError(
                f"Jira error {response.status_code}; check the request and service availability. "
                "Upstream response content omitted for privacy.",
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError:
            raise JiraApiError("Jira returned a non-JSON response") from None
        if not isinstance(payload, dict):
            raise JiraApiError("Jira returned an unexpected response shape")
        return payload

    @staticmethod
    def _parse_response(parser: Callable[[dict], _T], payload: dict) -> _T:
        try:
            return parser(payload)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise JiraApiError("Jira returned incomplete or invalid resource data") from None

    def _check_issue_scope(self, issue_key: str) -> None:
        pattern = rf"{re.escape(self._settings.jira_project_key)}-[1-9][0-9]*"
        if not re.fullmatch(pattern, issue_key):
            raise JiraPermissionError("Issue is outside the configured pilot project")

    def _check_board_scope(self, board_id: int) -> None:
        if board_id != self._settings.jira_board_id:
            raise JiraPermissionError("Board is outside the configured pilot scope")

    # -- reads -------------------------------------------------------------

    def get_issue(self, issue_key: str) -> Issue:
        self._check_issue_scope(issue_key)
        issue = self._parse_response(
            Issue.from_api, self._request("GET", f"/rest/api/3/issue/{issue_key}")
        )
        self._check_issue_scope(issue.key)  # Jira may resolve a moved issue's old key.
        return issue

    def get_board(self, board_id: int) -> Board:
        self._check_board_scope(board_id)
        return self._parse_response(
            Board.from_api, self._request("GET", f"/rest/agile/1.0/board/{board_id}")
        )

    def get_board_configuration(self, board_id: int) -> BoardConfiguration:
        self._check_board_scope(board_id)
        return self._parse_response(
            BoardConfiguration.from_api,
            self._request("GET", f"/rest/agile/1.0/board/{board_id}/configuration"),
        )

    def _scoped_jql(self, jql: str) -> str:
        """Hard-scope every search to the configured pilot project (spec section 10:
        indexing and queries are bounded to selected projects)."""
        # Week 1 accepts predicates only. Validate grouping so caller text cannot
        # escape the outer project restriction; Jira validates predicate syntax.
        depth = 0
        quote: str | None = None
        escaped = False
        unquoted: list[str] = []
        for character in jql:
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                unquoted.append(" ")
                continue
            if character in ("'", '"'):
                quote = character
                unquoted.append(" ")
                continue
            unquoted.append(character)
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth < 0:
                    raise JiraApiError("JQL must have balanced parentheses")
        if quote or depth != 0 or not jql.strip():
            raise JiraApiError("JQL must be a nonempty predicate with balanced quotes/parentheses")
        if re.search(r"\border\s+by\b", "".join(unquoted), re.IGNORECASE):
            raise JiraApiError(
                "Pass a JQL predicate without ORDER BY; sorting is not supported yet"
            )
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
            items = payload.get("issues")
            next_token = payload.get("nextPageToken")
            if not isinstance(items, list) or (
                next_token is not None and not isinstance(next_token, str)
            ):
                raise JiraApiError("Jira returned an invalid search page")
            if payload.get("isLast") is False and not next_token:
                raise JiraApiError("Jira returned an incomplete search page without a next token")
            for item in items:
                issue = self._parse_response(Issue.from_api, item)
                self._check_issue_scope(issue.key)
                yield issue
            token = next_token
            if not token:
                return
        raise JiraApiError(
            f"JQL search exceeded {max_pages} pages; narrow the query or raise the limit"
        )
