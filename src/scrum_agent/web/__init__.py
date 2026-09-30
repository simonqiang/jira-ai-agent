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


def create_app(settings: Settings, chat: ChatService, jobs=None) -> FastAPI:
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
    app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static")

    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    scope = chat.service.scope
    templates.env.filters["linkify_issue_keys"] = lambda text: linkify_issue_keys(
        text, scope.issue_key_pattern, settings.jira_site
    )
    templates.env.filters["localtime"] = lambda stamp: localtime(stamp, settings.report_timezone)

    conversations: dict[str, list[TurnView]] = {}

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

    # -- reports (Week 5) ------------------------------------------------------

    @app.get("/reports", response_class=HTMLResponse)
    async def reports_page(request: Request, error: str = "") -> HTMLResponse:
        sprints = chat.service.list_sprints(states=("active", "future"))
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
