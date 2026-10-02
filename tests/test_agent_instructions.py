"""Regression coverage for non-negotiable conversational-agent guidance."""

from scrum_agent.agent.instructions import AGENT_INSTRUCTION


def test_drafting_instruction_requires_proposals_before_the_first_tool_call() -> None:
    assert "Before your first `draft_ticket` call" in AGENT_INSTRUCTION
    assert "Do not ask the user to fill in the tool's `open_questions`" in AGENT_INSTRUCTION
    assert "accept the proposed draft" in AGENT_INSTRUCTION
    assert "correct any field" in AGENT_INSTRUCTION
