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
