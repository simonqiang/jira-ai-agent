"""Regression coverage for non-negotiable conversational-agent guidance."""

from scrum_agent.agent.instructions import AGENT_INSTRUCTION


def test_drafting_instruction_requires_proposals_before_the_first_tool_call() -> None:
    assert "Before your first `draft_ticket` call" in AGENT_INSTRUCTION
    assert "Do not ask the user to fill in the tool's `open_questions`" in AGENT_INSTRUCTION
    assert "accept the proposed draft" in AGENT_INSTRUCTION
    assert "correct any field" in AGENT_INSTRUCTION


def test_related_tickets_are_examples_not_requirements() -> None:
    assert "never requirements" in AGENT_INSTRUCTION
    assert "potential" in AGENT_INSTRUCTION and "confirmed" in AGENT_INSTRUCTION
    assert "`duplicate_keys`" in AGENT_INSTRUCTION


def test_drafting_instruction_keeps_suggestions_out_of_the_draft() -> None:
    assert '"Related work"' in AGENT_INSTRUCTION
    assert "never suggestion text" in AGENT_INSTRUCTION


def test_quality_review_instruction_bans_scores_and_invented_content() -> None:
    assert "`review_ticket`" in AGENT_INSTRUCTION
    assert "advisory suggestions" in AGENT_INSTRUCTION and "never block" in AGENT_INSTRUCTION
    assert "no numeric quality score" in AGENT_INSTRUCTION
    assert "fabricate the missing content" in AGENT_INSTRUCTION


def test_confirmed_update_guardrails_are_pinned() -> None:
    assert "`propose_ticket_update`" in AGENT_INSTRUCTION
    assert "never add extra fields" in AGENT_INSTRUCTION
    assert "server-rendered confirmation card" in AGENT_INSTRUCTION
    assert "A chat reply cannot approve or execute" in AGENT_INSTRUCTION
    assert "a new proposal every time" in AGENT_INSTRUCTION
