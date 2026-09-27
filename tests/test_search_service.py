"""Search service tests: sprint selection, ambiguity, accurate empty results."""

from __future__ import annotations

import json

import httpx
import pytest

from scrum_agent.jira.client import JiraClient
from scrum_agent.jira.errors import JiraPermissionError
from scrum_agent.search.errors import AmbiguousSprintError, SprintNotFoundError
from scrum_agent.search.filters import IssueFilters
from scrum_agent.search.service import SearchService
from tests.conftest import make_settings
from tests.test_jira_client import ISSUE_PAYLOAD

SPRINTS = [
    {"id": 77, "name": "Payments R1", "state": "closed", "originBoardId": 42},
    {"id": 78, "name": "Payments R2", "state": "active", "originBoardId": 42},
    {"id": 79, "name": "Payments R3", "state": "future", "originBoardId": 42},
]


def make_service(handler, **settings_overrides) -> tuple[SearchService, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    settings = make_settings(**settings_overrides)
    client = JiraClient(settings, transport=httpx.MockTransport(transport_handler))
    return SearchService(client), requests


def ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def listing_handler(sprints: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/board/42/sprint"):
            return ok({"isLast": True, "values": sprints})
        match_id = path.rsplit("/", 1)[-1]
        if path.startswith("/rest/agile/1.0/sprint/"):
            for sprint in sprints:
                if str(sprint["id"]) == match_id:
                    return ok(sprint)
            return httpx.Response(404, json={"errorMessages": ["not found"]})
        if path == "/rest/api/3/search/jql":
            return ok({"issues": [], "isLast": True})
        raise AssertionError(f"unexpected path {path}")

    return handler


def search_sprint_handler(calls: list[dict], *, search_payload: dict | None = None):
    """Handler for resolve-by-listing + search + sprint re-verification flows."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/board/42/sprint"):
            return ok({"isLast": True, "values": SPRINTS})
        if path.endswith("/sprint/78"):
            return ok(SPRINTS[1])
        if path == "/rest/api/3/search/jql":
            calls.append(json.loads(request.content))
            return ok(search_payload or {"issues": [], "isLast": True})
        raise AssertionError(f"unexpected path {path}")

    return handler


# -- sprint selection -----------------------------------------------------------


def test_resolve_sprint_by_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sprint/78")
        return ok(SPRINTS[1])

    service, _ = make_service(handler)
    sprint = service.resolve_sprint(78)
    assert (sprint.id, sprint.name) == (78, "Payments R2")


def test_resolve_sprint_by_numeric_string_uses_id_lookup() -> None:
    service, requests = make_service(
        lambda request: (
            ok(SPRINTS[1])
            if request.url.path.endswith("/sprint/78")
            else AssertionError(f"unexpected {request.url.path}")
        )
    )
    assert service.resolve_sprint("78").id == 78
    assert requests[0].url.path.endswith("/sprint/78")


def test_resolve_sprint_exact_name() -> None:
    service, _ = make_service(listing_handler(SPRINTS))
    assert service.resolve_sprint("Payments R1").id == 77


def test_resolve_sprint_unique_substring() -> None:
    service, _ = make_service(listing_handler(SPRINTS))
    assert service.resolve_sprint("R3").id == 79


def test_resolve_sprint_ambiguous_substring_lists_candidates() -> None:
    service, _ = make_service(listing_handler(SPRINTS))
    with pytest.raises(AmbiguousSprintError) as exc_info:
        service.resolve_sprint("Payments")
    assert [sprint.id for sprint in exc_info.value.candidates] == [77, 78, 79]
    assert "sprint ID" in str(exc_info.value)


def test_resolve_sprint_duplicate_exact_names_are_ambiguous() -> None:
    duplicated = [
        {"id": 77, "name": "Payments", "state": "closed", "originBoardId": 42},
        {"id": 78, "name": "Payments", "state": "active", "originBoardId": 42},
    ]
    service, _ = make_service(listing_handler(duplicated))
    with pytest.raises(AmbiguousSprintError) as exc_info:
        service.resolve_sprint("Payments")
    assert [sprint.id for sprint in exc_info.value.candidates] == [77, 78]


def test_resolve_sprint_not_found() -> None:
    service, _ = make_service(listing_handler(SPRINTS))
    with pytest.raises(SprintNotFoundError):
        service.resolve_sprint("No such sprint")


@pytest.mark.parametrize("reference", ["", "   "])
def test_resolve_sprint_blank_reference_is_not_found(reference: str) -> None:
    service, requests = make_service(listing_handler(SPRINTS))
    with pytest.raises(SprintNotFoundError):
        service.resolve_sprint(reference)
    assert requests == []


def test_resolve_sprint_respects_state_filter() -> None:
    service, requests = make_service(listing_handler(SPRINTS))
    service.resolve_sprint("R2", states=("active",))
    assert requests[0].url.params["state"] == "active"


def test_list_sprints_returns_parsed_sprints() -> None:
    service, _ = make_service(listing_handler(SPRINTS))
    sprints = service.list_sprints()
    assert [sprint.id for sprint in sprints] == [77, 78, 79]
    assert sprints[1].state == "active"


# -- issue search ---------------------------------------------------------------


def bug_payload(key: str, issue_id: str) -> dict:
    return {**ISSUE_PAYLOAD, "key": key, "id": issue_id}


def test_search_returns_complete_result_with_freshness_and_sprint() -> None:
    pages = [
        {"issues": [bug_payload("PAY-1", "10001")], "nextPageToken": "token-2"},
        {"issues": [bug_payload("PAY-3", "10003")], "isLast": True},
    ]
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/search/jql":
            calls.append(json.loads(request.content))
            return ok(pages[len(calls) - 1])
        if request.url.path.endswith("/sprint/78"):
            return ok(SPRINTS[1])
        raise AssertionError(f"unexpected path {request.url.path}")

    service, _ = make_service(handler)
    result = service.search_issues(
        IssueFilters(issue_types=["Bug"], sprint_id=78), max_results_per_page=1
    )

    assert [issue.key for issue in result.issues] == ["PAY-1", "PAY-3"]
    assert result.result_count == 2
    assert result.sprint is not None and result.sprint.id == 78
    assert result.fetched_at.tzinfo is not None
    assert calls[0]["jql"] == 'project = PAY AND (issuetype = "Bug" AND sprint = 78)'


def test_search_reports_empty_results_accurately() -> None:
    service, requests = make_service(
        lambda request: (
            ok({"issues": [], "isLast": True})
            if request.url.path == "/rest/api/3/search/jql"
            else AssertionError(f"unexpected {request.url.path}")
        )
    )
    result = service.search_issues(IssueFilters(issue_types=["Epic"]))
    assert result.issues == ()
    assert result.result_count == 0
    assert result.is_empty
    assert len(requests) == 1  # no sprint lookup unless a sprint filter is set


def test_search_fails_closed_on_out_of_scope_results() -> None:
    service, _ = make_service(
        lambda request: ok({"issues": [{**ISSUE_PAYLOAD, "key": "OTHER-1"}], "isLast": True})
    )
    with pytest.raises(JiraPermissionError):
        service.search_issues(IssueFilters(labels=["payments"]))


def test_search_sprint_resolves_name_then_filters_by_id() -> None:
    calls: list[dict] = []
    service, _ = make_service(
        search_sprint_handler(
            calls, search_payload={"issues": [bug_payload("PAY-1", "10001")], "isLast": True}
        )
    )
    result = service.search_sprint(
        "Payments R2", IssueFilters(issue_types=["Bug"], unresolved_only=True)
    )

    assert [issue.key for issue in result.issues] == ["PAY-1"]
    assert result.sprint is not None and result.sprint.id == 78
    assert calls[0]["jql"] == (
        'project = PAY AND (issuetype = "Bug" AND sprint = 78 AND resolution IS EMPTY)'
    )


def test_search_sprint_without_extra_filters() -> None:
    service, _ = make_service(search_sprint_handler([]))
    result = service.search_sprint("R2")
    assert result.jql == "sprint = 78"


def test_search_sprint_rejects_conflicting_sprint_filter() -> None:
    service, _ = make_service(listing_handler(SPRINTS))
    with pytest.raises(ValueError, match="only once"):
        service.search_sprint("Payments R2", IssueFilters(sprint_id=77))


def test_get_issue_delegates_to_client_scope_checks() -> None:
    service, requests = make_service(lambda request: ok(ISSUE_PAYLOAD))
    assert service.get_issue("PAY-1").key == "PAY-1"
    with pytest.raises(JiraPermissionError):
        service.get_issue("OTHER-1")
    assert len(requests) == 1


def test_service_exposes_the_client_scope() -> None:
    service, _ = make_service(lambda request: ok(ISSUE_PAYLOAD))
    assert service.scope.board_id == 42
    assert service.scope.project_key == "PAY"
