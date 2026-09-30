"""Build an editable ticket draft from a template and user-supplied fields.

Drafting never invents content: every section either carries text the caller
provided or, for a required/team-policy field, becomes an explicit open
question. Advisory gaps produce a writing suggestion instead of a question —
they improve the draft but never block it. Callers pass plain strings already
elicited from the user (typically the conversational agent); this module does
no generation and no field-value guessing.
"""

from __future__ import annotations

from scrum_agent.drafting.templates import TicketTemplate


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def build_draft(template: TicketTemplate, fields: dict[str, str] | None) -> dict:
    """Render one draft: populated sections, gaps as questions, readiness.

    ``fields`` maps template field keys (see the YAML template) to the text
    the user already gave; unknown keys are ignored so callers can pass
    slightly stale field sets without failing.
    """
    provided = fields or {}
    sections: list[dict] = []
    missing_required: list[str] = []
    missing_team_policy: list[str] = []
    advisory_suggestions: list[dict] = []
    open_questions: list[dict] = []

    for field in template.fields:
        value = _clean(provided.get(field.key))
        sections.append(
            {
                "key": field.key,
                "label": field.label,
                "category": field.category,
                "value": value,
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

    ready = not missing_required and not missing_team_policy
    return {
        "issue_type": template.issue_type,
        "template_version": template.version,
        "sections": sections,
        "missing_required_fields": missing_required,
        "missing_team_policy_fields": missing_team_policy,
        "advisory_suggestions": advisory_suggestions,
        "open_questions": open_questions,
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
            lines.append(section["value"])
        elif section["category"] in ("required_field", "team_policy"):
            lines.append("(missing — see open questions)")
        else:
            lines.append("(not provided)")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
