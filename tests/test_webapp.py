"""Web UI tests: chat flow, loopback enforcement, escaping and isolation.

The FastAPI app is driven in-process via httpx's ASGITransport over the
fixture Jira and a scripted model; no sockets are opened.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from scrum_agent.web import create_app
from tests.agent_fakes import ScriptedStep, make_chat, tool_call
from tests.checked_queries import FakeJira
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


def test_chat_turn_renders_markdown_issue_table_accessibly() -> None:
    table = (
        "Current sprint: **2025 CDCP&CMIR S19** (active) — 2 tickets:\n\n"
        "| Key | Type | Status | Summary |\n"
        "|---|---|---|---|\n"
        "| GACD-40 | Story | New | Access Database Data via User-Friendly Portal |\n"
        "| GACD-30 | Bug | New | As a user, I want to reset my password via email link "
        "so that I can regain access if I forget it <script>alert(1)</script> |"
    )
    settings = make_settings(jira_project_key="GACD", known_issue_key="GACD-1")
    chat, *_ = make_chat(settings=settings, steps=[ScriptedStep(text=table)])
    app = create_app(settings, chat)
    with client_for(app) as client:
        client.get("/")
        response = client.post("/chat", data={"message": "show sprint issues"})

    assert '<div class="answer-table-wrap">' in response.text
    assert '<th class="cell-key" scope="col">Key</th>' in response.text
    assert '<th class="cell-summary" scope="col">Summary</th>' in response.text
    assert '<th class="cell-type" scope="col">Type</th>' in response.text
    assert '<col class="col-key">' in response.text
    assert '<col class="col-type">' in response.text
    assert "<strong>2025 CDCP&amp;CMIR S19</strong>" in response.text
    assert "**2025" not in response.text
    assert 'href="https://test.atlassian.net/browse/GACD-40"' in response.text
    assert "GACD-30" in response.text
    assert "regain access if I forget it" in response.text
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


# -- approved ticket creation (Week 8) ---------------------------------------------


class TicketJira(FakeJira):
    """Fixture Jira plus the create-metadata and create-issue routes."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/rest/api/3/issue":
            self.created_fields.append(json.loads(request.content)["fields"])
            return httpx.Response(200, json={"id": "10090", "key": "PAY-90"})
        if request.method == "GET" and path == "/rest/api/3/issue/PAY-90":
            created = dict(self.issues[0].payload())  # verify step reads it back
            created["id"], created["key"] = "10090", "PAY-90"
            return httpx.Response(200, json=created)
        if path == "/rest/api/3/issue/createmeta/PAY/issuetypes":
            return httpx.Response(200, json={"values": [{"id": "10001", "name": "Bug"}]})
        if path == "/rest/api/3/issue/createmeta/PAY/issuetypes/10001":
            return httpx.Response(
                200,
                json={
                    "fields": [
                        {"fieldId": field_id}
                        for field_id in ("project", "issuetype", "summary", "description", "labels")
                    ]
                },
            )
        return super().handler(request)


class FakeTicketJobs:
    def __init__(self, storage) -> None:
        self._storage = storage

    def storage(self):
        return self._storage


def make_ticket_app():
    from tests.test_ticketing import MemoryStorage

    jira = TicketJira()
    jira.created_fields = []
    chat, _, _ = make_chat(jira=jira)
    storage = MemoryStorage()
    app = create_app(make_settings(), chat, FakeTicketJobs(storage))
    return app, storage, jira


