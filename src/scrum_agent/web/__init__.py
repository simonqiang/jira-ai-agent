"""Local single-user chat UI for the Week 3 agent (FastAPI + Jinja + htmx).

Security posture (pilot):
- Binds to loopback only; ``Settings.web_host`` rejects anything else, and
  the middleware below additionally rejects non-GET requests whose Host or
  Origin is not a loopback form of the configured host (blocks DNS
  rebinding and cross-site form posts).
- The session cookie is HttpOnly + SameSite=strict; no CORS headers are
  ever emitted, so browser cross-origin reads fail closed. A dedicated CSRF
  token is therefore not added for this single-user loopback pilot;
  revisit if the app ever binds wider.
- Conversations are short-lived and in-process (roadmap Week 3); reset or
  restart clears all context. Sources and usage shown per turn are the
  server's own records, never model claims.
"""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from scrum_agent.agent.chat import ChatService
from scrum_agent.agent.usage import UsageSnapshot
from scrum_agent.config import Settings
from scrum_agent.reports.exports import to_csv, to_markdown
from scrum_agent.reports.jobs import job_view, run_forever
from scrum_agent.web.linkify import linkify_issue_keys, localtime

_SESSION_COOKIE = "scrum_agent_session"
_MAX_MESSAGE_CHARS = 2000
_PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass
class TurnView:
    """What one rendered turn needs (server truth for sources and usage)."""

    user_text: str
    answer: str
    sources: tuple[dict, ...]
    usage_text: str
    fetched_at: str
    update_proposal: dict | None = None
    confirmation_token: str | None = None
    confirmation_used: bool = False


def _usage_text(usage: UsageSnapshot) -> str:
    call_word = "call" if usage.model_calls == 1 else "calls"
    return (
        f"{usage.model_calls} model {call_word}, "
        f"{usage.prompt_tokens} prompt / {usage.output_tokens} output tokens"
    )


def _loopback_ok(settings: Settings, request: Request) -> bool:
    host = (request.headers.get("host") or "").lower().strip()
    allowed_names = {settings.web_host.strip("[]"), "localhost", "127.0.0.1", "::1"}
    try:
        host_name = urlsplit(f"//{host}").hostname if host else None
        host_ok = not host or host_name in allowed_names
        origin = (request.headers.get("origin") or "").strip()
        if origin:
            parsed_origin = urlsplit(origin)
            origin_ok = (
                parsed_origin.scheme in {"http", "https"}
                and parsed_origin.hostname in allowed_names
                and parsed_origin.username is None
                and parsed_origin.password is None
            )
        else:
            origin_ok = True
    except ValueError:
        return False
    return host_ok and origin_ok


