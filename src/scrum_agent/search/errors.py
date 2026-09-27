"""Typed errors for the search layer.

These are unrelated to the Jira adapter's errors: they describe search-level
outcomes (no match, ambiguous reference) that callers must turn into a question
or an accurate "no results" answer - never a guessed result.
"""

from __future__ import annotations

from collections.abc import Iterable

from scrum_agent.jira.models import Sprint


class SearchError(Exception):
    """Base class for search-layer errors."""


class SprintNotFoundError(SearchError):
    """No sprint on the pilot board matches the reference."""


class AmbiguousSprintError(SearchError):
    """Multiple sprints match; the caller must prompt for a selection."""

    def __init__(self, message: str, *, candidates: Iterable[Sprint]):
        super().__init__(message)
        self.candidates = tuple(candidates)
