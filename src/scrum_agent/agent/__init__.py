"""Week 3 conversational agent: narrow ADK tools over the Week 2 search service.

The agent layer adds no scope logic of its own: every tool call goes through
``SearchService``/``JiraClient``, which enforce ``PilotScope`` on every data
path (scoped JQL, per-issue key checks, fail-closed pagination).
"""

from scrum_agent.agent.chat import ChatService, TurnResult, build_agent
from scrum_agent.agent.llm import ZaiAnthropicLlm
from scrum_agent.agent.usage import UsageRecorder, UsageSnapshot

__all__ = [
    "ChatService",
    "TurnResult",
    "UsageRecorder",
    "UsageSnapshot",
    "ZaiAnthropicLlm",
    "build_agent",
]
