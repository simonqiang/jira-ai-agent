"""Trusted pilot scope and centralized access checks (Week 2).

Every data path - client reads, search, and later reports, exports and
retrieval - must route authorization through :class:`PilotScope` rather than
re-implementing checks. App roles never widen the Jira permissions of the
pilot user's token.
"""

from scrum_agent.auth.scope import PilotScope

__all__ = ["PilotScope"]