def test_approved_ticket_flow_creates_exactly_once() -> None:
    from tests.test_ticketing import complete_bug

    app, storage, jira = make_ticket_app()
    client = client_for(app)
    client.get("/")  # establish the approval session

    draft = client.post("/tickets/drafts", json={"issue_type": "Bug", "fields": complete_bug()})
    assert draft.status_code == 200
    body = draft.json()
    assert body["payload"]["project"] == {"key": "PAY"}
    assert body["payload"]["summary"] == "Export fails"
    assert body["payload"]["labels"][0].startswith("scrum-agent-req-")
    assert body["payload_hash"]

    approval = client.post(f"/tickets/drafts/{body['id']}/approve")
    assert approval.status_code == 200
    assert "expires_at" in approval.json()

    executed = client.post(f"/tickets/approvals/{approval.json()['id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["issue_key"] == "PAY-90"
    assert jira.created_fields == [body["payload"]]  # exact approved bytes

    again = client.post(f"/tickets/approvals/{approval.json()['id']}/execute")
    assert again.json() == executed.json()
    assert len(jira.created_fields) == 1  # duplicate click created nothing
    assert storage.executions[executed.json()["execution_id"]]["status"] == "succeeded"


def test_ticket_endpoints_require_the_approval_session() -> None:
    from tests.test_ticketing import complete_bug

    app, *_ = make_ticket_app()
    client = client_for(app)  # no visit to "/": no approval session
    assert (
        client.post(
            "/tickets/drafts", json={"issue_type": "Bug", "fields": complete_bug()}
        ).status_code
        == 403
    )
    assert client.post("/tickets/drafts/1/approve").status_code == 403
    assert client.post("/tickets/approvals/1/execute").status_code == 403


def test_ticket_endpoints_without_database_return_409() -> None:
    app, *_ = make_app()
    client = client_for(app)
    client.get("/")
    assert (
        client.post("/tickets/drafts", json={"issue_type": "Bug", "fields": {}}).status_code == 409
    )
    assert client.post("/tickets/drafts/1/approve").status_code == 409
    assert client.post("/tickets/approvals/1/execute").status_code == 409


def test_create_ticket_draft_rejects_bad_bodies() -> None:
    app, *_ = make_ticket_app()
    client = client_for(app)
    client.get("/")
    base = "/tickets/drafts"
    assert (
        client.post(base, content=b"{", headers={"content-type": "application/json"}).status_code
        == 400
    )
    assert client.post(base, json="not-a-dict").status_code == 400
    assert (
        client.post(base, json={"issue_type": "Bug", "fields": {"summary": 5}}).status_code == 400
    )
    assert (
        client.post(base, json={"issue_type": "Task", "fields": {"summary": "x"}}).status_code
        == 400
    )
    assert (
        client.post(
            base, json={"issue_type": "Bug", "fields": {"summary": "x" * 10_001}}
        ).status_code
        == 400
    )


# -- reviewed ticket updates (Week 9) -----------------------------------------------


class UpdateJira(FakeJira):
    """Fixture Jira plus the issue-update route; reads see every applied write."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.update_calls: list[tuple[str, dict]] = []
        self.applied: dict[str, dict] = {}  # key -> fields a write set
        self.fail_put = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "PUT" and path.startswith("/rest/api/3/issue/"):
            key = path.rsplit("/", 1)[-1]
            fields = json.loads(request.content)["fields"]
            self.update_calls.append((key, fields))
            if self.fail_put:
                return httpx.Response(500)
            self.applied.setdefault(key, {}).update(fields)
            return httpx.Response(204)
        response = super().handler(request)
        if request.method == "GET" and path.startswith("/rest/api/3/issue/"):
            applied = self.applied.get(path.rsplit("/", 1)[-1])
            if applied:
                body = json.loads(response.content)
                body["fields"] = {**body["fields"], **applied}
                response = httpx.Response(200, json=body)
        return response


def make_update_app():
    from tests.test_ticketing import MemoryStorage

    jira = UpdateJira()
    chat, _, _ = make_chat(jira=jira)
    storage = MemoryStorage()
    app = create_app(make_settings(), chat, FakeTicketJobs(storage))
    return app, storage, jira


def test_reviewed_update_flow_applies_once_and_verifies() -> None:
    app, storage, jira = make_update_app()
    client = client_for(app)
    client.get("/")  # establish the approval session
    criteria = "Given a statement, When I export, Then a CSV downloads."

    proposal = client.post(
        "/tickets/updates",
        json={"issue_key": "PAY-3", "changes": {"acceptance_criteria": criteria}},
    )
    assert proposal.status_code == 200
    body = proposal.json()
    assert body["issue_key"] == "PAY-3"
    assert body["diff"] == {"acceptance_criteria": {"old": None, "new": criteria}}

    approval = client.post(f"/tickets/update-proposals/{body['id']}/approve")
    assert approval.status_code == 200

    executed = client.post(f"/tickets/update-approvals/{approval.json()['id']}/execute")
    assert executed.status_code == 200
    assert executed.json()["status"] == "succeeded"
    assert jira.update_calls == [("PAY-3", {"customfield_10350": criteria})]
    execution = storage.executions[executed.json()["execution_id"]]
    assert execution["status"] == "succeeded"
    assert execution["requested"] == {"acceptance_criteria": criteria}
    assert execution["verified"]["acceptance_criteria"]["match"] is True

    again = client.post(f"/tickets/update-approvals/{approval.json()['id']}/execute")
    assert again.json() == executed.json()
    assert len(jira.update_calls) == 1  # duplicate click wrote nothing


def test_update_flow_conflicts_on_intervening_edit_instead_of_overwriting() -> None:
    app, storage, jira = make_update_app()
    client = client_for(app)
    client.get("/")
    criteria = "Given a statement, When I export, Then a CSV downloads."
    proposal = client.post(
        "/tickets/updates",
        json={"issue_key": "PAY-3", "changes": {"acceptance_criteria": criteria}},
    )
    approval = client.post(f"/tickets/update-proposals/{proposal.json()['id']}/approve")

    # Someone else edits the same field between approval and execution.
    jira.applied["PAY-3"] = {"customfield_10350": "Criteria someone else wrote first"}

    executed = client.post(f"/tickets/update-approvals/{approval.json()['id']}/execute")
    assert executed.status_code == 409
    assert executed.json()["status"] == "rejected_stale"
    assert jira.update_calls == []  # no silent overwrite
    assert storage.executions[executed.json()["execution_id"]]["status"] == "rejected_stale"


def test_update_endpoints_require_session_database_and_valid_fields() -> None:
    app, *_ = make_update_app()
    client = client_for(app)  # no visit to "/": no approval session
    base = "/tickets/updates"
    body = {"issue_key": "PAY-3", "changes": {"acceptance_criteria": "Given, When, Then."}}
    assert client.post(base, json=body).status_code == 403
    assert client.post("/tickets/update-proposals/1/approve").status_code == 403
    assert client.post("/tickets/update-approvals/1/execute").status_code == 403

    client.get("/")
    no_db = client_for(make_app()[0])
    no_db.get("/")
    assert no_db.post(base, json=body).status_code == 409
    assert no_db.post("/tickets/update-proposals/1/approve").status_code == 409
    assert no_db.post("/tickets/update-approvals/1/execute").status_code == 409

    assert client.post(base, json={"issue_key": "", "changes": body["changes"]}).status_code == 400
    assert client.post(base, json={"issue_key": "PAY-3", "changes": {}}).status_code == 400
    assert (
        client.post(base, json={"issue_key": "PAY-3", "changes": {"nope": "x"}}).status_code == 400
    )
    assert (
        client.post(
            base, json={"issue_key": "PAY-3", "changes": {"summary": "Statement export"}}
        ).status_code
        == 400  # no-op: every requested value already matches the issue
    )
