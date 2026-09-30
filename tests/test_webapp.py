"""Web UI tests: chat flow, loopback enforcement, escaping and isolation.

The FastAPI app is driven in-process via httpx's ASGITransport over the
fixture Jira and a scripted model; no sockets are opened.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from scrum_agent.web import create_app
from tests.agent_fakes import ScriptedStep, make_chat, tool_call
from tests.conftest import make_settings

BASE = "http://127.0.0.1"


def make_app(steps=None, jira=None):
    chat, jira, fake = make_chat(jira=jira, steps=steps or [])
    app = create_app(make_settings(), chat)
    return app, chat, jira, fake


def client_for(app) -> TestClient:
    return TestClient(app, base_url=BASE, follow_redirects=False)


# -- chat page and turn flow ------------------------------------------------------


def test_index_sets_session_cookie_and_renders_scope() -> None:
    app, *_ = make_app()
    with client_for(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "scrum_agent_session" in response.cookies
    assert "project PAY" in response.text
    assert "board 42" in response.text
    assert "tok-test-123" not in response.text


def test_chat_turn_renders_linked_answer_sources_and_usage() -> None:
    app, chat, *_ = make_app(
        steps=[
            ScriptedStep(tool_calls=(tool_call("get_issue", issue_key="PAY-3"),)),
            ScriptedStep(text="PAY-3 is the Statement export story."),
        ]
    )
    with client_for(app) as client:
        client.get("/")
        response = client.post("/chat", data={"message": "What is PAY-3?"})
    assert response.status_code == 200
    assert 'href="https://test.atlassian.net/browse/PAY-3"' in response.text
    assert "Sources" in response.text
    assert "2 model calls" in response.text  # tool use requires a second model response
    assert "prompt /" in response.text


def test_chat_turn_escapes_markup_in_answers() -> None:
    app, *_ = make_app(
        steps=[
            ScriptedStep(text="<script>alert('PAY-1')</script> and PAY-2 are <b>fine</b>."),
        ]
    )
    with client_for(app) as client:
        client.get("/")
        response = client.post("/chat", data={"message": "show me things"})
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_chat_turn_rejects_blank_and_overlong_messages() -> None:
    app, *_ = make_app()
    with client_for(app) as client:
        client.get("/")
        assert client.post("/chat", data={"message": "   "}).status_code == 400
        assert client.post("/chat", data={"message": "x" * 2001}).status_code == 400


def test_chat_turn_without_known_session_is_rejected() -> None:
    app, *_ = make_app()
    with client_for(app) as client:  # no GET / first: no session cookie
        response = client.post("/chat", data={"message": "hello"})
    assert response.status_code == 400


def test_reset_redirects_and_clears_conversation() -> None:
    app, chat, *_ = make_app(steps=[ScriptedStep(text="One answer.")])
    with client_for(app) as client:
        client.get("/")
        client.post("/chat", data={"message": "hi"})
        response = client.post("/reset")
        assert response.status_code == 303
        assert response.headers["HX-Redirect"] == "/"
        page = client.get("/")
        assert "One answer." not in page.text


def test_healthz_reports_ok_without_settings() -> None:
    app, *_ = make_app()
    with client_for(app) as client:
        response = client.get("/healthz")
    assert response.json() == {"status": "ok"}
    assert "tok-test-123" not in response.text


def test_two_cookies_get_isolated_conversations() -> None:
    steps = [ScriptedStep(text="Private answer about PAY-1.")]
    app, *_ = make_app(steps=steps)
    with client_for(app) as first, client_for(app) as second:
        first.get("/")
        first.post("/chat", data={"message": "secret question"})
        second.get("/")
        page = second.get("/")
        assert "Private answer" not in page.text
        assert "secret question" not in page.text


# -- loopback enforcement ---------------------------------------------------------


def test_post_with_foreign_host_is_rejected() -> None:
    app, *_ = make_app()
    with client_for(app) as client:
        client.get("/")
        response = client.post("/chat", data={"message": "hi"}, headers={"Host": "evil.example"})
    assert response.status_code == 403


def test_post_with_foreign_origin_is_rejected() -> None:
    app, *_ = make_app()
    with client_for(app) as client:
        client.get("/")
        response = client.post(
            "/chat",
            data={"message": "hi"},
            headers={"Origin": "http://evil.example"},
        )
    assert response.status_code == 403


def test_post_with_loopback_origin_is_allowed() -> None:
    app, *_ = make_app(steps=[ScriptedStep(text="ok")])
    with client_for(app) as client:
        client.get("/")
        response = client.post(
            "/chat",
            data={"message": "hi"},
            headers={"Origin": "http://127.0.0.1"},
        )
    assert response.status_code == 200


# -- settings guardrails ----------------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com"])
def test_web_host_must_be_loopback(host: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(web_host=host)


def test_web_host_accepts_ipv6_loopback() -> None:
    assert make_settings(web_host="::1").web_host == "::1"


@pytest.mark.parametrize(
    "url", ["http://api.z.ai/api/anthropic", "https://api.z.ai/?x=1", "https://x/"]
)
def test_model_base_url_must_be_clean_https(url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(model_base_url=url)


# -- reports pages (Week 5) -------------------------------------------------------

# These use a plain TestClient (no `with`), so the app lifespan — and its
# background worker — never starts; the test drives the worker manually.


def make_report_app():
    import httpx

    from scrum_agent.jira.client import JiraClient
    from scrum_agent.reports.jobs import ReportJobs
    from tests.checked_queries import FakeJira
    from tests.test_reports import collect_into_storage

    chat, jira, _ = make_chat()
    storage = collect_into_storage(FakeJira())
    db_settings = make_settings(database_url="postgresql://localhost/scrum_agent")
    client = JiraClient(db_settings, transport=httpx.MockTransport(FakeJira().handler))
    jobs = ReportJobs(db_settings, client=client, storage=storage)
    app = create_app(make_settings(), chat, jobs)
    return app, jobs, storage


def test_reports_page_lists_board_sprints() -> None:
    app, *_ = make_report_app()
    client = client_for(app)
    response = client.get("/reports")
    assert response.status_code == 200
    assert "Payments R2" in response.text
    assert 'value="78"' in response.text


def test_submit_view_and_export_a_report() -> None:
    app, jobs, _ = make_report_app()
    client = client_for(app)
    submitted = client.post("/reports", data={"sprint": "78"})
    assert submitted.status_code == 303
    assert submitted.headers["location"] == "/reports/1"

    pending = client.get("/reports/1")
    assert "queued" in pending.text

    jobs.run_once()
    done = client.get("/reports/1")
    assert "Payments R2" in done.text
    assert "Estimates total" in done.text
    assert "complete" in done.text
    assert "/reports/1/report.md" in done.text
    assert "/reports/1/report.csv" in done.text

    markdown = client.get("/reports/1/report.md")
    assert markdown.status_code == 200
    assert "text/markdown" in markdown.headers["content-type"]
    assert "Sprint report: Payments R2" in markdown.text
    csv_export = client.get("/reports/1/report.csv")
    assert "text/csv" in csv_export.headers["content-type"]
    assert "Scope (current),3" in csv_export.text
    assert client.get("/reports/1/report.xml").status_code == 404


def test_report_export_before_completion_is_404() -> None:
    app, jobs, _ = make_report_app()
    client = client_for(app)
    client.post("/reports", data={"sprint": "78"})
    assert client.get("/reports/1/report.md").status_code == 404


def test_ambiguous_sprint_submission_returns_to_form_with_error() -> None:
    app, *_ = make_report_app()
    client = client_for(app)
    response = client.post("/reports", data={"sprint": "Payments"})
    assert response.status_code == 303
    assert "matches%203%20sprints" in response.headers["location"]


def test_reports_without_jobs_redirect_with_error() -> None:
    chat, *_ = make_chat()
    app = create_app(make_settings(), chat)
    client = client_for(app)
    response = client.post("/reports", data={"sprint": "78"})
    assert response.status_code == 303
    assert "database" in response.headers["location"]


def test_report_view_escapes_untrusted_issue_text() -> None:
    import dataclasses

    import httpx

    from scrum_agent.jira.client import JiraClient
    from scrum_agent.reports.jobs import ReportJobs
    from tests.checked_queries import DEFAULT_ISSUES, FakeJira
    from tests.test_reports import collect_into_storage

    evil = dataclasses.replace(DEFAULT_ISSUES[0], summary="<script>alert(1)</script>")
    jira = FakeJira(issues=(evil, *DEFAULT_ISSUES[1:]))
    chat, _, _ = make_chat()
    storage = collect_into_storage(jira)
    db_settings = make_settings(database_url="postgresql://localhost/scrum_agent")
    client = JiraClient(db_settings, transport=httpx.MockTransport(jira.handler))
    jobs = ReportJobs(db_settings, client=client, storage=storage)
    app = create_app(make_settings(), chat, jobs)
    web = client_for(app)
    web.post("/reports", data={"sprint": "78"})
    jobs.run_once()
    page = web.get("/reports/1")
    assert "<script>" not in page.text
    assert "&lt;script&gt;" in page.text
