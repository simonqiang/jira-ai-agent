"""Week 2 search service: issue-key lookup, board/sprint selection and typed filters.

Every operation goes through the Jira client, whose reads are checked against the
centralized :class:`~scrum_agent.auth.PilotScope`. Typed adapter errors (auth,
permission, rate limit) propagate unchanged: their messages state the action to
take, and the pilot does not retry automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime

from scrum_agent.jira.client import JiraClient
from scrum_agent.jira.errors import JiraError
from scrum_agent.jira.models import Issue, Sprint
from scrum_agent.search.errors import AmbiguousSprintError, SprintNotFoundError
from scrum_agent.search.filters import UNASSIGNED, IssueFilters
from scrum_agent.search.models import SearchResult


class SearchService:
    """Search the pilot board with typed filters and sprint selection."""

    def __init__(self, client: JiraClient):
        self._client = client

    @property
    def scope(self):
        """The pilot scope in force (delegates to the client's checks)."""
        return self._client.scope

    # -- lookups ------------------------------------------------------------

    def get_issue(self, issue_key: str) -> Issue:
        """Look up one issue by key (scope-checked by the client)."""
        return self._client.get_issue(issue_key)

    # -- sprint selection ---------------------------------------------------

    def list_sprints(
        self,
        *,
        states: tuple[str, ...] = (),
        max_results_per_page: int = 50,
        max_pages: int = 20,
    ) -> tuple[Sprint, ...]:
        """List the pilot board's sprints, optionally filtered by state."""
        return tuple(
            self._client.iter_board_sprints(
                self._client.scope.board_id,
                states=states,
                max_results_per_page=max_results_per_page,
                max_pages=max_pages,
            )
        )

    def resolve_sprint(
        self,
        reference: str | int,
        *,
        states: tuple[str, ...] = (),
    ) -> Sprint:
        """Resolve a sprint ID or name to one sprint on the pilot board.

        A numeric reference is a sprint ID. Names prefer exact matches; a unique
        substring match is accepted. Zero matches raise `SprintNotFoundError`;
        multiple matches raise `AmbiguousSprintError` carrying the candidates so
        the caller can prompt for a selection instead of guessing.
        """
        if isinstance(reference, int):
            return self._client.get_sprint(reference)
        text = reference.strip()
        if text.isdigit():
            return self._client.get_sprint(int(text))
        if not text:
            raise SprintNotFoundError("Sprint reference must not be blank")
        sprints = self.list_sprints(states=states)
        exact = [sprint for sprint in sprints if sprint.name == text]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise self._ambiguous(text, exact)
        partial = [sprint for sprint in sprints if text.lower() in sprint.name.lower()]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise self._ambiguous(text, partial)
        raise SprintNotFoundError(
            f"No sprint matching {text!r} was found on board {self._client.scope.board_id}"
        )

    @staticmethod
    def _ambiguous(text: str, candidates: list[Sprint]) -> AmbiguousSprintError:
        listing = ", ".join(f"{s.id} {s.name!r} ({s.state})" for s in candidates)
        return AmbiguousSprintError(
            f"Sprint reference {text!r} matches {len(candidates)} sprints: {listing}; "
            "pass a sprint ID or a more exact name",
            candidates=candidates,
        )

    # -- search -------------------------------------------------------------

    @staticmethod
    def _expand(values: tuple[str, ...], universe: tuple[str, ...]) -> tuple[str, ...]:
        """Replace each filter value with exact field names matching it partially.

        An exact (case-insensitive) match wins; otherwise every name containing
        the value matches; otherwise the value passes through unchanged, so an
        unknown value still yields an accurate zero instead of an error.
        """
        names = [(name, name.casefold()) for name in universe]
        expanded: list[str] = []
        for value in values:
            key = value.casefold()
            matches = [name for name, lowered in names if lowered == key]
            if not matches:
                matches = [name for name, lowered in names if key in lowered]
            expanded.extend(matches or [value])
        return tuple(dict.fromkeys(expanded))

    def _resolve_filters(self, filters: IssueFilters) -> IssueFilters:
        """Expand partial status/type/assignee values to the project's exact names.

        JQL `~` only works on text fields, so partial matching for these pickers
        goes through the client's metadata endpoints instead. Each lookup is
        best-effort: on any failure that field group keeps its original exact
        values, matching Week 2 behaviour — an optional enhancement must never
        break the search itself (scoped tokens, for example, commonly lack the
        user scope `user/assignable/search` needs). Labels always stay exact
        (Jira has no label enumeration).
        """
        update: dict[str, tuple[str, ...]] = {}
        if filters.statuses or filters.issue_types:
            try:
                status_names, type_names = self._client.project_field_names()
                update["statuses"] = self._expand(filters.statuses, status_names)
                update["issue_types"] = self._expand(filters.issue_types, type_names)
            except JiraError:
                pass
        named = tuple(a for a in filters.assignees if a != UNASSIGNED)
        if named:
            try:
                # ponytail: match by display name; duplicate display names would over-select
                update["assignees"] = self._expand(named, self._client.assignable_user_names()) + (
                    (UNASSIGNED,) if UNASSIGNED in filters.assignees else ()
                )
            except JiraError:
                pass
        return filters.model_copy(update=update)

    def search_issues(
        self,
        filters: IssueFilters,
        *,
        max_results_per_page: int = 50,
        max_pages: int = 20,
    ) -> SearchResult:
        """Search issues with typed filters; return the complete, fresh result.

        Status, issue type and assignee values may be partial names (for example
        ``"progress"`` matches ``"In Progress"``); they are expanded to the
        project's exact values before the JQL compiles.
        """
        jql = self._resolve_filters(filters).to_jql()
        issues = tuple(
            self._client.iter_search_jql(
                jql,
                max_results_per_page=max_results_per_page,
                max_pages=max_pages,
            )
        )
        sprint = self._client.get_sprint(filters.sprint_id) if filters.sprint_id else None
        return SearchResult(
            issues=issues,
            jql=jql,
            sprint=sprint,
            fetched_at=datetime.now(UTC),
        )

    def search_sprint(
        self,
        reference: str | int,
        filters: IssueFilters | None = None,
        *,
        max_results_per_page: int = 50,
        max_pages: int = 20,
    ) -> SearchResult:
        """Resolve a sprint reference, then search within that sprint."""
        sprint = self.resolve_sprint(reference)
        base = filters if filters is not None else IssueFilters()
        if base.sprint_id is not None and base.sprint_id != sprint.id:
            raise ValueError(
                f"filters carry sprint_id={base.sprint_id} but the reference resolves "
                f"to sprint {sprint.id}; pass the sprint only once"
            )
        return self.search_issues(
            base.model_copy(update={"sprint_id": sprint.id}),
            max_results_per_page=max_results_per_page,
            max_pages=max_pages,
        )
