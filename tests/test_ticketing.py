"""Week 8 approved-ticket creation safety tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


class MemoryStorage:
    def __init__(self) -> None:
        self.drafts: dict[int, dict] = {}
        self.approvals: dict[int, dict] = {}
        self.executions: dict[int, dict] = {}
        self._next_id = 1

    def create_draft(self, **row: object) -> int:
        draft_id = self._next_id
        self._next_id += 1
        self.drafts[draft_id] = {"id": draft_id, **row}
        return draft_id

    def get_draft(self, draft_id: int) -> dict | None:
        return self.drafts.get(draft_id)

    def create_approval(self, **row: object) -> int:
        approval_id = self._next_id
        self._next_id += 1
        self.approvals[approval_id] = {"id": approval_id, **row}
        return approval_id

    def get_approval(self, approval_id: int) -> dict | None:
        return self.approvals.get(approval_id)

    def create_execution(self, **row: object) -> int:
        execution_id = self._next_id
        self._next_id += 1
        self.executions[execution_id] = {"id": execution_id, **row}
        return execution_id

    def get_execution_by_approval(self, approval_id: int) -> dict | None:
        return next(
            (
                execution
                for execution in self.executions.values()
                if execution["approval_id"] == approval_id
            ),
            None,
        )

    def update_execution(self, execution_id: int, **changes: object) -> None:
        self.executions[execution_id].update(changes)


class FakeJira:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.marker_matches: dict[str, dict] = {}
        self.fail_after_create = False

    def validate_create_fields(self, issue_type: str, fields: dict) -> None:
        assert issue_type == "Bug"
        assert fields["project"] == {"key": "PAY"}

    def create_issue(self, fields: dict) -> dict:
        self.created.append(fields)
        created = {"id": "10042", "key": "PAY-42"}
        marker = fields["labels"][-1]
        self.marker_matches[marker] = created
        if self.fail_after_create:
            from scrum_agent.jira.errors import JiraApiError

            raise JiraApiError("Cannot reach Jira; check network access and retry")
        return created

    def get_issue(self, key: str) -> dict:
        return {"key": key}

    def find_by_marker(self, marker: str) -> dict | None:
        return self.marker_matches.get(marker)


def complete_bug() -> dict[str, str]:
    return {
        "summary": "Export fails",
        "environment": "production",
        "steps_to_reproduce": "Open export and choose CSV.",
        "expected": "A CSV downloads.",
        "actual": "The page returns an error.",
        "impact": "Finance cannot export reports.",
        "verification_criteria": "A CSV downloads for a valid report.",
    }


def service(now: datetime | None = None):
    from scrum_agent.ticketing.approvals import TicketApprovalService

    storage = MemoryStorage()
    jira = FakeJira()
    current = now or datetime(2026, 10, 1, tzinfo=UTC)
    tickets = TicketApprovalService(storage, jira, project_key="PAY", now=lambda: current)
    return tickets, storage, jira


def test_execution_requires_matching_unexpired_approval_and_creates_exact_payload() -> None:
    tickets, storage, jira = service()

    draft = tickets.create_draft("Bug", complete_bug(), creator="local-pilot")
    approval = tickets.approve(draft["id"], approver="local-pilot")
    result = tickets.execute(approval["id"], approver="local-pilot")

    assert result["status"] == "succeeded"
    assert result["issue_key"] == "PAY-42"
    assert len(jira.created) == 1
    assert jira.created[0] == draft["payload"]
    assert storage.executions[result["execution_id"]]["status"] == "succeeded"


def test_execution_is_idempotent_and_does_not_duplicate_clicks() -> None:
    tickets, _, jira = service()
    draft = tickets.create_draft("Bug", complete_bug(), creator="local-pilot")
    approval = tickets.approve(draft["id"], approver="local-pilot")

    first = tickets.execute(approval["id"], approver="local-pilot")
    second = tickets.execute(approval["id"], approver="local-pilot")

    assert second == first
    assert len(jira.created) == 1


def test_execution_rejects_wrong_user_and_expired_approval() -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    tickets, _, _ = service(now)
    draft = tickets.create_draft("Bug", complete_bug(), creator="local-pilot")
    approval = tickets.approve(draft["id"], approver="local-pilot")

    with pytest.raises(PermissionError, match="approver"):
        tickets.execute(approval["id"], approver="someone-else")

    expired, _, _ = service(now)
    draft = expired.create_draft("Bug", complete_bug(), creator="local-pilot")
    approval = expired.approve(draft["id"], approver="local-pilot")
    expired._now = lambda: now + timedelta(minutes=16)
    with pytest.raises(ValueError, match="expired"):
        expired.execute(approval["id"], approver="local-pilot")


def test_indeterminate_create_reconciles_marker_without_retrying() -> None:
    tickets, storage, jira = service()
    jira.fail_after_create = True
    draft = tickets.create_draft("Bug", complete_bug(), creator="local-pilot")
    approval = tickets.approve(draft["id"], approver="local-pilot")

    result = tickets.execute(approval["id"], approver="local-pilot")

    assert result["status"] == "succeeded"
    assert len(jira.created) == 1
    assert storage.executions[result["execution_id"]]["reconciled"] is True
