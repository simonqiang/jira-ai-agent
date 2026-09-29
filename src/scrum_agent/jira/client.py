"""Typed Jira Cloud client.

Uses REST v3 for issues and the enhanced JQL search endpoint (`/rest/api/3/search/jql`
with `nextPageToken` pagination), and the Jira Software board/sprint APIs.
Authentication headers and base URL come from `Settings`; credentials are never
logged. All scope decisions delegate to the centralized `PilotScope` (Week 2).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Sequence
from typing import Any, TypeVar

import httpx

from scrum_agent.auth import PilotScope
from scrum_agent.config import Settings
from scrum_agent.jira.errors import (
    JiraApiError,
    JiraAuthError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitedError,
)
from scrum_agent.jira.models import (
    Board,
    BoardConfiguration,
    ChangelogEntry,
    Issue,
    Sprint,
)

_T = TypeVar("_T")

_SEARCH_FIELDS = ["summary", "status", "issuetype", "assignee", "updated"]

# Curated field list for the collector's authoritative per-issue read; the
# board-specific estimate field is appended per run via extra_fields.
_ISSUE_DETAIL_FIELDS = [
    "summary",
    "status",
    "issuetype",
    "assignee",
    "updated",
    "created",
    "resolution",
    "labels",
    "sprint",
]

_SPRINT_STATES = ("future", "active", "closed")


class JiraClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        scope: PilotScope | None = None,
    ):
        self._settings = settings
        self._scope = scope if scope is not None else PilotScope.from_settings(settings)
        self._field_names: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._assignable_names: tuple[str, ...] | None = None
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

    @property
    def scope(self) -> PilotScope:
        """The centralized pilot scope every read is checked against."""
        return self._scope

    # -- low-level ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
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
        if not isinstance(payload, (dict, list)):
            raise JiraApiError("Jira returned an unexpected response shape")
        return payload

    @staticmethod
    def _parse_response(parser: Callable[[dict], _T], payload: dict) -> _T:
        try:
            return parser(payload)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise JiraApiError("Jira returned incomplete or invalid resource data") from None

    def _check_issue_scope(self, issue_key: str) -> None:
        self._scope.assert_issue_key(issue_key)

    def _check_board_scope(self, board_id: int) -> None:
        self._scope.assert_board_id(board_id)

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

    def get_issue_detail(
        self, issue_key: str, *, extra_fields: Sequence[str] = ()
    ) -> dict[str, Any]:
        """Authoritative issue read for collection: curated fields, raw payload.

        Returns the raw response so the collector can persist the ``fields``
        object verbatim (the board-specific estimate field id arrives via
        ``extra_fields``). The returned key is re-checked so a moved issue
        cannot smuggle an out-of-scope payload in.
        """
        self._check_issue_scope(issue_key)
        fields = [*_ISSUE_DETAIL_FIELDS, *extra_fields]
        payload = self._request(
            "GET",
            f"/rest/api/3/issue/{issue_key}",
            params={"fields": ",".join(fields)},
        )
        key = payload.get("key") if isinstance(payload, dict) else None
        if not isinstance(key, str) or not key:
            raise JiraApiError("Jira returned an issue without a key")
        self._check_issue_scope(key)
        return payload

    def iter_issue_changelog(
        self,
        issue_key: str,
        *,
        max_results_per_page: int = 100,
        max_pages: int = 20,
        on_page: Callable[[], None] | None = None,
    ) -> Iterator[ChangelogEntry]:
        """Iterate an issue's changelog, following startAt/isLast pagination.

        Mirrors the sprint-listing contract: malformed pages fail closed and a
        page cap fails loudly instead of truncating history silently.
        """
        self._check_issue_scope(issue_key)
        start_at = 0
        for _ in range(max_pages):
            payload = self._request(
                "GET",
                f"/rest/api/3/issue/{issue_key}/changelog",
                params={"startAt": start_at, "maxResults": max_results_per_page},
            )
            values = payload.get("values")
            is_last = payload.get("isLast")
            if not isinstance(values, list) or not isinstance(is_last, bool):
                raise JiraApiError("Jira returned an invalid changelog page")
            if on_page is not None:
                on_page()
            for item in values:
                yield self._parse_response(ChangelogEntry.from_api, item)
            if is_last:
                return
            if not values:
                raise JiraApiError("Jira returned an incomplete changelog page without values")
            start_at += len(values)
        raise JiraApiError(f"Changelog listing exceeded {max_pages} pages; raise the limit")

    def get_board_configuration(self, board_id: int) -> BoardConfiguration:
        self._check_board_scope(board_id)
        return self._parse_response(
            BoardConfiguration.from_api,
            self._request("GET", f"/rest/agile/1.0/board/{board_id}/configuration"),
        )

    def get_sprint(self, sprint_id: int) -> Sprint:
        """Fetch one sprint; sprints from other boards fail closed."""
        if sprint_id <= 0:
            raise JiraApiError("sprint_id must be a positive integer")
        sprint = self._parse_response(
            Sprint.from_api, self._request("GET", f"/rest/agile/1.0/sprint/{sprint_id}")
        )
        self._scope.assert_sprint(sprint)
        return sprint

    def iter_board_sprints(
        self,
        board_id: int,
        *,
        states: Sequence[str] = (),
        max_results_per_page: int = 50,
        max_pages: int = 20,
    ) -> Iterator[Sprint]:
        """Iterate the board's sprints (agile API), following startAt pagination.

        Every returned sprint is verified to originate from the configured board,
        so a board filter spanning other boards cannot leak sprints into results.
        """
        self._check_board_scope(board_id)
        invalid_states = set(states) - set(_SPRINT_STATES)
        if invalid_states:
            raise JiraApiError(
                f"Invalid sprint states {sorted(invalid_states)}; "
                f"valid states are {list(_SPRINT_STATES)}"
            )
        start_at = 0
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "startAt": start_at,
                "maxResults": max_results_per_page,
            }
            if states:
                params["state"] = ",".join(states)
            payload = self._request(
                "GET", f"/rest/agile/1.0/board/{board_id}/sprint", params=params
            )
            values = payload.get("values")
            is_last = payload.get("isLast")
            if not isinstance(values, list) or not isinstance(is_last, bool):
                raise JiraApiError("Jira returned an invalid sprint page")
            for item in values:
                sprint = self._parse_response(Sprint.from_api, item)
                self._scope.assert_sprint(sprint)
                yield sprint
            if is_last:
                return
            if not values:
                raise JiraApiError("Jira returned an incomplete sprint page without values")
            start_at += len(values)
        raise JiraApiError(
            f"Sprint listing exceeded {max_pages} pages; narrow the states or raise the limit"
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
        on_page: Callable[[], None] | None = None,
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
            if on_page is not None:
                on_page()
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

    def project_field_names(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The pilot project's (status names, issue type names), cached per client.

        Feeds partial filter matching: JQL `~` only works on text fields, so
        status/type filter values are resolved against these exact names before
        the JQL compiles.
        """
        if self._field_names is None:
            payload = self._request(
                "GET", f"/rest/api/3/project/{self._settings.jira_project_key}/statuses"
            )
            if not isinstance(payload, list):
                raise JiraApiError("Jira returned an unexpected response shape")
            status_names: list[str] = []
            type_names: list[str] = []
            for group in payload:
                if not isinstance(group, dict):
                    raise JiraApiError("Jira returned an invalid project statuses page")
                for status in group.get("statuses") or ():
                    name = status.get("name") if isinstance(status, dict) else None
                    if name:
                        status_names.append(name)
                issue_type = group.get("issueType")
                if isinstance(issue_type, dict) and issue_type.get("name"):
                    type_names.append(issue_type["name"])
            self._field_names = (
                tuple(dict.fromkeys(status_names)),
                tuple(dict.fromkeys(type_names)),
            )
        return self._field_names

    def assignable_user_names(
        self, *, max_results_per_page: int = 50, max_pages: int = 20
    ) -> tuple[str, ...]:
        """Display names of users assignable to the pilot project, cached per client.

        The endpoint returns bare arrays, so a short page marks the end
        instead of an `isLast` flag.
        """
        if self._assignable_names is None:
            names: list[str] = []
            start_at = 0
            for _ in range(max_pages):
                payload = self._request(
                    "GET",
                    "/rest/api/3/user/assignable/search",
                    params={
                        "project": self._settings.jira_project_key,
                        "startAt": start_at,
                        "maxResults": max_results_per_page,
                    },
                )
                if not isinstance(payload, list):
                    raise JiraApiError("Jira returned an invalid assignable users page")
                names.extend(
                    display
                    for user in payload
                    if isinstance(user, dict)
                    for display in (user.get("displayName"),)
                    if display
                )
                if len(payload) < max_results_per_page:
                    break
                start_at += len(payload)
            else:
                raise JiraApiError(
                    f"Assignable user listing exceeded {max_pages} pages; raise the limit"
                )
            self._assignable_names = tuple(dict.fromkeys(names))
        return self._assignable_names
