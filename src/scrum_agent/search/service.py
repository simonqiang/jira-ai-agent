"""Week 2 search service: issue-key lookup, board/sprint selection and typed filters.

Every operation goes through the Jira client, whose reads are checked against the
centralized :class:`~scrum_agent.auth.PilotScope`. Typed adapter errors (auth,
permission, rate limit) propagate unchanged: their messages state the action to
take, and the pilot does not retry automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime

from scrum_agent.jira.client import JiraClient
from scrum_agent.jira.models import Issue, Sprint
from scrum_agent.search.errors import AmbiguousSprintError, SprintNotFoundError
from scrum_agent.search.filters import IssueFilters
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

    def search_issues(
        self,
        filters: IssueFilters,
        *,
        max_results_per_page: int = 50,
        max_pages: int = 20,
    ) -> SearchResult:
        """Search issues with typed filters; return the complete, fresh result."""
        jql = filters.to_jql()
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
