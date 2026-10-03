"""Jira client tests against a mocked transport (no live credentials)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from scrum_agent.jira.client import JiraClient, _as_jira_fields
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
        "reporter": {"displayName": "P. Manager"},
        "labels": ["payments", "customer-impact"],
        "duedate": "2026-10-15",
        "priority": {"name": "High"},
        "description": {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Repro steps"}]}
            ],
        },
        "customfield_10350": {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "No duplicate charge"}]}
            ],
        },
        "subtasks": [
            {
                "key": "PAY-7",
                "fields": {
                    "summary": "Add regression test",
                    "status": {"name": "To Do"},
                    "priority": {"name": "Medium"},
                },
            }
        ],
        "issuelinks": [
            {
                "type": {"name": "Blocks", "outward": "blocks"},
                "outwardIssue": {
                    "key": "PAY-8",
                    "fields": {"summary": "Release validation", "status": {"name": "To Do"}},
                },
            }
        ],
        "customfield_10199": {"value": "Critical"},
        "customfield_10263": {"value": "High"},
        "customfield_10249": {"value": "4"},
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
    assert issue.description == "Repro steps"
    assert issue.acceptance_criteria == "No duplicate charge"
    assert issue.reporter == "P. Manager"
    assert issue.labels == ("payments", "customer-impact")
    assert issue.due_date == "2026-10-15"
    assert issue.severity == "Critical"
    assert issue.risk_rating == "High"
    assert issue.issue_rating == "4"
    assert issue.priority == "High"
    assert issue.subtasks[0].key == "PAY-7"
    assert issue.linked_work_items[0].relationship == "blocks"


def test_authorization_header_sent_basic_site() -> None:
    client, requests = make_client(lambda request: ok(ISSUE_PAYLOAD))
    client.get_issue("PAY-1")
    expected = base64.b64encode(b"sm@test.example:tok-test-123").decode()
    assert requests[0].headers["Authorization"] == f"Basic {expected}"
    assert requests[0].url.host == "test.atlassian.net"
    fields = set(requests[0].url.params["fields"].split(","))
    assert {
        "description",
        "customfield_10350",
        "subtasks",
        "issuelinks",
        "reporter",
        "labels",
        "duedate",
        "customfield_10199",
        "customfield_10263",
        "customfield_10249",
        "priority",
    } <= fields


@pytest.mark.parametrize("auth_mode", ["basic_central", "bearer_central"])
def test_central_request_preserves_cloud_id_prefix_and_auth(auth_mode: str) -> None:
    client, requests = make_client(
        lambda request: ok(ISSUE_PAYLOAD),
        jira_auth_mode=auth_mode,
        jira_cloud_id="test-cloud-id",
    )
    client.get_issue("PAY-1")
    assert str(requests[0].url).startswith(
        "https://api.atlassian.com/ex/jira/test-cloud-id/rest/api/3/issue/PAY-1?"
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


def board_then_sprint_handler(sprint_payload: dict, listed_ids: list[int]):
    """Serve the board listing (scope authority) then the sprint detail."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/board/42/sprint"):
            values = [{**SPRINT_PAYLOAD, "id": sprint_id} for sprint_id in listed_ids]
            return ok(sprint_page(values, is_last=True))
        assert request.url.path.endswith("/sprint/77")
        return ok(sprint_payload)

    return handler


def test_get_sprint_parses_fields() -> None:
    client, _ = make_client(board_then_sprint_handler(SPRINT_PAYLOAD, [77]))
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


def test_get_sprint_accepts_foreign_origin_listed_by_the_board() -> None:
    # Real boards list sprints originating on another board; the listing is
    # the scope authority, not originBoardId.
    client, requests = make_client(
        board_then_sprint_handler({**SPRINT_PAYLOAD, "originBoardId": 99}, [77])
    )
    assert client.get_sprint(77).id == 77
    assert [request.url.path for request in requests].count("/rest/agile/1.0/sprint/77") == 1


def test_get_sprint_not_listed_by_the_board_fails_closed() -> None:
    client, requests = make_client(board_then_sprint_handler(SPRINT_PAYLOAD, [78]))
    with pytest.raises(JiraPermissionError):
        client.get_sprint(77)
    assert all("/sprint/77" not in request.url.path for request in requests)


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


