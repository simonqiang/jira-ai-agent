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
    "columnConfig": {
        "columns": [
            {
                "name": "To Do",
                "statuses": [{"name": "Backlog"}, {"name": "Selected for Development"}],
            },
            {"name": "In Progress", "statuses": [{"name": "In Progress"}]},
            {"name": "Done", "statuses": [{"name": "Done"}, {"name": "Closed"}]},
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
    assert mapping["Done"] == ("Done", "Closed")


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
