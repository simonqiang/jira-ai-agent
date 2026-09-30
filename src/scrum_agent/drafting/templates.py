"""Versioned YAML ticket templates (Week 7).

Templates live as YAML files under ``drafting/ticket_templates/`` and are loaded
once per process; each carries its own ``version`` so drafts and any future
change can be traced to the exact template revision that produced them (no
administrative UI in the pilot — edit the YAML and bump the version). Each
field is tagged with a ``category`` that separates:

- ``required_field``: content Jira itself needs for a valid issue of this type.
- ``team_policy``: content this team always requires (mandatory even though
  Jira's schema does not enforce it — for example Bug repro/verification).
- ``advisory``: writing suggestions that improve the draft but are optional.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

FieldCategory = Literal["required_field", "team_policy", "advisory"]


class TemplateField(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    category: FieldCategory
    question: str | None = None
    hint: str | None = None

    @model_validator(mode="after")
    def _check_guidance(self) -> TemplateField:
        if self.category in ("required_field", "team_policy") and not self.question:
            raise ValueError(f"field {self.key!r} needs a question (category {self.category!r})")
        return self


class TicketTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue_type: str
    version: str
    fields: tuple[TemplateField, ...]

    @field_validator("fields")
    @classmethod
    def _unique_keys(cls, value: tuple[TemplateField, ...]) -> tuple[TemplateField, ...]:
        keys = [field.key for field in value]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate field keys in template: {keys}")
        if not value:
            raise ValueError("template must define at least one field")
        return value

    def field(self, key: str) -> TemplateField | None:
        return next((field for field in self.fields if field.key == key), None)


def _parse_template(raw: dict) -> TicketTemplate:
    fields = tuple(TemplateField(**field) for field in raw.get("fields") or ())
    return TicketTemplate(issue_type=raw["issue_type"], version=raw["version"], fields=fields)


def load_templates(directory: Path | None = None) -> dict[str, TicketTemplate]:
    """Load every ``*.yaml`` template, keyed by lower-cased issue type.

    Defaults to the packaged Week 7 templates; a directory can be passed to
    load overrides (tests use this for incomplete/malformed fixtures).
    """
    templates: dict[str, TicketTemplate] = {}
    if directory is None:
        source = resources.files("scrum_agent.drafting") / "ticket_templates"
        paths = sorted(p for p in source.iterdir() if p.name.endswith(".yaml"))
    else:
        paths = sorted(directory.glob("*.yaml"))
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"template {path} must contain a YAML mapping")
        template = _parse_template(raw)
        key = template.issue_type.strip().lower()
        if key in templates:
            raise ValueError(f"duplicate template for issue type {template.issue_type!r}")
        templates[key] = template
    if not templates:
        raise ValueError(f"no templates found in {directory or 'the packaged templates dir'}")
    return templates


@lru_cache(maxsize=1)
def default_templates() -> dict[str, TicketTemplate]:
    """The packaged Week 7 templates, loaded once per process."""
    return load_templates()


def get_template(templates: dict[str, TicketTemplate], issue_type: str) -> TicketTemplate:
    """Look up a template by issue type name (case-insensitive, exact match).

    Unlike search's partial status/type matching, drafting requires an exact
    identity/version match: silently guessing the wrong template would let
    team-policy requirements slip.
    """
    key = issue_type.strip().lower()
    template = templates.get(key)
    if template is None:
        known = ", ".join(sorted(t.issue_type for t in templates.values()))
        raise ValueError(f"No template for issue type {issue_type!r}; known types: {known}")
    return template
