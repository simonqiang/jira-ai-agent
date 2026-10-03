"""Deterministic ticket-quality checker tests (design spec §3).

The checker is pure: template metadata in, findings out. Only mandatory
findings (required_field/team_policy) block readiness; advisory suggestions
never do, and no numeric score exists anywhere.
"""

from __future__ import annotations

import json

from scrum_agent.drafting import default_templates, get_template
from scrum_agent.jira.models import Issue


def _story():
    return get_template(default_templates(), "Story")


def _bug():
    return get_template(default_templates(), "Bug")


def test_complete_story_is_ready_with_no_findings() -> None:
    from scrum_agent.ticketing.quality import review_ticket_fields

    fields = {
        "role": "finance user",
        "goal": "download invoices",
        "benefit": "no manual transfer",
        "scope": "In: download. Out: backfill.",
        "acceptance_criteria": "Given the portal, when I click export, then the CSV downloads.",
    }
    review = review_ticket_fields(fields, _story())
    assert review["ready"] is True
    assert review["mandatory"] == []
    assert review["questions"] == []
    assert review["issue_type"] == "Story"


def test_missing_team_policy_field_is_a_hard_finding_with_question() -> None:
    from scrum_agent.ticketing.quality import review_ticket_fields

    review = review_ticket_fields({"role": "a", "goal": "b", "benefit": "c"}, _story())
    assert review["ready"] is False
    keys = [item["key"] for item in review["mandatory"]]
    assert keys == ["scope", "acceptance_criteria"]
    asked = {item["key"]: item["question"] for item in review["questions"]}
    assert asked["scope"] == _story().field("scope").question


def test_advisory_gap_never_blocks_readiness() -> None:
    from scrum_agent.ticketing.quality import review_ticket_fields

    fields = {
        "role": "a",
        "goal": "b",
        "benefit": "c",
        "scope": "s",
        "acceptance_criteria": "ac",
    }
    review = review_ticket_fields(fields, _story())
    assert review["ready"] is True
    assert {item["key"] for item in review["advisory"]} == {"context", "dependencies", "open_questions"}
    assert all(item["suggestion"] for item in review["advisory"])


def test_no_numeric_score_and_no_invented_content() -> None:
    from scrum_agent.ticketing.quality import review_ticket_fields

    review = review_ticket_fields({}, _bug())
    text = json.dumps(review).lower()
    assert "score" not in text
    for item in review["mandatory"] + review["advisory"]:
        assert not item.get("finding", "").strip().startswith("the repro steps are")


def test_extract_template_fields_maps_direct_and_headings() -> None:
    from scrum_agent.ticketing.quality import extract_template_fields

    issue = Issue(
        key="PAY-90",
        id="90",
        summary="Export fails",
        status="To Do",
        issue_type="Bug",
        description=(
            "Export crashes.\n\n"
            "**Environment:** production, release 4.2\n\n"
            "**Steps to reproduce:**\n1. Open export\n2. Click CSV\n\n"
            "**Expected:**\nfile downloads\n\n**Actual:**\nerror page\n\n"
            "**Impact:**\nfinance blocked\n\n**Verification criteria:**\nexport succeeds"
        ),
        acceptance_criteria="Given export, when clicked, then CSV downloads.",
    )
    fields = extract_template_fields(issue, _bug())
    assert fields["summary"] == "Export fails"
    assert fields["environment"] == "production, release 4.2"
    assert fields["steps_to_reproduce"] == "1. Open export\n2. Click CSV"
    assert fields["expected"] == "file downloads"
    assert fields["actual"] == "error page"
    assert fields["impact"] == "finance blocked"
    assert fields["verification_criteria"] == "export succeeds"


def test_extract_template_fields_story_fallback_and_missing_sections() -> None:
    from scrum_agent.ticketing.quality import extract_template_fields

    issue = Issue(
        key="PAY-3",
        id="3",
        summary="Statement export",
        status="To Do",
        issue_type="Story",
        description="As a finance user, I want to export statements so that month-end closes faster.",
    )
    fields = extract_template_fields(issue, _story())
    assert fields["role"] is not None
    assert fields["goal"] is not None
    assert fields["benefit"] is not None
    assert fields["scope"] is None
    assert fields["acceptance_criteria"] is None
