"""Jira client tests against a mocked transport (no live credentials)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from scrum_agent.jira.client import JiraClient
from scrum_agent.jira.errors import (
    JiraApiError,
    JiraAuthError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitedError,
)
from tests.conftest import make_settings

ISSUE_PAYLOAD = {
    "id": "10001",
    "key": "PAY-1",
    "fields": {
        "summary": "Double charge on checkout retry",
        "status": {"name": "In Progress"},
        "issuetype": {"name": "Bug"},
        "assignee": {"displayName": "A. Developer"},
        "updated": "2026-09-27T08:00:00.000+0000",
    },
}

BOARD_PAYLOAD = {
    "id": 42,
    "name": "Payments Scrum Board",
    "type": "scrum",
    "location": {"projectName": "Payments"},
}

BOARD_CONFIGURATION_PAYLOAD = {
    "id": 42,
    "name": "Payments Scrum Board",
    "filter": {"id": "1001", "self": "https://test.atlassian.net/rest/api/3/filter/1001"},
    "estimation": {
        "type": "field",
        "field": {"fieldId": "customfield_10002", "displayName": "Story Points"},
    },
    "columnConfig": {
        "columns": [
            {
                "name": "To Do",
                "statuses": [{"id": "1"}, {"id": "4"}],
            },
            {"name": "In Progress", "statuses": [{"id": "3"}]},
            {"name": "Done", "statuses": [{"id": "5"}, {"id": "6"}]},
            {"name": "Unused", "statuses": []},
        ]
    },
}


def make_client(handler, **settings_overrides) -> tuple[JiraClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    settings = make_settings(**settings_overrides)
    client = JiraClient(settings, transport=httpx.MockTransport(transport_handler))
    return client, requests


def ok(payload: dict, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=payload, headers=headers or {})


def test_get_issue_parses_nested_fields() -> None:
    client, _ = make_client(lambda request: ok(ISSUE_PAYLOAD))
    issue = client.get_issue("PAY-1")
    assert issue.key == "PAY-1"
    assert issue.summary == "Double charge on checkout retry"
    assert issue.status == "In Progress"
    assert issue.issue_type == "Bug"
    assert issue.assignee == "A. Developer"


def test_authorization_header_sent_basic_site() -> None:
    client, requests = make_client(lambda request: ok(ISSUE_PAYLOAD))
    client.get_issue("PAY-1")
    expected = base64.b64encode(b"sm@test.example:tok-test-123").decode()
    assert requests[0].headers["Authorization"] == f"Basic {expected}"
    assert requests[0].url.host == "test.atlassian.net"


@pytest.mark.parametrize("auth_mode", ["basic_central", "bearer_central"])
def test_central_request_preserves_cloud_id_prefix_and_auth(auth_mode: str) -> None:
    client, requests = make_client(
        lambda request: ok(ISSUE_PAYLOAD),
        jira_auth_mode=auth_mode,
        jira_cloud_id="test-cloud-id",
    )
    client.get_issue("PAY-1")
    assert str(requests[0].url) == (
        "https://api.atlassian.com/ex/jira/test-cloud-id/rest/api/3/issue/PAY-1"
    )
    if auth_mode == "basic_central":
        encoded = base64.b64encode(b"sm@test.example:tok-test-123").decode()
        expected = f"Basic {encoded}"
    else:
        expected = "Bearer tok-test-123"
    assert requests[0].headers["Authorization"] == expected


def test_error_mapping() -> None:
    cases = [
        (401, JiraAuthError),
        (403, JiraPermissionError),
        (404, JiraNotFoundError),
        (500, JiraApiError),
    ]
    for status, expected_error in cases:

        def handler(request: httpx.Request, status: int = status) -> httpx.Response:
            return httpx.Response(status, json={"errorMessages": ["boom"]})

        client, _ = make_client(handler)
        with pytest.raises(expected_error):
            client.get_issue("PAY-1")


def test_rate_limit_carries_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"errorMessages": []}, headers={"Retry-After": "7"})

    client, _ = make_client(handler)
    with pytest.raises(JiraRateLimitedError) as exc_info:
        client.get_issue("PAY-1")
    assert exc_info.value.retry_after == 7.0


def test_board_and_configuration_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/configuration"):
            return ok(BOARD_CONFIGURATION_PAYLOAD)
        if path.endswith("/board/42"):
            return ok(BOARD_PAYLOAD)
        raise AssertionError(f"unexpected path {path}")

    client, _ = make_client(handler)
    board = client.get_board(42)
    assert (board.id, board.name, board.type) == (42, "Payments Scrum Board", "scrum")

    configuration = client.get_board_configuration(42)
    mapping = configuration.statuses_by_column()
    assert mapping["Done"] == ("5", "6")
    assert configuration.done_status_ids == ("5", "6")
    assert configuration.estimation_type == "field"
    assert configuration.estimate_field_id == "customfield_10002"
    assert configuration.estimate_field_name == "Story Points"
    assert configuration.filter_id == "1001"


def test_search_jql_follows_next_page_token() -> None:
    pages = [
        {
            "issues": [ISSUE_PAYLOAD, {**ISSUE_PAYLOAD, "key": "PAY-2", "id": "10002"}],
            "nextPageToken": "token-2",
        },
        {
            "issues": [{**ISSUE_PAYLOAD, "key": "PAY-3", "id": "10003"}],
            "nextPageToken": None,
        },
    ]
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return ok(pages[len(calls) - 1])

    client, _ = make_client(handler)
    issues = list(client.iter_search_jql("assignee = currentUser()"))

    assert [issue.key for issue in issues] == ["PAY-1", "PAY-2", "PAY-3"]
    assert len(calls) == 2
    assert calls[0]["jql"] == "project = PAY AND (assignee = currentUser())"
    assert "nextPageToken" not in calls[0]
    assert calls[1]["nextPageToken"] == "token-2"


def test_search_jql_always_scoped_to_configured_project() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"issues": []})

    client, requests = make_client(handler)
    list(client.iter_search_jql('text ~ "billing" OR key = PAY-9'))
    body = json.loads(requests[0].content)
    assert body["jql"] == 'project = PAY AND (text ~ "billing" OR key = PAY-9)'


def test_search_jql_enforces_page_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"issues": [ISSUE_PAYLOAD], "nextPageToken": "always-more"})

    client, _ = make_client(handler)
    with pytest.raises(JiraApiError, match="exceeded"):
        list(client.iter_search_jql("project = PAY", max_pages=3))


@pytest.mark.parametrize(
    "method, target",
    [
        ("get_issue", "OTHER-1"),
        ("get_issue", "PAY-1/../../search"),
        ("get_board", 99),
        ("get_board_configuration", 99),
    ],
)
def test_out_of_scope_requests_are_blocked_before_http(method: str, target: object) -> None:
    client, requests = make_client(lambda request: ok(ISSUE_PAYLOAD))
    with pytest.raises(JiraPermissionError):
        getattr(client, method)(target)
    assert requests == []


def test_moved_issue_outside_pilot_is_not_returned() -> None:
    client, _ = make_client(lambda request: ok({**ISSUE_PAYLOAD, "key": "OTHER-1"}))
    with pytest.raises(JiraPermissionError):
        client.get_issue("PAY-1")


def test_search_does_not_return_out_of_scope_issues() -> None:
    client, _ = make_client(
        lambda request: ok(
            {
                "issues": [{**ISSUE_PAYLOAD, "key": "OTHER-1"}],
            }
        )
    )
    with pytest.raises(JiraPermissionError):
        list(client.iter_search_jql("status = Open"))


@pytest.mark.parametrize(
    "jql",
    [
        "status = Open) OR project = OTHER OR (status = Closed",
        'text ~ "unterminated',
        "status = Open ORDER BY updated DESC",
        "",
    ],
)
def test_unsafe_or_unsupported_jql_is_rejected_before_http(jql: str) -> None:
    client, requests = make_client(lambda request: ok({"issues": []}))
    with pytest.raises(JiraApiError):
        list(client.iter_search_jql(jql))
    assert requests == []


def test_quoted_parentheses_do_not_break_scoped_jql() -> None:
    client, requests = make_client(lambda request: ok({"issues": []}))
    list(client.iter_search_jql('text ~ "billing)" AND status in (Open, Closed)'))
    assert json.loads(requests[0].content)["jql"] == (
        'project = PAY AND (text ~ "billing)" AND status in (Open, Closed))'
    )


def test_connection_failure_becomes_safe_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sensitive upstream text", request=request)

    client, _ = make_client(handler)
    with pytest.raises(JiraApiError) as caught:
        client.get_issue("PAY-1")
    assert "sensitive upstream text" not in str(caught.value)


@pytest.mark.parametrize("payload", [[], {}, {"key": "PAY-1"}, {"fields": []}])
def test_malformed_issue_response_becomes_typed_error(payload: object) -> None:
    client, _ = make_client(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(JiraApiError):
        client.get_issue("PAY-1")


def test_non_json_success_becomes_typed_error() -> None:
    client, _ = make_client(lambda request: httpx.Response(200, text="upstream HTML"))
    with pytest.raises(JiraApiError):
        client.get_issue("PAY-1")


@pytest.mark.parametrize("status", [400, 500])
def test_error_response_does_not_echo_private_content(status: int) -> None:
    client, _ = make_client(
        lambda request: httpx.Response(
            status,
            json={"errorMessages": ["tok-test-123 private issue content"]},
        )
    )
    with pytest.raises(JiraApiError) as caught:
        client.get_issue("PAY-1")
    assert "tok-test-123" not in str(caught.value)
    assert "private issue content" not in str(caught.value)


@pytest.mark.parametrize("retry_after", ["invalid", "NaN", "-1"])
def test_invalid_retry_after_still_raises_rate_limit(retry_after: str) -> None:
    client, _ = make_client(
        lambda request: httpx.Response(
            429,
            headers={"Retry-After": retry_after},
        )
    )
    with pytest.raises(JiraRateLimitedError) as caught:
        client.get_issue("PAY-1")
    assert caught.value.retry_after is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"issues": None},
        {"issues": {}},
        {"issues": 1},
        {"issues": [], "nextPageToken": 123},
        {"issues": [], "isLast": False},
    ],
)
def test_malformed_search_page_is_not_reported_as_complete(payload: dict) -> None:
    client, _ = make_client(lambda request: ok(payload))
    with pytest.raises(JiraApiError):
        list(client.iter_search_jql("status = Open"))


# -- sprints (Week 2) ----------------------------------------------------------

SPRINT_PAYLOAD = {
    "id": 77,
    "name": "Payments R1",
    "state": "closed",
    "startDate": "2026-09-14T01:00:00.000Z",
    "endDate": "2026-09-27T01:00:00.000Z",
    "completeDate": "2026-09-27T02:00:00.000Z",
    "originBoardId": 42,
    "goal": "Stabilize checkout",
}


def sprint_page(values: list[dict], *, is_last: bool, start_at: int = 0) -> dict:
    return {
        "maxResults": 50,
        "startAt": start_at,
        "isLast": is_last,
        "total": start_at + len(values),
        "values": values,
    }


def test_get_sprint_parses_fields() -> None:
    client, _ = make_client(lambda request: ok(SPRINT_PAYLOAD))
    sprint = client.get_sprint(77)
    assert (sprint.id, sprint.name, sprint.state, sprint.origin_board_id) == (
        77,
        "Payments R1",
        "closed",
        42,
    )
    assert sprint.is_closed
    assert sprint.goal == "Stabilize checkout"
    assert sprint.complete_date is not None


def test_get_sprint_from_foreign_board_fails_closed() -> None:
    client, _ = make_client(lambda request: ok({**SPRINT_PAYLOAD, "originBoardId": 99}))
    with pytest.raises(JiraPermissionError):
        client.get_sprint(77)


@pytest.mark.parametrize("sprint_id", [0, -1])
def test_get_sprint_rejects_invalid_ids_before_http(sprint_id: int) -> None:
    client, requests = make_client(lambda request: ok(SPRINT_PAYLOAD))
    with pytest.raises(JiraApiError):
        client.get_sprint(sprint_id)
    assert requests == []


def test_iter_board_sprints_follows_start_at_pagination() -> None:
    pages = [
        sprint_page([SPRINT_PAYLOAD], is_last=False),
        sprint_page(
            [{**SPRINT_PAYLOAD, "id": 78, "name": "Payments R2", "state": "active"}],
            is_last=True,
            start_at=1,
        ),
    ]
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/board/42/sprint")
        calls.append(dict(request.url.params))
        return ok(pages[len(calls) - 1])

    client, _ = make_client(handler)
    sprints = list(client.iter_board_sprints(42))

    assert [sprint.id for sprint in sprints] == [77, 78]
    assert len(calls) == 2
    assert calls[0]["startAt"] == "0"
    assert calls[1]["startAt"] == "1"
    assert calls[0]["maxResults"] == "50"


def test_iter_board_sprints_sends_state_filter() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return ok(sprint_page([], is_last=True))

    client, _ = make_client(handler)
    list(client.iter_board_sprints(42, states=("active", "closed")))
    assert seen["state"] == "active,closed"


def test_iter_board_sprints_rejects_invalid_states_before_http() -> None:
    client, requests = make_client(lambda request: ok(sprint_page([], is_last=True)))
    with pytest.raises(JiraApiError, match="Invalid sprint states"):
        list(client.iter_board_sprints(42, states=("active", "deleted")))
    assert requests == []


def test_iter_board_sprints_enforces_page_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(sprint_page([SPRINT_PAYLOAD], is_last=False))

    client, _ = make_client(handler)
    with pytest.raises(JiraApiError, match="exceeded"):
        list(client.iter_board_sprints(42, max_pages=3))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"values": []},
        {"values": [], "isLast": False},
        {"values": [SPRINT_PAYLOAD], "isLast": "yes"},
        {"values": [{}], "isLast": True},
    ],
)
def test_malformed_sprint_page_is_not_reported_as_complete(payload: dict) -> None:
    client, _ = make_client(lambda request: ok(payload))
    with pytest.raises(JiraApiError):
        list(client.iter_board_sprints(42))


def test_iter_board_sprints_empty_page_with_is_last_true_completes() -> None:
    client, _ = make_client(lambda request: ok(sprint_page([], is_last=True)))
    assert list(client.iter_board_sprints(42)) == []


def test_iter_board_sprints_denies_foreign_board_sprint_in_listing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(sprint_page([{**SPRINT_PAYLOAD, "originBoardId": 99}], is_last=True))

    client, _ = make_client(handler)
    with pytest.raises(JiraPermissionError):
        list(client.iter_board_sprints(42))


def test_iter_board_sprints_blocks_other_board_before_http() -> None:
    client, requests = make_client(lambda request: ok(sprint_page([], is_last=True)))
    with pytest.raises(JiraPermissionError):
        list(client.iter_board_sprints(99))
    assert requests == []


def test_client_exposes_the_centralized_scope() -> None:
    from scrum_agent.auth import PilotScope

    client, _ = make_client(lambda request: ok(ISSUE_PAYLOAD))
    assert client.scope == PilotScope.from_settings(client._settings)
