"""Probe acceptance tests: real CLI/config/client, mocked HTTP only."""

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