def test_iter_board_sprints_accepts_foreign_origin_sprints() -> None:
    # The pilot's real board lists sprints originating on another board; the
    # configured board's own endpoint is the scope authority for sprints.
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(sprint_page([{**SPRINT_PAYLOAD, "originBoardId": 99}], is_last=True))

    client, _ = make_client(handler)
    assert [sprint.id for sprint in client.iter_board_sprints(42)] == [77]


def test_iter_board_sprints_blocks_other_board_before_http() -> None:
    client, requests = make_client(lambda request: ok(sprint_page([], is_last=True)))
    with pytest.raises(JiraPermissionError):
        list(client.iter_board_sprints(99))
    assert requests == []


def test_client_exposes_the_centralized_scope() -> None:
    from scrum_agent.auth import PilotScope

    client, _ = make_client(lambda request: ok(ISSUE_PAYLOAD))
    assert client.scope == PilotScope.from_settings(client._settings)


CHANGELOG_ENTRY_PAYLOAD = {
    "id": "10050",
    "created": "2026-09-20T10:30:00.000+0000",
    "author": {"displayName": "A. Developer"},
    "items": [
        {
            "field": "status",
            "fieldtype": "jira",
            "fieldId": "status",
            "from": "10001",
            "fromString": "To Do",
            "to": "10002",
            "toString": "In Progress",
        },
        {
            "field": "Story Points",
            "fieldId": "customfield_10002",
            "from": None,
            "fromString": None,
            "to": "5",
            "toString": "5",
        },
    ],
}


def test_get_issue_detail_returns_raw_payload_with_curated_fields() -> None:
    client, requests = make_client(lambda request: ok(ISSUE_PAYLOAD))
    payload = client.get_issue_detail("PAY-1", extra_fields=("customfield_10002",))
    assert payload["key"] == "PAY-1"
    fields_param = dict(requests[0].url.params)["fields"]
    assert "summary" in fields_param.split(",")
    assert "customfield_10002" in fields_param.split(",")


def test_get_issue_detail_denies_out_of_scope_key_without_http() -> None:
    client, requests = make_client(lambda request: ok(ISSUE_PAYLOAD))
    with pytest.raises(JiraPermissionError):
        client.get_issue_detail("OTHER-1")
    assert requests == []


def test_get_issue_detail_rejects_payload_without_key() -> None:
    client, _ = make_client(lambda request: ok({"id": "10001"}))
    with pytest.raises(JiraApiError):
        client.get_issue_detail("PAY-1")


def test_get_issue_detail_rechecks_moved_issue_key() -> None:
    moved = {**ISSUE_PAYLOAD, "key": "OTHER-9"}
    client, _ = make_client(lambda request: ok(moved))
    with pytest.raises(JiraPermissionError):
        client.get_issue_detail("PAY-1")


def test_iter_issue_changelog_follows_start_at_pages() -> None:
    first = {
        "startAt": 0,
        "maxResults": 1,
        "total": 2,
        "isLast": False,
        "values": [CHANGELOG_ENTRY_PAYLOAD],
    }
    second = {
        "startAt": 1,
        "maxResults": 1,
        "total": 2,
        "isLast": True,
        "values": [
            {
                **CHANGELOG_ENTRY_PAYLOAD,
                "id": "10051",
                "created": "2026-09-21T09:00:00.000+0000",
                "items": [
                    {
                        "field": "Sprint",
                        "from": None,
                        "fromString": None,
                        "to": "3001",
                        "toString": "Sprint 8",
                    }
                ],
            }
        ],
    }
    pages = {0: first, 1: second}

    def handler(request: httpx.Request) -> httpx.Response:
        start_at = int(request.url.params["startAt"])
        return ok(pages[start_at])

    client, requests = make_client(handler)
    entries = list(client.iter_issue_changelog("PAY-1", max_results_per_page=1))
    assert [entry.id for entry in entries] == ["10050", "10051"]
    first_entry, second_entry = entries
    assert first_entry.author == "A. Developer"
    assert first_entry.items[0].field == "status"
    assert first_entry.items[0].from_value == "To Do"
    assert first_entry.items[1].to_id == "5"
    assert second_entry.items[0].field == "Sprint"
    assert first_entry.created.isoformat() == "2026-09-20T10:30:00+00:00"
    start_values = [int(r.url.params["startAt"]) for r in requests]
    assert start_values == [0, 1]


