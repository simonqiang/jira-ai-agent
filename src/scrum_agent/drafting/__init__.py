"""Week 7: draft tickets from team-standard templates.

``scrum_agent.drafting`` turns a rough request into an editable Story, Bug or
Task draft without ever making a Jira change. Templates are versioned YAML
(``templates.py``); drafting (``draft.py``) separates required Jira fields,
mandatory team policy and advisory writing suggestions, and never invents
missing content — gaps become explicit open questions instead.
"""

from __future__ import annotations

from scrum_agent.drafting.draft import build_draft
from scrum_agent.drafting.templates import (
    TemplateField,
    TicketTemplate,
    default_templates,
    get_template,
    load_templates,
)

__all__ = [
    "TemplateField",
    "TicketTemplate",
    "load_templates",
    "default_templates",
    "get_template",
    "build_draft",
]
