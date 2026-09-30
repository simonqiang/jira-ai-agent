"""Probe acceptance tests: real CLI/config/client, mocked HTTP only."""

import contextlib

import httpx
import pytest

from scrum_agent import app
from scrum_agent.jira.client import JiraClient
from tests.conftest import make_settings
from tests.test_jira_client import BOARD_CONFIGURATION_PAYLOAD, BOARD_PAYLOAD, ISSUE_PAYLOAD


def install_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    board_type: str = "scrum",
    configuration: dict | None = None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/configuration"):
            return httpx.Response(
                200, json=(BOARD_CONFIGURATION_PAYLOAD if configuration is None else configuration)
            )
        if request.url.path.endswith("/board/42"):
            return httpx.Response(200, json={**BOARD_PAYLOAD, "type": board_type})
        assert request.url.path.endswith("/issue/PAY-1")
        return httpx.Response(200, json=ISSUE_PAYLOAD)

    monkeypatch.setattr(app, "Settings", make_settings)
    monkeypatch.setattr(
        app,
        "JiraClient",
        lambda settings: JiraClient(
            settings,
            transport=httpx.MockTransport(handler),
        ),
    )


def test_probe_reports_week_one_demo_evidence_without_assignee(monkeypatch, capsys) -> None:
    install_probe(monkeypatch)
    assert app.main(["probe"]) == 0
    output = capsys.readouterr().out
    assert "https://test.atlassian.net/browse/PAY-1" in output
    assert "customfield_10002" in output
    assert "Story Points" in output
    assert "5, 6" in output
    assert "1001" in output
    assert "A. Developer" not in output


def test_probe_does_not_claim_success_for_kanban_board(monkeypatch, capsys) -> None:
    install_probe(monkeypatch, board_type="kanban")
    assert app.main([]) == 1
    captured = capsys.readouterr()
    assert "Probe OK" not in captured.out
    assert "scrum" in captured.err.lower()