@pytest.mark.parametrize(
    "page",
    [
        {},
        {"values": [], "isLast": False, "startAt": 0},  # incomplete page without values
        {"values": [CHANGELOG_ENTRY_PAYLOAD], "isLast": "yes", "startAt": 0},
    ],
)
def test_iter_issue_changelog_fails_closed_on_malformed_pages(page: dict) -> None:
    client, _ = make_client(lambda request: ok(page))
    with pytest.raises(JiraApiError):
        list(client.iter_issue_changelog("PAY-1"))


def test_iter_issue_changelog_fails_loudly_at_page_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(
            {
                "startAt": 0,
                "maxResults": 1,
                "total": 999,
                "isLast": False,
                "values": [CHANGELOG_ENTRY_PAYLOAD],
            }
        )

    client, _ = make_client(handler)
    with pytest.raises(JiraApiError, match="exceeded 2 pages"):
        list(client.iter_issue_changelog("PAY-1", max_results_per_page=1, max_pages=2))


def test_iter_issue_changelog_denies_out_of_scope_key_without_http() -> None:
    client, requests = make_client(lambda request: ok({}))
    with pytest.raises(JiraPermissionError):
        list(client.iter_issue_changelog("OTHER-1"))
    assert requests == []


def test_iter_issue_changelog_surfaces_rate_limit_without_retry() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(429, json={}, headers={"Retry-After": "7"})
    )
    with pytest.raises(JiraRateLimitedError) as caught:
        list(client.iter_issue_changelog("PAY-1"))
    assert caught.value.retry_after == 7.0


# -- approved ticket creation (Week 8) ---------------------------------------------

CREATE_FIELD_IDS = ("project", "issuetype", "summary", "description", "labels")


