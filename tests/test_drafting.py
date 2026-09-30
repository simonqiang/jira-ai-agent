"""Week 7: versioned templates and draft building.

Covers each issue type with a complete example, an incomplete example (the
"review at least two examples of each issue type" requirement), required vs
team-policy vs advisory separation, unknown-type handling and that drafting
never invents content for a field the caller did not supply.
"""

from __future__ import annotations

import pytest

from scrum_agent.drafting import build_draft, default_templates, get_template, load_templates
from scrum_agent.drafting.templates import TemplateField, TicketTemplate


def test_default_templates_cover_story_bug_task() -> None:
    templates = default_templates()
    assert set(templates) == {"story", "bug", "task"}
    for template in templates.values():
        assert template.version
        assert template.fields


def test_get_template_is_case_insensitive_exact_match() -> None:
    templates = default_templates()
    assert get_template(templates, "story").issue_type == "Story"
    assert get_template(templates, "STORY").issue_type == "Story"
    assert get_template(templates, " Bug ").issue_type == "Bug"


def test_get_template_unknown_type_lists_known_types() -> None:
    templates = default_templates()
    with pytest.raises(ValueError, match="Story, Task|Bug, Story"):
        get_template(templates, "Epic")


# -- Story: one complete, one incomplete example -----------------------------


def test_story_complete_example_is_ready_with_no_open_questions() -> None:
    template = get_template(default_templates(), "Story")
    draft = build_draft(
        template,
        {
            "role": "logistics operations analyst",
            "goal": "automatic retries for failed shipment-status API calls",
            "benefit": "transient failures do not block daily tracking updates",
            "scope": "retry logic for the shipment-status integration only",
            "acceptance_criteria": "retries stop after N attempts and alert on exhaustion",
        },
    )
    assert draft["ready"] is True
    assert draft["missing_required_fields"] == []
    assert draft["missing_team_policy_fields"] == []
    assert draft["open_questions"] == []


def test_story_incomplete_example_asks_instead_of_inventing() -> None:
    """Mirrors docs/samples/story.example.md: no acceptance criteria, no estimate."""
    template = get_template(default_templates(), "Story")
    draft = build_draft(
        template,
        {
            "role": "logistics operations analyst",
            "goal": "automatic retries for failed shipment-status API calls",
            "benefit": "transient failures do not block daily tracking updates",
        },
    )
    assert draft["ready"] is False
    assert "acceptance_criteria" in draft["missing_team_policy_fields"]
    assert "scope" in draft["missing_team_policy_fields"]
    question_keys = {q["key"] for q in draft["open_questions"]}
    assert "acceptance_criteria" in question_keys
    # No invented acceptance criteria text anywhere in the rendered draft.
    acceptance_section = next(s for s in draft["sections"] if s["key"] == "acceptance_criteria")
    assert acceptance_section["value"] is None
    assert "missing" in draft["rendered"].lower()


# -- Bug: one complete, one incomplete example -------------------------------


def test_bug_complete_example_is_ready() -> None:
    template = get_template(default_templates(), "Bug")
    draft = build_draft(
        template,
        {
            "summary": "Database connection pool exhausted during peak hours",
            "environment": "production, morning peak window",
            "steps_to_reproduce": "Send concurrent requests during the peak window",
            "expected": "Pool serves peak load without exhaustion",
            "actual": "Pool exhausts, responses degrade",
            "impact": "User-facing degradation during business peaks",
            "verification_criteria": "Pool stays below capacity during a simulated peak",
        },
    )
    assert draft["ready"] is True
    assert draft["missing_required_fields"] == []
    assert draft["missing_team_policy_fields"] == []


def test_bug_incomplete_example_flags_repro_and_verification_as_team_policy() -> None:
    """Mirrors docs/samples/bug.example.md: no repro steps, no verification criteria."""
    template = get_template(default_templates(), "Bug")
    draft = build_draft(
        template,
        {
            "summary": "Database connection pool exhausted during peak hours",
            "expected": "Pool serves peak load without exhaustion",
            "actual": "Pool exhausts, responses degrade, errors surface to users",
        },
    )
    assert draft["ready"] is False
    for key in ("environment", "steps_to_reproduce", "impact", "verification_criteria"):
        assert key in draft["missing_team_policy_fields"], key
    # required_field content the user gave is preserved verbatim, not reworded.
    actual_section = next(s for s in draft["sections"] if s["key"] == "actual")
    assert actual_section["value"] == "Pool exhausts, responses degrade, errors surface to users"


