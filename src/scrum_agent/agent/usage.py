"""Model-call and token accounting per conversation (the Week 3 cost baseline).

``UsageRecorder.on_model_response`` is an ADK ``after_model_callback``: ADK
invokes it once per model turn with the ``LlmResponse`` whose
``usage_metadata`` carries prompt/candidates/total token counts. Totals are
keyed by session id so the web UI can show per-turn deltas and cumulative
usage, and the baseline runner can record per-task cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse


@dataclass(frozen=True)
class UsageSnapshot:
    """Immutable point-in-time usage for one conversation."""

    model_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def plus(self, other: UsageSnapshot) -> UsageSnapshot:
        return UsageSnapshot(
            model_calls=self.model_calls + other.model_calls,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )

    def minus(self, other: UsageSnapshot) -> UsageSnapshot:
        return UsageSnapshot(
            model_calls=max(0, self.model_calls - other.model_calls),
            prompt_tokens=max(0, self.prompt_tokens - other.prompt_tokens),
            output_tokens=max(0, self.output_tokens - other.output_tokens),
            total_tokens=max(0, self.total_tokens - other.total_tokens),
        )


class UsageRecorder:
    """Accumulates model calls and tokens per ADK session id."""

    def __init__(self) -> None:
        self._totals: dict[str, UsageSnapshot] = {}

    def on_model_response(
        self, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> None:
        """``after_model_callback`` target; returning None passes the response through."""
        session_id = callback_context.session.id
        usage = llm_response.usage_metadata
        current = self._totals.get(session_id, UsageSnapshot())
        updated = UsageSnapshot(
            model_calls=current.model_calls + 1,
            prompt_tokens=current.prompt_tokens + (usage.prompt_token_count or 0)
            if usage
            else current.prompt_tokens,
            output_tokens=current.output_tokens + (usage.candidates_token_count or 0)
            if usage
            else current.output_tokens,
            total_tokens=current.total_tokens + self._total_of(usage),
        )
        self._totals[session_id] = updated

    @staticmethod
    def _total_of(usage) -> int:
        if usage is None:
            return 0
        return usage.total_token_count or (
            (usage.prompt_token_count or 0) + (usage.candidates_token_count or 0)
        )

    def snapshot(self, session_id: str) -> UsageSnapshot:
        return self._totals.get(session_id, UsageSnapshot())

    def delta_since(self, session_id: str, before: UsageSnapshot) -> UsageSnapshot:
        return self.snapshot(session_id).minus(before)

    def forget(self, session_id: str) -> None:
        self._totals.pop(session_id, None)
