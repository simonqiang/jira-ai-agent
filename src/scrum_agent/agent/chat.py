"""Conversational agent service with opt-in persisted sessions.

``ChatService`` owns the Jira client, the ADK agent, a session service and the
usage recorder. When ``SCRUM_AGENT_DATABASE_URL`` is configured, ADK sessions
persist in the local Postgres database (keyed by app/user/session, so the
single pilot user's conversations survive an app restart); otherwise they stay
in process memory as in Week 3. The rendered web transcript remains
in-process regardless. Sources reported per turn come from the tool payloads
the server actually executed, never from citations the model claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, DatabaseSessionService, InMemorySessionService
from google.genai import types
from httpx import BaseTransport

from scrum_agent.agent.instructions import AGENT_INSTRUCTION
from scrum_agent.agent.llm import ZaiAnthropicLlm
from scrum_agent.agent.tools import make_tools
from scrum_agent.agent.usage import UsageRecorder, UsageSnapshot
from scrum_agent.config import Settings
from scrum_agent.jira.client import JiraClient
from scrum_agent.search.service import SearchService

_MAX_TURNS_NOTICE = (
    "This short-lived conversation has reached its turn limit. "
    "Start a new conversation to continue."
)


@dataclass(frozen=True)
class TurnResult:
    """One completed conversational turn."""

    answer: str
    sources: tuple[dict, ...] = ()
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)
    turn_index: int = 0
    fetched_at: str = ""


def build_agent(
    service: SearchService, llm: BaseLlm, usage: UsageRecorder, jobs=None, retrieval=None
) -> LlmAgent:
    """Assemble the read-only pilot agent."""
    return LlmAgent(
        name="scrum_agent",
        model=llm,
        description="Read-only Scrum Master Jira assistant for the pilot board.",
        instruction=AGENT_INSTRUCTION,
        tools=make_tools(service, jobs, retrieval),
        after_model_callback=usage.on_model_response,
    )


def asyncpg_url(database_url: str) -> str:
    """Rewrite a plain postgresql:// DSN for ADK's asyncpg-only session engine."""
    return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)


def build_session_service(settings: Settings) -> BaseSessionService:
    """Persist ADK sessions in Postgres when configured, else keep them in memory.

    ADK's DatabaseSessionService creates its own tables lazily on first use,
    so construction needs no live database.
    """
    if settings.database_url is None:
        return InMemorySessionService()
    return DatabaseSessionService(db_url=asyncpg_url(settings.database_url))


class ChatService:
    """Runs conversational turns against the ADK agent over the pilot board."""

    APP_NAME = "scrum-agent"
    USER_ID = "pilot"
    MAX_TURNS = 30

    def __init__(
        self,
        settings: Settings,
        *,
        llm: BaseLlm | None = None,
        transport: BaseTransport | None = None,
        jobs=None,
        retrieval=None,
    ) -> None:
        if llm is None:
            llm = ZaiAnthropicLlm.from_settings(settings)
        self._client = JiraClient(settings, transport=transport)
        self._service = SearchService(self._client)
        self.usage = UsageRecorder()
        self._jobs = jobs
        self._agent = build_agent(self._service, llm, self.usage, jobs, retrieval)
        self._sessions = build_session_service(settings)
        self._runner = Runner(
            app_name=self.APP_NAME, agent=self._agent, session_service=self._sessions
        )
        self._turn_counts: dict[str, int] = {}

    @property
    def jobs(self):
        """The report job engine (None until the caller provides one)."""
        return self._jobs

    @property
    def service(self) -> SearchService:
        """The scoped search service every tool call flows through."""
        return self._service

    @property
    def jira_client(self) -> JiraClient:
        """The server-owned Jira client, never exposed to the model for writes."""
        return self._client

    async def run_turn(self, session_id: str, user_text: str) -> TurnResult:
        """Run one user turn and return the answer with server-truth sources."""
        text = user_text.strip()
        if not text:
            raise ValueError("message must not be blank")
        turn_index = self._turn_counts.get(session_id, 0) + 1
        self._turn_counts[session_id] = turn_index
        if turn_index > self.MAX_TURNS:
            return TurnResult(answer=_MAX_TURNS_NOTICE, turn_index=turn_index)

        if (
            await self._sessions.get_session(
                app_name=self.APP_NAME, user_id=self.USER_ID, session_id=session_id
            )
            is None
        ):
            await self._sessions.create_session(
                app_name=self.APP_NAME, user_id=self.USER_ID, session_id=session_id
            )

        before = self.usage.snapshot(session_id)
        answer_parts: list[str] = []
        sources: list[dict] = []
        fetched_at = ""
        async for event in self._runner.run_async(
            user_id=self.USER_ID,
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
        ):
            if event.is_final_response() and event.content and event.content.parts:
                answer_parts.extend(part.text for part in event.content.parts if part.text)
            for function_response in event.get_function_responses():
                payload = function_response.response
                if isinstance(payload, dict):
                    sources.extend(payload.get("sources") or [])
                    if payload.get("ok") and payload.get("fetched_at"):
                        fetched_at = payload["fetched_at"]

        deduplicated: list[dict] = []
        for source in sources:
            if source not in deduplicated:
                deduplicated.append(source)
        return TurnResult(
            answer="\n".join(part for part in answer_parts if part).strip(),
            sources=tuple(deduplicated),
            usage=self.usage.delta_since(session_id, before),
            turn_index=turn_index,
            fetched_at=fetched_at or datetime.now(UTC).isoformat(timespec="seconds"),
        )

    async def reset(self, session_id: str) -> None:
        """Forget the conversation: ADK session, usage totals and turn count."""
        await self._sessions.delete_session(
            app_name=self.APP_NAME, user_id=self.USER_ID, session_id=session_id
        )
        self.usage.forget(session_id)
        self._turn_counts.pop(session_id, None)

    async def aclose(self) -> None:
        """Release the Jira client, report jobs and any session engine."""
        self._client.close()
        if self._jobs is not None:
            self._jobs.close()
        close = getattr(self._sessions, "close", None)
        if close is not None:
            await close()