# -- Task: one complete, one incomplete example ------------------------------


def test_task_complete_example_is_ready() -> None:
    template = get_template(default_templates(), "Task")
    draft = build_draft(
        template,
        {
            "objective": "Integrate AI demographic analysis for targeted marketing",
            "scope": "Segmentation insights surfaced in the customer-profile view",
            "completion_checklist": "Analysis capability shipped; insights visible in profile view",
        },
    )
    assert draft["ready"] is True


def test_task_incomplete_example_flags_checklist_as_team_policy() -> None:
    """Mirrors docs/samples/task.example.md: no completion checklist recorded."""
    template = get_template(default_templates(), "Task")
    draft = build_draft(
        template,
        {"objective": "Integrate AI demographic analysis for targeted marketing"},
    )
    assert draft["ready"] is False
    assert draft["missing_team_policy_fields"] == ["scope", "completion_checklist"]
    assert draft["missing_required_fields"] == []


# -- advisory suggestions never block readiness ------------------------------


def test_advisory_gaps_are_suggestions_not_blockers() -> None:
    template = get_template(default_templates(), "Story")
    draft = build_draft(
        template,
        {
            "role": "analyst",
            "goal": "goal",
            "benefit": "benefit",
            "scope": "scope",
            "acceptance_criteria": "criteria",
        },
    )
    assert draft["ready"] is True
    advisory_keys = {item["key"] for item in draft["advisory_suggestions"]}
    assert {"context", "dependencies", "open_questions"} <= advisory_keys


def test_unknown_fields_are_ignored_not_errors() -> None:
    template = get_template(default_templates(), "Task")
    draft = build_draft(template, {"objective": "x", "not_a_real_field": "y"})
    assert draft["issue_type"] == "Task"
    assert not any(section["key"] == "not_a_real_field" for section in draft["sections"])


def test_non_string_and_whitespace_values_count_as_missing() -> None:
    """_clean never invents: junk values degrade to open questions, not content."""
    template = get_template(default_templates(), "Task")
    draft = build_draft(template, {"objective": 42, "scope": "   "})
    assert draft["missing_required_fields"] == ["objective"]
    assert "scope" in draft["missing_team_policy_fields"]
    assert draft["ready"] is False


def test_build_draft_accepts_none_fields() -> None:
    template = get_template(default_templates(), "Task")
    draft = build_draft(template, None)
    assert draft["ready"] is False
    assert draft["missing_required_fields"] == ["objective"]


# -- template loading validation ---------------------------------------------


def test_load_templates_rejects_duplicate_field_keys(tmp_path) -> None:
    (tmp_path / "bad.yaml").write_text(
        "issue_type: Story\nversion: story-v2\nfields:\n"
        "  - {key: role, label: Role, category: required_field, question: 'Who?'}\n"
        "  - {key: role, label: Role again, category: advisory, hint: 'dup'}\n"
    )
    with pytest.raises(ValueError, match="duplicate field keys"):
        load_templates(tmp_path)


def test_load_templates_rejects_missing_question_for_required_field(tmp_path) -> None:
    (tmp_path / "bad.yaml").write_text(
        "issue_type: Story\nversion: story-v2\nfields:\n"
        "  - {key: role, label: Role, category: required_field}\n"
    )
    with pytest.raises(ValueError, match="needs a question"):
        load_templates(tmp_path)


def test_load_templates_rejects_duplicate_issue_types(tmp_path) -> None:
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(
            "issue_type: Story\nversion: story-v2\nfields:\n"
            "  - {key: role, label: Role, category: advisory, hint: 'h'}\n"
        )
    with pytest.raises(ValueError, match="duplicate template"):
        load_templates(tmp_path)


def test_ticket_template_field_lookup() -> None:
    template = TicketTemplate(
        issue_type="Story",
        version="story-v9",
        fields=(TemplateField(key="role", label="Role", category="advisory", hint="h"),),
    )
    assert template.field("role") is not None
    assert template.field("missing") is None
