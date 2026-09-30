"""The Week 2 checked query set (roadmap: "Build a small checked query set
containing empty results, ambiguous sprint names, multiple pages and a board
spanning the allowed project scope").

`FakeJira` implements the same endpoints and pagination contracts as the pilot's
Jira Cloud endpoints (issue by key, board sprint listing with startAt pagination,
sprint by ID, enhanced JQL search with nextPageToken), so each checked query
below runs against known fixture truth instead of canned responses. Week 3's
agent checks must answer this same set.

The fixtures contain synthetic data only - no real tickets, users or boards.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest

from scrum_agent.jira.errors import JiraPermissionError
from scrum_agent.search.errors import AmbiguousSprintError, SprintNotFoundError
from scrum_agent.search.filters import IssueFilters
from scrum_agent.search.models import SearchResult
from scrum_agent.search.service import SearchService

BOARD_ID = 42
OTHER_BOARD_ID = 99


# -- fixture data ---------------------------------------------------------------


@dataclass(frozen=True)
class FixtureSprint:
    id: int
    name: str
    state: str
    origin_board_id: int = BOARD_ID

    def payload(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "originBoardId": self.origin_board_id,
        }


@dataclass(frozen=True)
class FixtureIssue:
    key: str
    summary: str
    status: str
    issue_type: str
    assignee: str | None = None
    labels: tuple[str, ...] = ()
    sprint_id: int | None = None
    resolved: bool = False

    def payload(self) -> dict:
        return {
            "id": f"1{abs(hash(self.key)) % 10000:04d}",
            "key": self.key,
            "fields": {
                "summary": self.summary,
                "status": {"name": self.status},
                "issuetype": {"name": self.issue_type},
                "assignee": ({"displayName": self.assignee} if self.assignee is not None else None),
                "updated": "2026-09-27T08:00:00.000+0000",
            },
        }


DEFAULT_SPRINTS: tuple[FixtureSprint, ...] = (
    FixtureSprint(77, "Payments R1", "closed"),
    FixtureSprint(78, "Payments R2", "active"),
    FixtureSprint(79, "Payments R3", "future"),
)

DEFAULT_BOARD_CONFIG: dict = {
    "id": BOARD_ID,
    "name": "Payments Scrum Board",
    "filter": {"id": "1001"},
    "estimation": {
        "type": "field",
        "field": {"fieldId": "customfield_10002", "displayName": "Story Points"},
    },
    "columnConfig": {
        "columns": [
            {"name": "To Do", "statuses": [{"id": "1"}]},
            {"name": "Done", "statuses": [{"id": "5"}]},
        ]
    },
}

DEFAULT_ISSUES: tuple[FixtureIssue, ...] = (
    FixtureIssue(
        key="PAY-1",
        summary="Double charge on checkout retry",
        status="In Progress",
        issue_type="Bug",
        assignee="A. Developer",
        labels=("payments", "checkout"),
        sprint_id=78,
        resolved=False,
    ),
    FixtureIssue(
        key="PAY-2",
        summary="Refund webhook fails silently",
        status="Done",
        issue_type="Bug",
        assignee="A. Developer",
        labels=("payments", "webhook"),
        sprint_id=78,
        resolved=True,
    ),
    FixtureIssue(
        key="PAY-3",
        summary="Statement export",
        status="To Do",
        issue_type="Story",
        assignee=None,
        labels=("export",),
        sprint_id=78,
        resolved=False,
    ),
    FixtureIssue(
        key="PAY-4",
        summary="Rotate support rota",
        status="In Progress",
        issue_type="Task",
        assignee="S. Tester",
        labels=("ops",),
        sprint_id=None,
        resolved=False,
    ),
    FixtureIssue(
        key="PAY-5",
        summary="Login loop for SSO users",
        status="In Progress",
        issue_type="Bug",
        assignee=None,
        labels=("auth",),
        sprint_id=77,
        resolved=False,
    ),
    FixtureIssue(
        key="PAY-6",
        summary="CSV export drops last row",
        status="To Do",
        issue_type="Bug",
        assignee="S. Tester",
        labels=("export",),
        sprint_id=79,
        resolved=False,
    ),
)


# -- fake Jira server ------------------------------------------------------------


def _clause_values(jql: str, keyword: str) -> tuple[str, ...]:
    """Extract quoted values of `keyword = "x"` / `keyword in ("x", ...)` clauses."""
    single = re.search(rf'\b{keyword} = "((?:\\.|[^"\\])*)"', jql)
    if single:
        return (single.group(1),)
    multi = re.search(rf"\b{keyword} in \(([^)]*)\)", jql)
    if multi:
        return tuple(re.findall(r'"((?:\\.|[^"\\])*)"', multi.group(1)))
    return ()


def _matches(issue: FixtureIssue, jql: str) -> bool:
    """Evaluate the JQL shapes IssueFilters produces against a fixture issue."""
    if "resolution IS EMPTY" in jql and issue.resolved:
        return False
    sprint = re.search(r"\bsprint = (\d+)", jql)
    if sprint and issue.sprint_id != int(sprint.group(1)):
        return False
    statuses = _clause_values(jql, "status")
    if statuses and issue.status not in statuses:
        return False
    issue_types = _clause_values(jql, "issuetype")
    if issue_types and issue.issue_type not in issue_types:
        return False
    labels = _clause_values(jql, "labels")
    if labels and not set(issue.labels) & set(labels):
        return False
    assignees = _clause_values(jql, "assignee")
    if assignees or "assignee IS EMPTY" in jql:
        named_match = issue.assignee is not None and issue.assignee in assignees
        unassigned_match = issue.assignee is None and "assignee IS EMPTY" in jql
        if not (named_match or unassigned_match):
            return False
    return True


@dataclass
class FakeJira:
    """Mock transport serving fixture sprints/issues with real pagination.

    The collector routes (board configuration, issue changelogs) are served
    here too; `updated >=` JQL clauses are deliberately not filtered so a
    delta poll re-serves every fixture and replays exercise the dedup keys.
    """

    sprints: tuple[FixtureSprint, ...] = DEFAULT_SPRINTS
    issues: tuple[FixtureIssue, ...] = DEFAULT_ISSUES
    page_size: int = 2
    board_config: dict = field(default_factory=lambda: dict(DEFAULT_BOARD_CONFIG))
    changelogs: dict[str, list[dict]] = field(default_factory=dict)
    detail_overrides: dict[str, int] = field(default_factory=dict)
    search_calls: list[dict] = field(default_factory=list)
    detail_calls: list[dict] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/rest/agile/1.0/board/{BOARD_ID}/sprint":
            return self._sprints_page(request)
        if path == f"/rest/agile/1.0/board/{BOARD_ID}/configuration":
            return httpx.Response(200, json=self.board_config)
        match = re.fullmatch(r"/rest/agile/1.0/sprint/([1-9][0-9]*)", path)
        if match:
            return self._sprint_by_id(int(match.group(1)))
        match = re.fullmatch(r"/rest/api/3/issue/([A-Za-z0-9-]+)/changelog", path)
        if match:
            return self._changelog_page(match.group(1), request)
        match = re.fullmatch(r"/rest/api/3/issue/([A-Za-z0-9-]+)", path)
        if match:
            return self._issue_by_key(match.group(1), request)
        if path == "/rest/api/3/project/PAY/statuses":
            return self._project_statuses()
        if path == "/rest/api/3/user/assignable/search":
            return self._assignable_users(request)
        if path == "/rest/api/3/search/jql":
            return self._search(request)
        raise AssertionError(f"unexpected request {path}")

    def _project_statuses(self) -> httpx.Response:
        """Group the fixtures' statuses under their issue types (same v3 shape)."""
        type_names = list(dict.fromkeys(issue.issue_type for issue in self.issues))
        return httpx.Response(
            200,
            json=[
                {
                    "issueType": {"name": issue_type},
                    "statuses": [
                        {"name": status}
                        for status in dict.fromkeys(
                            issue.status for issue in self.issues if issue.issue_type == issue_type
                        )
                    ],
                }
                for issue_type in type_names
            ],
        )

    def _assignable_users(self, request: httpx.Request) -> httpx.Response:
        names = list(dict.fromkeys(issue.assignee for issue in self.issues if issue.assignee))
        params = request.url.params
        start_at = int(params.get("startAt", "0"))
        max_results = int(params.get("maxResults", "50"))
        return httpx.Response(
            200,
            json=[
                {"accountId": f"acc-{position}", "displayName": name}
                for position, name in enumerate(
                    names[start_at : start_at + max_results], start=start_at
                )
            ],
        )

    def _sprints_page(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        start_at = int(params.get("startAt", "0"))
        max_results = int(params.get("maxResults", "50"))
        states = tuple(state for state in params.get("state", "").split(",") if state)
        selected = [sprint for sprint in self.sprints if not states or sprint.state in states]
        window = selected[start_at : start_at + max_results]
        is_last = start_at + max_results >= len(selected)
        return httpx.Response(
            200,
            json={
                "maxResults": max_results,
                "startAt": start_at,
                "isLast": is_last,
                "total": len(selected),
                "values": [sprint.payload() for sprint in window],
            },
        )

    def _sprint_by_id(self, sprint_id: int) -> httpx.Response:
        for sprint in self.sprints:
            if sprint.id == sprint_id:
                return httpx.Response(200, json=sprint.payload())
        return httpx.Response(404, json={"errorMessages": ["Sprint does not exist"]})

    def _issue_by_key(self, issue_key: str, request: httpx.Request) -> httpx.Response:
        self.detail_calls.append(dict(request.url.params))
        override = self.detail_overrides.get(issue_key)
        if override is not None:
            return httpx.Response(override, json={"errorMessages": ["not available"]})
        for issue in self.issues:
            if issue.key == issue_key:
                return httpx.Response(200, json=issue.payload())
        return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})

    def _changelog_page(self, issue_key: str, request: httpx.Request) -> httpx.Response:
        entries = self.changelogs.get(issue_key, [])
        params = request.url.params
        start_at = int(params.get("startAt", "0"))
        max_results = int(params.get("maxResults", "100"))
        window = entries[start_at : start_at + max_results]
        return httpx.Response(
            200,
            json={
                "startAt": start_at,
                "maxResults": max_results,
                "total": len(entries),
                "isLast": start_at + max_results >= len(entries),
                "values": window,
            },
        )

    def _search(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.search_calls.append(body)
        matched = [issue for issue in self.issues if _matches(issue, body["jql"])]
        max_results = body.get("maxResults", 50)
        start = 0 if body.get("nextPageToken") is None else int(body["nextPageToken"])
        window = matched[start : start + max_results]
        payload: dict = {"issues": [issue.payload() for issue in window]}
        if start + max_results < len(matched):
            payload["nextPageToken"] = str(start + max_results)
        else:
            payload["isLast"] = True
        return httpx.Response(200, json=payload)


# -- the checked queries ---------------------------------------------------------


@dataclass(frozen=True)
class CheckedQuery:
    name: str
    intent: str
    make_jira: Callable[[], FakeJira]
    verify: Callable[[SearchService, FakeJira], None]


def _check_keys(result: SearchResult, expected_keys: tuple[str, ...]) -> None:
    assert tuple(issue.key for issue in result.issues) == expected_keys
    assert result.result_count == len(expected_keys)


def _expect_error(
    error_type: type[Exception],
    action: Callable[[], object],
    *,
    candidate_ids: tuple[int, ...] | None = None,
) -> None:
    with pytest.raises(error_type) as exc_info:
        action()
    if candidate_ids is not None:
        assert tuple(sprint.id for sprint in exc_info.value.candidates) == candidate_ids


def _check(condition: bool) -> None:
    assert condition


def _verify_issue_key_lookup(service: SearchService, jira: FakeJira) -> None:
    issue = service.get_issue("PAY-3")
    assert issue.key == "PAY-3"
    assert issue.summary == "Statement export"


def _verify_issue_key_outside_pilot(service: SearchService, jira: FakeJira) -> None:
    _expect_error(JiraPermissionError, lambda: service.get_issue("OTHER-9"))


def _verify_unresolved_bugs_in_sprint(service: SearchService, jira: FakeJira) -> None:
    result = service.search_sprint(
        "Payments R2", IssueFilters(issue_types=["Bug"], unresolved_only=True)
    )
    _check_keys(result, ("PAY-1",))


def _verify_empty_results(service: SearchService, jira: FakeJira) -> None:
    result = service.search_issues(IssueFilters(issue_types=["Epic"], sprint_id=78))
    assert result.is_empty
    assert result.result_count == 0


def _verify_multi_page_search(service: SearchService, jira: FakeJira) -> None:
    result = service.search_issues(IssueFilters(labels=["export"]), max_results_per_page=1)
    _check_keys(result, ("PAY-3", "PAY-6"))
    assert len(jira.search_calls) == 2, "expected two search pages"


def _verify_multi_page_sprint_listing(service: SearchService, jira: FakeJira) -> None:
    sprints = service.list_sprints(max_results_per_page=2)
    assert [sprint.id for sprint in sprints] == [77, 78, 79]


def _verify_partial_filter_names_expand(service: SearchService, jira: FakeJira) -> None:
    result = service.search_issues(
        IssueFilters(statuses=["progress"], issue_types=["bug"], assignees=["dev"])
    )
    _check_keys(result, ("PAY-1",))
    assert '"In Progress"' in result.jql
    assert '"Bug"' in result.jql
    assert '"A. Developer"' in result.jql


def _with_foreign_issue() -> FakeJira:
    return FakeJira(
        issues=DEFAULT_ISSUES
        + (
            FixtureIssue(
                key="OTHER-9",
                summary="Foreign project bug",
                status="In Progress",
                issue_type="Bug",
                labels=("payments",),
                sprint_id=78,
            ),
        )
    )


def _with_foreign_sprint() -> FakeJira:
    return FakeJira(
        sprints=DEFAULT_SPRINTS
        + (FixtureSprint(81, "Cross-board sprint", "active", OTHER_BOARD_ID),)
    )


CHECKED_QUERIES: tuple[CheckedQuery, ...] = (
    CheckedQuery(
        name="issue-key-lookup",
        intent="A known issue key returns that issue",
        make_jira=FakeJira,
        verify=_verify_issue_key_lookup,
    ),
    CheckedQuery(
        name="issue-key-outside-pilot-project",
        intent="An issue key outside the pilot project is denied, not fetched",
        make_jira=FakeJira,
        verify=_verify_issue_key_outside_pilot,
    ),
    CheckedQuery(
        name="sprint-by-id",
        intent="A numeric reference selects exactly that sprint",
        make_jira=FakeJira,
        verify=lambda service, jira: _check(service.resolve_sprint(78).name == "Payments R2"),
    ),
    CheckedQuery(
        name="sprint-by-exact-name",
        intent="An exact sprint name resolves to one sprint",
        make_jira=FakeJira,
        verify=lambda service, jira: _check(service.resolve_sprint("Payments R1").id == 77),
    ),
    CheckedQuery(
        name="sprint-by-unique-substring",
        intent="A unique substring resolves to one sprint",
        make_jira=FakeJira,
        verify=lambda service, jira: _check(service.resolve_sprint("R3").id == 79),
    ),
    CheckedQuery(
        name="ambiguous-sprint-name",
        intent="An ambiguous sprint name prompts a selection instead of guessing",
        make_jira=FakeJira,
        verify=lambda service, jira: _expect_error(
            AmbiguousSprintError,
            lambda: service.resolve_sprint("Payments"),
            candidate_ids=(77, 78, 79),
        ),
    ),
    CheckedQuery(
        name="unknown-sprint-name",
        intent="An unknown sprint name is reported as not found",
        make_jira=FakeJira,
        verify=lambda service, jira: _expect_error(
            SprintNotFoundError, lambda: service.resolve_sprint("Onboarding")
        ),
    ),
    CheckedQuery(
        name="unresolved-bugs-in-active-sprint",
        intent="The Friday demo: unresolved bugs in the selected sprint",
        make_jira=FakeJira,
        verify=_verify_unresolved_bugs_in_sprint,
    ),
    CheckedQuery(
        name="empty-results-are-accurate",
        intent="A query matching nothing reports an accurate zero, not an error",
        make_jira=FakeJira,
        verify=_verify_empty_results,
    ),
    CheckedQuery(
        name="search-spans-multiple-pages",
        intent="Results beyond one page are completely collected",
        make_jira=lambda: FakeJira(page_size=1),
        verify=_verify_multi_page_search,
    ),
    CheckedQuery(
        name="sprint-listing-spans-multiple-pages",
        intent="Sprint listings beyond one page are completely collected",
        make_jira=lambda: FakeJira(page_size=2),
        verify=_verify_multi_page_sprint_listing,
    ),
    CheckedQuery(
        name="partial-filter-names-expand",
        intent="Partial status/type/assignee names expand to the project's exact values",
        make_jira=FakeJira,
        verify=_verify_partial_filter_names_expand,
    ),
    CheckedQuery(
        name="board-spanning-project-scope-fails-closed",
        intent=(
            "Search results leaking from outside the pilot project deny the whole "
            "result set instead of surfacing unauthorized tickets"
        ),
        make_jira=_with_foreign_issue,
        verify=lambda service, jira: _expect_error(
            JiraPermissionError, lambda: service.search_issues(IssueFilters(labels=["payments"]))
        ),
    ),
    CheckedQuery(
        name="foreign-board-sprint-in-listing-fails-closed",
        intent="A sprint from another board never enters sprint selection",
        make_jira=_with_foreign_sprint,
        verify=lambda service, jira: _expect_error(
            JiraPermissionError, lambda: service.list_sprints()
        ),
    ),
    CheckedQuery(
        name="foreign-board-sprint-by-id-fails-closed",
        intent="Direct sprint lookup outside the pilot board is denied",
        make_jira=_with_foreign_sprint,
        verify=lambda service, jira: _expect_error(
            JiraPermissionError, lambda: service.resolve_sprint(81)
        ),
    ),
)