def test_connection_failure_has_cli_exit_code_without_traceback(monkeypatch, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private detail", request=request)

    monkeypatch.setattr(app, "Settings", make_settings)
    monkeypatch.setattr(
        app,
        "JiraClient",
        lambda settings: JiraClient(
            settings,
            transport=httpx.MockTransport(handler),
        ),
    )
    assert app.main([]) == 1
    error = capsys.readouterr().err
    assert "private detail" not in error
    assert "Traceback" not in error


@pytest.mark.parametrize(
    "overrides",
    [
        {"columnConfig": {"columns": []}},
        {"estimation": {}},
        {"estimation": {"type": "field"}},
        {"filter": {}},
        {"id": 99},
    ],
)
def test_probe_does_not_claim_success_with_incomplete_board_evidence(
    monkeypatch,
    capsys,
    overrides,
) -> None:
    install_probe(monkeypatch, configuration={**BOARD_CONFIGURATION_PAYLOAD, **overrides})
    assert app.main([]) == 1
    assert "Probe OK" not in capsys.readouterr().out


@pytest.mark.parametrize("estimation_type", ["none", "issueCount"])
def test_probe_accepts_boards_without_an_estimate_field(
    monkeypatch, capsys, estimation_type
) -> None:
    install_probe(
        monkeypatch,
        configuration={
            **BOARD_CONFIGURATION_PAYLOAD,
            "estimation": {"type": estimation_type},
        },
    )
    assert app.main([]) == 0
    assert f"estimation={estimation_type}" in capsys.readouterr().out


# -- search and sprints (Week 2) ------------------------------------------------

SPRINTS_PAYLOAD = {
    "maxResults": 50,
    "startAt": 0,
    "isLast": True,
    "total": 2,
    "values": [
        {"id": 77, "name": "Payments R1", "state": "closed", "originBoardId": 42},
        {"id": 78, "name": "Payments R2", "state": "active", "originBoardId": 42},
    ],
}

SEARCH_BUG_PAGE = {
    "issues": [
        {
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
    ],
    "isLast": True,
}


def install_search(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    monkeypatch.setattr(app, "Settings", make_settings)
    monkeypatch.setattr(
        app,
        "JiraClient",
        lambda settings: JiraClient(
            settings,
            transport=httpx.MockTransport(handler),
        ),
    )


def search_handler(
    *,
    sprint_listing: dict = SPRINTS_PAYLOAD,
    search_page: dict = SEARCH_BUG_PAGE,
    seen_params: dict | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/board/42/sprint"):
            if seen_params is not None:
                seen_params.update(request.url.params)
            return httpx.Response(200, json=sprint_listing)
        if path.endswith("/sprint/78"):
            return httpx.Response(
                200,
                json={"id": 78, "name": "Payments R2", "state": "active", "originBoardId": 42},
            )
        if path == "/rest/api/3/search/jql":
            return httpx.Response(200, json=search_page)
        if path == "/rest/api/3/project/PAY/statuses":
            return httpx.Response(
                200,
                json=[{"issueType": {"name": "Bug"}, "statuses": [{"name": "Done"}]}],
            )
        assert path.endswith("/issue/PAY-1")
        return httpx.Response(200, json=ISSUE_PAYLOAD)

    return handler


def test_search_finds_unresolved_bugs_in_the_selected_sprint(monkeypatch, capsys) -> None:
    install_search(monkeypatch, search_handler())
    exit_code = app.main(["search", "--sprint", "Payments R2", "--type", "Bug", "--unresolved"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "PAY-1" in output
    assert "Double charge on checkout retry" in output
    assert "https://test.atlassian.net/browse/PAY-1" in output
    assert "Sprint: 78 Payments R2 [active]" in output
    assert "resolution IS EMPTY" in output
    assert "1 issue(s)." in output
    assert "A. Developer" not in output


def test_search_reports_zero_results_accurately(monkeypatch, capsys) -> None:
    install_search(monkeypatch, search_handler(search_page={"issues": [], "isLast": True}))
    assert app.main(["search", "--sprint", "78", "--type", "Epic"]) == 0
    output = capsys.readouterr().out
    assert "No issues match the query (checked: 0 issues)." in output


def test_search_ambiguous_sprint_lists_candidates_without_traceback(monkeypatch, capsys) -> None:
    duplicated = {
        **SPRINTS_PAYLOAD,
        "values": [
            {"id": 77, "name": "Payments", "state": "closed", "originBoardId": 42},
            {"id": 78, "name": "Payments", "state": "active", "originBoardId": 42},
        ],
    }
    install_search(monkeypatch, search_handler(sprint_listing=duplicated))
    assert app.main(["search", "--sprint", "Payments", "--unresolved"]) == 1
    captured = capsys.readouterr()
    assert "77: Payments [closed]" in captured.err
    assert "78: Payments [active]" in captured.err
    assert "Traceback" not in captured.err


def test_search_by_issue_key(monkeypatch, capsys) -> None:
    install_search(monkeypatch, search_handler())
    assert app.main(["search", "--issue", "PAY-1"]) == 0
    output = capsys.readouterr().out
    assert "PAY-1" in output
    assert "https://test.atlassian.net/browse/PAY-1" in output
    assert "Description: Repro steps" in output
    assert "Acceptance Criteria: No duplicate charge" in output
    assert "Subtasks:" in output
    assert "Linked Work Items:" in output
    assert "Severity: Critical" in output


def test_search_requires_issue_or_a_filter(monkeypatch, capsys) -> None:
    install_search(monkeypatch, search_handler())
    assert app.main(["search"]) == 2
    assert "at least one filter" in capsys.readouterr().err


def test_search_rejects_issue_combined_with_filters(monkeypatch, capsys) -> None:
    install_search(monkeypatch, search_handler())
    assert app.main(["search", "--issue", "PAY-1", "--unresolved"]) == 2
    assert "not both" in capsys.readouterr().err


def test_search_blank_filter_value_is_an_input_error(monkeypatch, capsys) -> None:
    install_search(monkeypatch, search_handler())
    assert app.main(["search", "--status", "  "]) == 2
    captured = capsys.readouterr()
    assert "Invalid search input" in captured.err
    assert "Traceback" not in captured.err


def test_sprints_command_lists_sprints(monkeypatch, capsys) -> None:
    seen: dict = {}
    install_search(monkeypatch, search_handler(seen_params=seen))
    assert app.main(["sprints"]) == 0
    output = capsys.readouterr().out
    assert "77: Payments R1 [closed]" in output
    assert "78: Payments R2 [active]" in output
    assert "2 sprint(s)." in output
    assert "state" not in seen  # no state filter by default


def test_sprints_command_filters_by_state(monkeypatch, capsys) -> None:
    seen: dict = {}
    install_search(monkeypatch, search_handler(seen_params=seen))
    assert app.main(["sprints", "--state", "active,closed"]) == 0
    assert seen["state"] == "active,closed"


def test_sprints_command_rejects_invalid_states(monkeypatch, capsys) -> None:
    install_search(monkeypatch, search_handler())
    assert app.main(["sprints", "--state", "deleted"]) == 1
    assert "Jira error" in capsys.readouterr().err


# -- Week 4 storage commands -----------------------------------------------------


def test_migrate_requires_database_url(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app, "Settings", make_settings)
    assert app.main(["migrate"]) == 2
    assert "SCRUM_AGENT_DATABASE_URL" in capsys.readouterr().err


def test_freshness_requires_database_url(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app, "Settings", make_settings)
    assert app.main(["freshness"]) == 2
    assert "SCRUM_AGENT_DATABASE_URL" in capsys.readouterr().err


def test_collect_requires_database_url(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app, "Settings", make_settings)
    assert app.main(["collect"]) == 2
    assert "SCRUM_AGENT_DATABASE_URL" in capsys.readouterr().err


def _install_storage(monkeypatch, storage) -> None:
    @contextlib.contextmanager
    def fake_connect(settings):
        yield object()

    monkeypatch.setattr("scrum_agent.storage.db.connect", fake_connect)
    monkeypatch.setattr("scrum_agent.storage.repository.PgStorage", lambda conn: storage)


def _install_collector_jira(monkeypatch, jira) -> None:
    monkeypatch.setattr(
        app,
        "JiraClient",
        lambda settings: JiraClient(settings, transport=httpx.MockTransport(jira.handler)),
    )


def test_collect_runs_collector_and_prints_freshness(monkeypatch, capsys) -> None:
    from tests.checked_queries import FakeJira
    from tests.storage_fakes import InMemoryStorage

    storage = InMemoryStorage()
    monkeypatch.setattr(
        app, "Settings", lambda: make_settings(database_url="postgresql://db/scrum_agent")
    )
    _install_collector_jira(monkeypatch, FakeJira())
    _install_storage(monkeypatch, storage)

    assert app.main(["collect"]) == 0
    out = capsys.readouterr().out
    assert "Collected 6 issue(s)" in out
    assert "7 Jira page(s)" in out
    assert "never succeeded" not in out
    assert len(storage.snapshots) == 6
    assert storage.checkpoints["issues"]


def test_collect_surfaces_expired_credential_alarm(monkeypatch, capsys) -> None:
    from tests.checked_queries import FakeJira
    from tests.storage_fakes import InMemoryStorage

    monkeypatch.setattr(
        app,
        "Settings",
        lambda: make_settings(
            database_url="postgresql://db/scrum_agent", token_expires_on="2020-01-01"
        ),
    )
    _install_collector_jira(monkeypatch, FakeJira())
    _install_storage(monkeypatch, InMemoryStorage())

    assert app.main(["collect"]) == 0  # collection itself succeeded
    assert "ALARM" in capsys.readouterr().out


def test_freshness_exits_nonzero_on_alarms(monkeypatch, capsys) -> None:
    from tests.storage_fakes import InMemoryStorage

    monkeypatch.setattr(
        app, "Settings", lambda: make_settings(database_url="postgresql://db/scrum_agent")
    )
    _install_storage(monkeypatch, InMemoryStorage())

    assert app.main(["freshness"]) == 1
    captured = capsys.readouterr()
    assert "never succeeded" in captured.out
    assert "Traceback" not in captured.err


def test_freshness_exits_zero_when_healthy(monkeypatch, capsys) -> None:
    from datetime import UTC, datetime

    from tests.storage_fakes import InMemoryStorage

    now = datetime.now(UTC)
    storage = InMemoryStorage(now=lambda: now)
    run_id = storage.start_run()
    storage.finish_run(run_id, status="success")
    monkeypatch.setattr(
        app, "Settings", lambda: make_settings(database_url="postgresql://db/scrum_agent")
    )
    _install_storage(monkeypatch, storage)

    assert app.main(["freshness"]) == 0
    assert "Credential: unknown" in capsys.readouterr().out
