"""ADK model adapter for an Anthropic-compatible provider (Z.ai GLM).

Mirrors google-adk's own ``Claude`` subclass pattern: override the
``_anthropic_client`` cached property so the underlying ``AsyncAnthropic``
client points at the configured base URL with the pilot's own API key.
Credentials come from ``Settings`` (a ``SecretStr``), never from the process
environment, so the app never touches the user's Claude Code configuration.
"""

from __future__ import annotations

from functools import cached_property

from anthropic import AsyncAnthropic
from google.adk.models.anthropic_llm import AnthropicLlm
from pydantic import SecretStr
from typing_extensions import override

from scrum_agent.config import Settings, require_model_settings

DEFAULT_MODEL_BASE_URL = "https://api.z.ai/api/anthropic"


class ZaiAnthropicLlm(AnthropicLlm):
    """Anthropic-compatible ADK model pointed at a configured provider.

    Attributes:
        model: Model name sent to the provider (e.g. ``glm-5.3``).
        api_key: Provider API key (kept as a ``SecretStr``).
        base_url: Anthropic-compatible API root.
        max_tokens: Maximum tokens generated per model turn.
    """

    model: str = "glm-5.3"
    api_key: SecretStr
    base_url: str = DEFAULT_MODEL_BASE_URL
    max_tokens: int = 8192

    @classmethod
    @override
    def supported_models(cls) -> list[str]:
        return [r"glm-.*"]

    @classmethod
    def from_settings(cls, settings: Settings) -> ZaiAnthropicLlm:
        """Build the adapter from validated model settings.

        Raises ``ValueError`` via ``require_model_settings`` when the model
        name or API key is absent (chat features gate on this at startup).
        """
        require_model_settings(settings)  # guarantees name and key are present
        return cls(
            model=str(settings.model_name),
            api_key=SecretStr(settings.model_api_key.get_secret_value()),
            base_url=settings.model_base_url,
            max_tokens=settings.model_max_tokens,
        )

    @cached_property
    @override
    def _anthropic_client(self) -> AsyncAnthropic:
        return AsyncAnthropic(
            api_key=self.api_key.get_secret_value(),
            base_url=self.base_url,
        )