def createmeta_handler(created: list[dict]):
    """Serve create metadata for Bug plus a POST create route recording payloads."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/rest/api/3/issue":
            created.append(json.loads(request.content)["fields"])
            return ok({"id": "10090", "key": "PAY-90"})
        if path == "/rest/api/3/issue/createmeta/PAY/issuetypes":
            return ok({"values": [{"id": "10001", "name": "Bug"}]})
        if path == "/rest/api/3/issue/createmeta/PAY/issuetypes/10001":
            return ok({"fields": [{"fieldId": field_id} for field_id in CREATE_FIELD_IDS]})
        raise AssertionError(f"unexpected request {path}")

    return handler


def ticket_payload(**overrides: object) -> dict:
    payload: dict = {
        "project": {"key": "PAY"},
        "issuetype": {"name": "Bug"},
        "summary": "Export fails",
        "description": "Steps to reproduce.",
        "labels": ["scrum-agent-req-00000000-0000-0000-0000-000000000000"],
    }
    payload.update(overrides)
    return payload


def test_create_issue_posts_exact_fields_and_returns_identity() -> None:
    created: list[dict] = []
    client, requests = make_client(createmeta_handler(created))
    payload = ticket_payload()

    assert client.create_issue(payload) == {"id": "10090", "key": "PAY-90"}
    assert created == [_as_jira_fields(payload)]
    assert json.loads(requests[0].content) == {"fields": _as_jira_fields(payload)}


def test_create_issue_rejects_response_without_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"id": "10090"})  # key omitted

    with pytest.raises(JiraApiError, match="identity"):
        make_client(handler)[0].create_issue(ticket_payload())


def test_validate_create_fields_blocks_foreign_project_before_http() -> None:
    client, requests = make_client(lambda request: ok({}))

    with pytest.raises(JiraApiError, match="unexpected project"):
        client.validate_create_fields("Bug", ticket_payload(project={"key": "OTHER"}))
    assert requests == []


def test_validate_create_fields_accepts_permitted_metadata() -> None:
    client, requests = make_client(createmeta_handler([]))

    client.validate_create_fields("Bug", ticket_payload())
    assert [request.url.path for request in requests] == [
        "/rest/api/3/issue/createmeta/PAY/issuetypes",
        "/rest/api/3/issue/createmeta/PAY/issuetypes/10001",
    ]


def test_validate_create_fields_rejects_unavailable_issue_type() -> None:
    client, requests = make_client(createmeta_handler([]))

    with pytest.raises(JiraApiError, match="issue type 'Task'"):
        client.validate_create_fields("Task", ticket_payload())
    assert len(requests) == 1  # no field metadata fetched for a missing type


def test_validate_create_fields_rejects_metadata_missing_required_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issuetypes"):
            return ok({"values": [{"id": "10001", "name": "Bug"}]})
        return ok({"fields": [{"fieldId": field_id} for field_id in CREATE_FIELD_IDS[:4]]})

    with pytest.raises(JiraApiError, match="labels"):
        make_client(handler)[0].validate_create_fields("Bug", ticket_payload())


def test_find_by_marker_returns_single_match() -> None:
    issue = {**ISSUE_PAYLOAD, "id": "10090", "key": "PAY-90"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/search/jql"
        return ok({"issues": [issue], "nextPageToken": None})

    marker = "scrum-agent-req-00000000-0000-0000-0000-000000000000"
    assert make_client(handler)[0].find_by_marker(marker) == {"id": "10090", "key": "PAY-90"}


def test_find_by_marker_rejects_malformed_marker_before_http() -> None:
    client, requests = make_client(lambda request: ok({"issues": []}))

    with pytest.raises(JiraApiError, match="marker"):
        client.find_by_marker('labels-injection" OR key IS NOT EMPTY')
    assert requests == []


def test_find_by_marker_refuses_ambiguous_matches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(
            {
                "issues": [
                    ISSUE_PAYLOAD,
                    {**ISSUE_PAYLOAD, "key": "PAY-2", "id": "10002"},
                ],
                "nextPageToken": None,
            }
        )

    marker = "scrum-agent-req-00000000-0000-0000-0000-000000000000"
    with pytest.raises(JiraApiError, match="multiple issues"):
        make_client(handler)[0].find_by_marker(marker)


def test_update_issue_puts_exact_fields_and_accepts_empty_response() -> None:
    fields = {"customfield_10350": "Given, When, Then."}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/rest/api/3/issue/PAY-3"
        assert json.loads(request.content) == {"fields": _as_jira_fields(fields)}
        return httpx.Response(204)

    client, requests = make_client(handler)
    client.update_issue("PAY-3", fields)
    assert len(requests) == 1


def test_update_issue_rejects_unexpected_response_body() -> None:
    with pytest.raises(JiraApiError, match="unexpected response body"):
        make_client(lambda request: ok({"ignored": True}))[0].update_issue(
            "PAY-3", {"summary": "x"}
        )


def test_update_issue_refuses_out_of_scope_key_before_http() -> None:
    client, requests = make_client(lambda request: httpx.Response(204))

    with pytest.raises(JiraPermissionError):
        client.update_issue("OTHER-1", {"summary": "x"})
    assert requests == []


# -- ADF text-panel fields (description etc.) --------------------------------------


def test_adf_text_round_trips_through_jira_text() -> None:
    from scrum_agent.jira.models import adf_text, jira_text

    for text in (
        "Single line",
        "Two\nparagraphs",
        "Blank\n\nline\n\nbetween",
    ):
        assert jira_text(adf_text(text)) == text.strip(), repr(text)


def test_update_issue_converts_description_to_adf() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client, requests = make_client(handler)
    client.update_issue("PAY-3", {"description": "First\n\nSecond", "summary": "Keep plain"})

    body = json.loads(requests[0].content)
    sent = body["fields"]
    assert sent["summary"] == "Keep plain"  # plain string stays plain
    assert sent["description"]["type"] == "doc"
    assert [p["content"] for p in sent["description"]["content"]] == [
        [{"type": "text", "text": "First"}],
        [],
        [{"type": "text", "text": "Second"}],
    ]


def test_create_issue_converts_description_to_adf() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"id": "1", "key": "PAY-9"})

    client, requests = make_client(handler)
    client.create_issue({"summary": "s", "description": "line one\nline two"})

    sent = json.loads(requests[0].content)["fields"]
    assert sent["description"]["type"] == "doc"
    assert len(sent["description"]["content"]) == 2


def test_find_assignable_parses_account_and_display_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/user/assignable/search"
        assert request.url.params["issueKey"] == "PAY-3"
        return ok([{"accountId": "acc-1", "displayName": "A. Developer"}, {"displayName": "no-id"}])

    client, _ = make_client(handler)
    users = client.find_assignable("PAY-3", "dev")
    assert users == [{"account_id": "acc-1", "display_name": "A. Developer"}]
