"""Cost-baseline artifact contract for the Week 3 agent."""

from __future__ import annotations

import pytest


async def test_baseline_requires_model_configuration_before_running() -> None:
    from scrum_agent.agent.baseline import run_baseline
    from tests.conftest import make_settings

    with pytest.raises(ValueError, match="SCRUM_AGENT_MODEL_NAME"):
        await run_baseline(make_settings(), out_path=None)
