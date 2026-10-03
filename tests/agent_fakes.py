"""Shared fakes for Week 3 agent tests: a scripted model over the fixture Jira.

``FakeLlm`` replaces the live model inside the real ADK Runner: each
``generate_content_async`` call pops one scripted step (tool calls, or final
text) and reports synthetic token usage, so the full agent loop — Runner,
tools, ``SearchService``, scoped ``JiraClient``, pagination — runs without
network. The honest limit: tool-call *choice* is scripted here, so these
tests verify plumbing, payload fidelity and error translation; live-model
correctness is the ``scrum-agent baseline`` artifact's ``answer_ok`` field.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field, PrivateAttr

from scrum_agent.agent.chat import ChatService
from scrum_agent.config import Settings
from scrum_agent.jira.client import JiraClient
from scrum_agent.search.service import SearchService
from tests.checked_queries import FakeJira
from tests.conftest import make_settings


@dataclass(frozen=True)
class ScriptedStep:
    """One model turn: tool calls, or the final answer text."""

    tool_calls: tuple[dict[str, Any], ...] = ()
    text: str = ""
    prompt_tokens: int = 120
    output_tokens: int = 30

    def usage(self) -> types.GenerateContentResponseUsageMetadata:
        return types.GenerateContentResponseUsageMetadata(
            prompt_token_count=self.prompt_tokens,
            candidates_token_count=self.output_tokens,
            total_token_count=self.prompt_tokens + self.output_tokens,
        )


def tool_call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args}


class FakeLlm(BaseLlm):
    """Pops one ``ScriptedStep`` per model call; strict on exhaustion."""

    model: str = "fake-checked-model"
    steps: list[ScriptedStep] = Field(default_factory=list)

    _cursor: int = PrivateAttr(default=0)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> Iterator[LlmResponse]:
        if stream:
            raise AssertionError("FakeLlm does not script streaming responses")
        if self._cursor >= len(self.steps):
            raise AssertionError(
                f"FakeLlm script exhausted after {self._cursor} step(s); "
                "the agent requested another model call"
            )
        step = self.steps[self._cursor]
        self._cursor += 1

        parts: list[types.Part] = []
        for index, call in enumerate(step.tool_calls):
            part = types.Part.from_function_call(name=call["name"], args=call["args"])
            part.function_call.id = f"fake_call_{self._cursor}_{index}"
            parts.append(part)
        if step.text:
            parts.append(types.Part.from_text(text=step.text))

        await asyncio.sleep(0)  # keep this a genuine coroutine
        yield LlmResponse(
            content=types.Content(role="model", parts=parts),
            usage_metadata=step.usage(),
        )


class FakeUpdateService:
    """Records service calls with scripted outcomes for the update tools."""

    def __init__(self, execute_result: dict | None = None) -> None:
        self.calls: list[tuple] = []
        self._execute_result = execute_result or {
            "status": "succeeded",
            "execution_id": 3,
            "issue_key": "PAY-3",
            "verified": {"summary": {"expected": "New", "actual": "New", "match": True}},
        }

    def propose_update(self, issue_key: str, changes: dict, *, creator: str) -> dict:
        self.calls.append(("propose", issue_key, dict(changes), creator))
        return {
            "id": 7,
            "issue_key": issue_key,
            "summary": "Statement export",
            "diff": {key: {"old": None, "new": value} for key, value in changes.items()},
            "unchanged_fields_untouched": True,
            "payload_hash": "h",
        }

    def approve_update(self, proposal_id: int, *, approver: str) -> dict:
        self.calls.append(("approve", proposal_id, approver))
        return {"id": 11, "expires_at": "2026-10-03T18:00:00+00:00"}

    def execute_update(self, approval_id: int, *, approver: str) -> dict:
        self.calls.append(("execute", approval_id, approver))
        return dict(self._execute_result)


def make_chat(
    *,
    jira: FakeJira | None = None,
    steps: list[ScriptedStep] | None = None,
    settings: Settings | None = None,
    retrieval=None,
    updates=None,
) -> tuple[ChatService, FakeJira, FakeLlm]:
    """Compose a ChatService over the fixture Jira with a scripted model."""
    jira = jira if jira is not None else FakeJira()
    settings = settings if settings is not None else make_settings()
    fake = FakeLlm(model="fake-checked-model", steps=list(steps or []))
    chat = ChatService(
        settings,
        llm=fake,
        transport=httpx.MockTransport(jira.handler),
        retrieval=retrieval,
        updates=updates,
    )
    return chat, jira, fake


@dataclass
class ServiceProbe:
    """A search service plus the HTTP requests it made (for zero-HTTP checks)."""

    service: SearchService
    requests: list[httpx.Request]


@contextmanager
def service_over(jira: FakeJira) -> Iterator[ServiceProbe]:
    """Scoped SearchService over the fixture Jira, recording every request."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return jira.handler(request)

    with JiraClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        yield ServiceProbe(SearchService(client), requests)


@contextmanager
def tools_over(jira: FakeJira) -> Iterator[dict[str, Any]]:
    """The four agent tools as a name -> callable mapping over the fixture Jira."""
    from scrum_agent.agent.tools import make_tools

    with service_over(jira) as probe:
        yield {tool.name: tool.func for tool in make_tools(probe.service)}
