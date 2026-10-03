"""Deterministic ticket-quality review (design spec §3).

Three checks, kept separate: Jira schema validity, mandatory team rules and
advisory writing quality. A *fetched* issue is schema-valid by construction
(Jira would not return it), so the checker's hard findings are the mandatory
team-policy sections only; advisory findings never block readiness. Findings
name the specific weakness — the spec forbids a numeric score, and unknown
content becomes a question rather than invented text.
"""

from __future__ import annotations

import re

from scrum_agent.drafting.templates import TicketTemplate
from scrum_agent.jira.models import Issue

# Story descriptions usually state the role/goal/benefit as a sentence
# ("As a ... I want ... so that ...") rather than as labelled sections; these
# markers accept that shape instead of reporting false gaps.
_STORY_FALLBACK_MARKERS = {
    "role": ("as a ", "as an "),
    "goal": ("i want ", "i'd want "),
    "benefit": ("so that ",),
}


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|[-*]\s+)?\*{0,2}(?P<label>[^:*]+?)\*{0,2}:\s*(?P<inline>.*)$"
)


def _section_after_heading(body: str, label: str) -> str | None:
    """Text following a ``<label>:`` heading until the next heading.

    ponytail: naive heading scan over description/acceptance text — tickets
    drafted from our templates render exactly this shape. Free-form
    descriptions may miss headings, which only produces a "missing" finding
    with a question, never wrongly attributed content.
    """
    collecting: list[str] | None = None
    # Labels like "Environment / version" head sections as "Environment:".
    wanted = label.split("/")[0].strip().lower()
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match is not None:
            if collecting is not None:
                break  # the next heading ends this section
            if match.group("label").strip().lower() == wanted:
                inline = match.group("inline").strip().strip("*").strip()
                collecting = [inline] if inline else []
        elif collecting is not None:
            collecting.append(line)
    if collecting is None:
        return None
    return "\n".join(collecting).strip() or None


def extract_template_fields(issue: Issue, template: TicketTemplate) -> dict[str, str | None]:
    """Map a fetched issue onto the template's field keys.

    Direct issue attributes win (``summary``, ``acceptance_criteria``);
    every other section is detected by its label heading in the description
    (plus the Story role/goal/benefit sentence fallback). Unfound sections
    come back as ``None`` — a gap, never guessed content.
    """
    direct = {"summary": issue.summary, "acceptance_criteria": issue.acceptance_criteria}
    sources = [text for text in (issue.description, issue.acceptance_criteria) if text]
    lowered_description = (issue.description or "").lower()
    fields: dict[str, str | None] = {}
    for field in template.fields:
        value = _clean(direct.get(field.key))
        if value is None:
            for source in sources:
                value = _section_after_heading(source, field.label)
                if value is not None:
                    break
        if value is None and any(
            marker in lowered_description
            for marker in _STORY_FALLBACK_MARKERS.get(field.key, ())
        ):
            value = issue.description  # stated as a sentence, not a section
        fields[field.key] = value
    return fields


def review_ticket_fields(fields: dict[str, object], template: TicketTemplate) -> dict:
    """Review a field mapping against a template; no scores, no invention.

    Returns mandatory findings (required_field/team_policy sections missing —
    hard), advisory suggestions (optional writing quality) and one question
    per mandatory gap. ``ready`` is false only for mandatory findings.
    """
    mandatory: list[dict] = []
    advisory: list[dict] = []
    questions: list[dict] = []
    for field in template.fields:
        if _clean(fields.get(field.key)) is not None:
            continue
        if field.category in ("required_field", "team_policy"):
            required = field.category == "required_field"
            mandatory.append(
                {
                    "key": field.key,
                    "label": field.label,
                    "finding": (
                        f"{field.label} is missing ({'Jira schema requires it' if required else 'team policy requires it'})"
                    ),
                }
            )
            questions.append({"key": field.key, "question": field.question})
        else:
            advisory.append(
                {
                    "key": field.key,
                    "label": field.label,
                    "suggestion": field.hint,
                }
            )
    return {
        "issue_type": template.issue_type,
        "template_version": template.version,
        "mandatory": mandatory,
        "advisory": advisory,
        "questions": questions,
        "ready": not mandatory,
    }
