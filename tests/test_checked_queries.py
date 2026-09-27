"""Every checked query must hold: this file is the Week 2 exit-check runner.

The same set is reused in Week 3 to verify the ADK agent's answers, so the
fixtures and expectations live in `checked_queries.py`, not inline here.
"""

from __future__ import annotations

import httpx
import pytest

from scrum_agent.jira.client import JiraClient
from scrum_agent.search.service import SearchService
from tests.checked_queries import CHECKED_QUERIES
from tests.conftest import make_settings


@pytest.mark.parametrize("query", CHECKED_QUERIES, ids=lambda query: query.name)
def test_checked_query(query) -> None:
    jira = query.make_jira()
    with JiraClient(make_settings(), transport=httpx.MockTransport(jira.handler)) as client:
        service = SearchService(client)
        query.verify(service, jira)


def test_the_checked_set_covers_the_roadmap_scenarios() -> None:
    """Guard: the roadmap's four required scenarios stay represented by name."""
    names = {query.name for query in CHECKED_QUERIES}
    assert any("empty" in name for name in names), "empty-results scenario missing"
    assert any("ambiguous" in name for name in names), "ambiguity scenario missing"
    assert any("multiple-pages" in name for name in names), "pagination scenario missing"
    assert any("project-scope" in name for name in names), "board-scope scenario missing"