def create_app(settings: Settings, chat: ChatService, jobs=None, updates=None) -> FastAPI:
    """Build the chat web app bound to ``chat`` (and report ``jobs``)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if jobs is not None:
            worker = asyncio.create_task(run_forever(jobs))
        yield
        if worker is not None:
            worker.cancel()
        await chat.aclose()

    app = FastAPI(
        title="scrum-agent pilot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.jira_api_token.get_secret_value(),
        same_site="strict",
        https_only=False,
    )
    app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static")

    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    scope = chat.service.scope
    templates.env.filters["linkify_issue_keys"] = lambda text: linkify_issue_keys(
        text, scope.issue_key_pattern, settings.jira_site
    )
    templates.env.filters["localtime"] = lambda stamp: localtime(stamp, settings.report_timezone)

    conversations: dict[str, list[TurnView]] = {}
    tickets = None
    if jobs is not None:
        from scrum_agent.ticketing.approvals import TicketApprovalService

        tickets = TicketApprovalService(
            jobs.storage(), chat.jira_client, project_key=settings.jira_project_key
        )
    if updates is None and jobs is not None:
        from scrum_agent.ticketing.updates import TicketUpdateService

        updates = TicketUpdateService(jobs.storage(), chat.jira_client)

    def approver(request: Request) -> str:
        user = request.session.get("approval_user")
        if user != settings.approval_user_id:
            raise PermissionError("Approval session is not authenticated")
        return user

    def template_globals() -> dict:
        return {
            "project_key": scope.project_key,
            "board_id": scope.board_id,
            "jira_site": settings.jira_site,
            "timezone": settings.report_timezone,
        }

    @app.middleware("http")
    async def enforce_loopback(request: Request, call_next):
        if request.method != "GET" and not _loopback_ok(settings, request):
            return PlainTextResponse("Forbidden: loopback pilot only.", status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        request.session["approval_user"] = settings.approval_user_id
        session_id = request.cookies.get(_SESSION_COOKIE)
        if session_id not in conversations:
            session_id = secrets.token_urlsafe(32)
            conversations[session_id] = []
        response = templates.TemplateResponse(
            request=request,
            name="chat.html",
            context={
                **template_globals(),
                "turns": conversations[session_id],
                "cumulative_usage": _usage_text(chat.usage.snapshot(session_id)),
            },
        )
        response.set_cookie(_SESSION_COOKIE, session_id, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/chat", response_class=HTMLResponse)
    async def chat_turn(request: Request, message: str = Form(...)) -> HTMLResponse:
        session_id = request.cookies.get(_SESSION_COOKIE)
        if session_id not in conversations:
            return PlainTextResponse("Session expired; reload the page.", status_code=400)
        text = message.strip()
        if not text:
            return PlainTextResponse("Message must not be empty.", status_code=400)
        if len(text) > _MAX_MESSAGE_CHARS:
            return PlainTextResponse(
                f"Message too long (max {_MAX_MESSAGE_CHARS} characters).", status_code=400
            )

        result = await chat.run_turn(session_id, text)
        view = TurnView(
            user_text=text,
            answer=result.answer,
            sources=result.sources,
            usage_text=_usage_text(result.usage),
            fetched_at=result.fetched_at,
            update_proposal=result.update_proposal,
            confirmation_token=(secrets.token_urlsafe(32) if result.update_proposal else None),
        )
        conversations[session_id].append(view)
        return templates.TemplateResponse(
            request=request,
            name="_turn.html",
            context={
                **template_globals(),
                "turn": view,
                "cumulative_usage": _usage_text(chat.usage.snapshot(session_id)),
                "oob_usage": True,
            },
        )

    @app.post("/reset")
    async def reset(request: Request) -> RedirectResponse:
        session_id = request.cookies.get(_SESSION_COOKIE)
        if session_id in conversations:
            conversations.pop(session_id, None)
            await chat.reset(session_id)
        response = RedirectResponse("/", status_code=303)
        response.headers["HX-Redirect"] = "/"
        return response

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # -- approved ticket creation (Week 8) --------------------------------------

    @app.post("/tickets/drafts")
    async def create_ticket_draft(request: Request) -> Response:
        if tickets is None:
            return PlainTextResponse("Ticket creation needs the local database.", status_code=409)
        try:
            body = await request.json()
            issue_type = body.get("issue_type") if isinstance(body, dict) else None
            fields = body.get("fields") if isinstance(body, dict) else None
            valid_fields = isinstance(fields, dict) and all(
                isinstance(key, str) and isinstance(value, str) for key, value in fields.items()
            )
            if not isinstance(issue_type, str) or not issue_type.strip() or not valid_fields:
                raise ValueError("issue_type and fields must be text values")
            if len(fields) > 32 or any(len(value) > 10_000 for value in fields.values()):
                raise ValueError("Too many or too-large draft fields")
            return JSONResponse(tickets.create_draft(issue_type, fields, creator=approver(request)))
        except PermissionError:
            return PlainTextResponse("Approval session is not authenticated.", status_code=403)
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=400)

    @app.post("/tickets/drafts/{draft_id}/approve")
    async def approve_ticket_draft(request: Request, draft_id: int) -> Response:
        if tickets is None:
            return PlainTextResponse("Ticket creation needs the local database.", status_code=409)
        try:
            return JSONResponse(tickets.approve(draft_id, approver=approver(request)))
        except PermissionError:
            return PlainTextResponse("Approval session is not authenticated.", status_code=403)
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=400)

    @app.post("/tickets/approvals/{approval_id}/execute")
    async def execute_ticket_approval(request: Request, approval_id: int) -> Response:
        if tickets is None:
            return PlainTextResponse("Ticket creation needs the local database.", status_code=409)
        try:
            result = tickets.execute(approval_id, approver=approver(request))
            return JSONResponse(result, status_code=200 if result["status"] == "succeeded" else 202)
        except PermissionError:
            return PlainTextResponse("Approval session is not authenticated.", status_code=403)
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=400)

    # -- reviewed ticket updates (Week 9) ----------------------------------------

    @app.post("/tickets/updates")
    async def propose_ticket_update(request: Request) -> Response:
        if updates is None:
            return PlainTextResponse("Ticket updates need the local database.", status_code=409)
        try:
            body = await request.json()
            issue_key = body.get("issue_key") if isinstance(body, dict) else None
            changes = body.get("changes") if isinstance(body, dict) else None
            if not isinstance(issue_key, str) or not issue_key.strip():
                raise ValueError("issue_key must be a Jira issue key such as PAY-3")
            if not isinstance(changes, dict) or not changes:
                raise ValueError("changes must be a non-empty mapping of field to new value")
            if len(changes) > 32 or any(
                not isinstance(key, str) or (isinstance(value, str) and len(value) > 10_000)
                for key, value in changes.items()
            ):
                raise ValueError("Too many or too-large update fields")
            return JSONResponse(
                updates.propose_update(issue_key, changes, creator=approver(request))
            )
        except PermissionError:
            return PlainTextResponse("Approval session is not authenticated.", status_code=403)
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=400)

    @app.post("/tickets/update-proposals/{proposal_id}/approve")
    async def approve_ticket_update(request: Request, proposal_id: int) -> Response:
        if updates is None:
            return PlainTextResponse("Ticket updates need the local database.", status_code=409)
        try:
            return JSONResponse(updates.approve_update(proposal_id, approver=approver(request)))
        except PermissionError:
            return PlainTextResponse("Approval session is not authenticated.", status_code=403)
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=400)

    @app.post("/tickets/update-approvals/{approval_id}/execute")
    async def execute_ticket_update(request: Request, approval_id: int) -> Response:
        if updates is None:
            return PlainTextResponse("Ticket updates need the local database.", status_code=409)
        try:
            result = updates.execute_update(approval_id, approver=approver(request))
            code = {"succeeded": 200, "rejected_stale": 409}.get(result["status"], 502)
            return JSONResponse(result, status_code=code)
        except PermissionError:
            return PlainTextResponse("Approval session is not authenticated.", status_code=403)
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=400)

    @app.post("/tickets/update-proposals/{proposal_id}/confirm", response_class=HTMLResponse)
    async def confirm_ticket_update(
        request: Request, proposal_id: int, confirmation_token: str = Form(...)
    ) -> Response:
        """Apply a diff only after the browser submits its rendered review card."""
        if updates is None:
            return PlainTextResponse("Ticket updates need the local database.", status_code=409)
        session_id = request.cookies.get(_SESSION_COOKIE)
        views = conversations.get(session_id or "", ())
        view = next(
            (
                item
                for item in views
                if item.update_proposal and item.update_proposal.get("id") == proposal_id
            ),
            None,
        )
        if view is None or view.confirmation_token is None:
            return PlainTextResponse(
                "Update review not found in this conversation.", status_code=403
            )
        if not secrets.compare_digest(confirmation_token, view.confirmation_token):
            return PlainTextResponse("Update confirmation is invalid.", status_code=403)
        if view.confirmation_used:
            return PlainTextResponse("This update review was already confirmed.", status_code=409)
        view.confirmation_used = True
        try:
            approval = updates.approve_update(proposal_id, approver=approver(request))
            result = updates.execute_update(approval["id"], approver=approver(request))
        except PermissionError:
            return PlainTextResponse("Approval session is not authenticated.", status_code=403)
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=400)
        return templates.TemplateResponse(
            request=request,
            name="_ticket_update_result.html",
            context={"result": result},
            status_code={"succeeded": 200, "rejected_stale": 409}.get(result["status"], 502),
        )

    # -- reports (Weeks 5-6) ---------------------------------------------------

    @app.get("/reports", response_class=HTMLResponse)
    async def reports_page(request: Request, error: str = "") -> HTMLResponse:
        sprints = chat.service.list_sprints(states=("active", "future", "closed"))
        return templates.TemplateResponse(
            request=request,
            name="reports.html",
            context={
                **template_globals(),
                "sprints": sprints,
                "error": error,
                "page": "reports",
            },
        )

    @app.post("/reports")
    async def submit_report(request: Request, sprint: str = Form(...)) -> Response:
        if jobs is None:
            return RedirectResponse("/reports?error=Reports+need+the+local+database", 303)
        try:
            handle = jobs.submit(sprint.strip())
        except Exception as exc:
            message = str(exc).replace("\n", " ")[:300]
            return RedirectResponse(f"/reports?error={quote(message)}", 303)
        return RedirectResponse(f"/reports/{handle['job_id']}", 303)

    @app.get("/reports/{job_id}", response_class=HTMLResponse)
    async def report_view(request: Request, job_id: int) -> HTMLResponse:
        row = jobs.storage().get_report_job(job_id) if jobs is not None else None
        if row is None:
            return PlainTextResponse("No such report job.", status_code=404)
        view = job_view(row)
        return templates.TemplateResponse(
            request=request,
            name="report.html",
            context={
                **template_globals(),
                "job": view,
                "report": view.get("report"),
                "page": "reports",
            },
        )

    @app.get("/reports/{job_id}/report.{fmt}")
    async def report_export(job_id: int, fmt: str) -> Response:
        if jobs is None or fmt not in ("md", "csv"):
            return PlainTextResponse("No such export.", status_code=404)
        row = jobs.storage().get_report_job(job_id)
        if row is None or row["status"] != "done" or row["report"] is None:
            return PlainTextResponse("Report not finished; export unavailable.", status_code=404)
        content = to_markdown(row["report"]) if fmt == "md" else to_csv(row["report"])
        media = "text/markdown; charset=utf-8" if fmt == "md" else "text/csv; charset=utf-8"
        filename = f"sprint-{row['sprint_id']}-report.{fmt}"
        return Response(
            content,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app
