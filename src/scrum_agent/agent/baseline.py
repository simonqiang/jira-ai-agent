"""Live-model cost-baseline runner for the Week 3 checked intents."""

from __future__ import annotations

from scrum_agent.config import Settings, require_model_settings


async def run_baseline(settings: Settings, out_path: str | None = None) -> dict:
    """Validate model configuration before executing any live baseline task."""
    require_model_settings(settings)
    raise NotImplementedError("Week 3 baseline execution is not implemented yet")
