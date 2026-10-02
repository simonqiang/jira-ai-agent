"""Build an editable ticket draft from a template and user-supplied fields.

Drafting preserves user text and adds bounded, clearly labelled Story
proposals when a role and goal are available. Those proposals avoid turning a
routine request into a questionnaire, but still require the user to confirm
or correct them before the draft is ready. Genuinely unknown content remains
an open question; advisory gaps remain optional suggestions.
"""

from __future__ import annotations

from scrum_agent.drafting.templates import TicketTemplate


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _story_proposals(template: TicketTemplate, provided: dict[str, str]) -> dict[str, str]:
    """Supply safe Story defaults once the user states who wants what.

    The proposals intentionally avoid domain-specific facts: they describe the
    requested outcome, a conservative boundary, and observable integration
    behavior. The caller marks them as proposals and requires confirmation.
    """
    if template.issue_type != "Story":
        return {}
    role = _clean(provided.get("role"))
    goal = _clean(provided.get("goal"))
    if role is None or goal is None:
        return {}

    requested_outcome = goal.rstrip(".")
    proposals = {
        "benefit": (
            f"{role.capitalize()} can use {requested_outcome} without manual "
            "transfer or reconciliation."
        ),
        "scope": (
            f"In scope: deliver {requested_outcome}. Out of scope: changes to source data, "
            "historical backfill, and unrelated integrations."
        ),
        "acceptance_criteria": (
            "Given the source system publishes the requested data, when the scheduled "
            "integration runs, then the target system receives and stores it; and transfer "
            "failures are visible for follow-up."
        ),
    }
    return {key: value for key, value in proposals.items() if _clean(provided.get(key)) is None}


def build_draft(template: TicketTemplate, fields: dict[str, str] | None) -> dict:
    """Render one draft: populated sections, gaps as questions, readiness.

    ``fields`` maps template field keys (see the YAML template) to the text
    the user already gave; unknown keys are ignored so callers can pass
    slightly stale field sets without failing.
    """
    provided = fields or {}
    proposed = _story_proposals(template, provided)
    sections: list[dict] = []
    missing_required: list[str] = []
    missing_team_policy: list[str] = []
    advisory_suggestions: list[dict] = []
    open_questions: list[dict] = []

    for field in template.fields:
        value = _clean(provided.get(field.key)) or proposed.get(field.key)
        sections.append(
            {
                "key": field.key,
                "label": field.label,
                "category": field.category,
                "value": value,
                "proposed": field.key in proposed,
            }
        )
        if value is not None:
            continue
        if field.category == "required_field":
            missing_required.append(field.key)
            open_questions.append({"key": field.key, "question": field.question})
        elif field.category == "team_policy":
            missing_team_policy.append(field.key)
            open_questions.append({"key": field.key, "question": field.question})
        elif field.hint:
            advisory_suggestions.append({"key": field.key, "suggestion": field.hint})

    requires_confirmation = bool(proposed)
    ready = not missing_required and not missing_team_policy and not requires_confirmation
    return {
        "issue_type": template.issue_type,
        "template_version": template.version,
        "sections": sections,
        "missing_required_fields": missing_required,
        "missing_team_policy_fields": missing_team_policy,
        "advisory_suggestions": advisory_suggestions,
        "open_questions": open_questions,
        "proposed_fields": list(proposed),
        "requires_confirmation": requires_confirmation,
        "ready": ready,
        "rendered": render_draft(template, sections),
    }


def render_draft(template: TicketTemplate, sections: list[dict]) -> str:
    """Plain-text draft: provided sections rendered, gaps marked explicitly.

    A missing required/team-policy section is never left blank or invented —
    it is marked so it is obvious the draft is not yet ready to submit.
    """
    lines = [f"{template.issue_type} draft ({template.version})", ""]
    for section in sections:
        lines.append(f"{section['label']}:")
        if section["value"] is not None:
            prefix = "Proposal: " if section["proposed"] else ""
            lines.append(f"{prefix}{section['value']}")
        elif section["category"] in ("required_field", "team_policy"):
            lines.append("(missing — see open questions)")
        else:
            lines.append("(not provided)")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
