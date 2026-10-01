"""Week 9 reviewed-issue-update safety tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scrum_agent.jira.errors import JiraApiError
from scrum_agent.jira.models import Issue
from tests.test_ticketing import MemoryStorage as MemoryUpdateStorage


def sample_issue(**overrides: object) -> Issue:
    values: dict = dict(
        key="PAY-42",
        id="10042",
        summary="Export fails",
        status="To Do",
        issue_type="Bug",
        description="Current description.",
        acceptance_criteria=None,
        labels=("export",),
    )
    values.update(overrides)
    return Issue(**values)


class FakeUpdateJira:
    """Fixture issue store: reads see every applied write, exactly as sent."""

    def __init__(self, initial: Issue) -> None:
        self.issues = {initial.key: initial}
        self.updates: list[tuple[str, dict]] = []
        self.fail_write = False  # raise after (optionally) applying
        self.apply_writes = True  # simulate a write that never landed

    def get_issue(self, key: str) -> Issue:
        return self.issues[key]

    def update_issue(self, key: str, fields: dict) -> None:
        self.updates.append((key, dict(fields)))
        if self.apply_writes:
            self.issues[key] = self._apply(self.issues[key], fields)
        if self.fail_write:
            raise JiraApiError("Cannot reach Jira; check network access and retry")

    def edit_externally(self, key: str, **changes: object) -> None:
        """Someone else edits the issue between proposal and execution."""
        self.issues[key] = self.issues[key].model_copy(update=changes)

    @staticmethod
    def _apply(issue: Issue, fields: dict) -> Issue:
        mapping = {"customfield_10350": "acceptance_criteria", "duedate": "due_date"}
        changes = {
            mapping.get(field_id, field_id): (tuple(value) if field_id == "labels" else value)
            for field_id, value in fields.items()
        }
        return issue.model_copy(update=changes)


def service(now: datetime | None = None):
    from scrum_agent.ticketing.updates import TicketUpdateService

    storage = MemoryUpdateStorage()
    jira = FakeUpdateJira(sample_issue())
    current = now or datetime(2026, 10, 2, tzinfo=UTC)
    return TicketUpdateService(storage, jira, now=lambda: current), storage, jira


def proposed_and_approved(now: datetime | None = None, **changes: object):
    tickets, storage, jira = service(now)
    if not changes:
        changes = {
            "acceptance_criteria": "Given a valid report, When I export, Then a CSV downloads."
        }
    proposal = tickets.propose_update("PAY-42", changes, creator="local-pilot")
    approval = tickets.approve_update(proposal["id"], approver="local-pilot")
    return tickets, storage, jira, proposal, approval


def test_proposal_diffs_only_the_changed_fields() -> None:
    tickets, storage, jira, proposal, _ = proposed_and_approved()

    assert proposal["issue_key"] == "PAY-42"
    assert set(proposal["diff"]) == {"acceptance_criteria"}
    assert proposal["diff"]["acceptance_criteria"] == {
        "old": None,
        "new": "Given a valid report, When I export, Then a CSV downloads.",
    }
    row = storage.proposals[proposal["id"]]
    assert row["base"] == {"acceptance_criteria": None}
    assert row["changes"] == {"acceptance_criteria": proposal["diff"]["acceptance_criteria"]["new"]}


def test_proposal_rejects_unknown_fields_noops_and_bad_values() -> None:
    tickets, *_ = service()

    with pytest.raises(ValueError, match="Unsupported update field"):
        tickets.propose_update("PAY-42", {"priority": "High"}, creator="local-pilot")
    with pytest.raises(ValueError, match="already matches"):
        tickets.propose_update("PAY-42", {"description": "Current description."}, creator="x")
    with pytest.raises(ValueError, match="spaces"):
        tickets.propose_update("PAY-42", {"labels": ["a b"]}, creator="x")
    with pytest.raises(ValueError, match="ISO date"):
        tickets.propose_update("PAY-42", {"due_date": "soon"}, creator="x")
    with pytest.raises(ValueError, match="must be a string"):
        tickets.propose_update("PAY-42", {"summary": 7}, creator="x")


def test_mixed_change_and_noop_request_executes_cleanly() -> None:
    tickets, storage, jira, proposal, approval = proposed_and_approved(
        summary="Renamed export bug",
        description="Current description.",  # no-op alongside the real change
    )

    assert set(proposal["diff"]) == {"summary"}
    result = tickets.execute_update(approval["id"], approver="local-pilot")

    assert result["status"] == "succeeded"
    assert jira.updates == [("PAY-42", {"summary": "Renamed export bug"})]
    assert storage.proposals[proposal["id"]]["base"] == {"summary": "Export fails"}


def test_execute_applies_only_reviewed_fields_and_verifies() -> None:
    tickets, storage, jira, proposal, approval = proposed_and_approved()

    result = tickets.execute_update(approval["id"], approver="local-pilot")

    assert result["status"] == "succeeded"
    assert jira.updates == [
        ("PAY-42", {"customfield_10350": proposal["diff"]["acceptance_criteria"]["new"]})
    ]
    assert all(item["match"] for item in result["verified"].values())
    row = storage.executions[result["execution_id"]]
    assert row["status"] == "succeeded"
    assert row["requested"] == {  # audit trail: exactly what was asked for
        "acceptance_criteria": proposal["diff"]["acceptance_criteria"]["new"]
    }
    assert row["verified"] == result["verified"]


def test_execute_rejects_stale_when_a_reviewed_field_changed() -> None:
    tickets, storage, jira, _, approval = proposed_and_approved()
    jira.edit_externally("PAY-42", acceptance_criteria="Someone else wrote criteria first.")

    result = tickets.execute_update(approval["id"], approver="local-pilot")

    assert result["status"] == "rejected_stale"
    assert result["verified"]["conflicts"]["acceptance_criteria"]["now"] == (
        "Someone else wrote criteria first."
    )
    assert jira.updates == []  # nothing was written
    assert storage.executions[result["execution_id"]]["status"] == "rejected_stale"


def test_execute_proceeds_and_preserves_unrelated_concurrent_edits() -> None:
    tickets, _, jira, _, approval = proposed_and_approved()
    jira.edit_externally("PAY-42", summary="Summary edited by someone else meanwhile")

    result = tickets.execute_update(approval["id"], approver="local-pilot")

    assert result["status"] == "succeeded"
    assert set(jira.updates[0][1]) == {"customfield_10350"}  # only the reviewed field
    assert jira.get_issue("PAY-42").summary == "Summary edited by someone else meanwhile"


def test_write_error_reconciles_from_the_readback() -> None:
    tickets, storage, jira, _, approval = proposed_and_approved()
    jira.fail_write = True  # write lands, then the connection dies

    result = tickets.execute_update(approval["id"], approver="local-pilot")

    assert result["status"] == "succeeded"
    assert result["reconciled"] is True
    assert storage.executions[result["execution_id"]]["status"] == "succeeded"


def test_write_error_that_never_landed_does_not_claim_success() -> None:
    tickets, storage, jira, _, approval = proposed_and_approved()
    jira.fail_write = True
    jira.apply_writes = False

    result = tickets.execute_update(approval["id"], approver="local-pilot")

    assert result["status"] == "failed"
    assert not any(item["match"] for item in result["verified"].values())
    assert storage.executions[result["execution_id"]]["status"] == "failed"


def test_silent_write_that_did_not_stick_fails_verification() -> None:
    tickets, _, jira, _, approval = proposed_and_approved()
    jira.apply_writes = False  # Jira answers 204 but the field did not change

    result = tickets.execute_update(approval["id"], approver="local-pilot")

    assert result["status"] == "verification_failed"
    assert result["verified"]["acceptance_criteria"]["match"] is False


def test_execute_is_idempotent_and_enforces_approver_and_expiry() -> None:
    now = datetime(2026, 10, 2, tzinfo=UTC)
    tickets, _, jira, _, approval = proposed_and_approved(now)

    first = tickets.execute_update(approval["id"], approver="local-pilot")
    second = tickets.execute_update(approval["id"], approver="local-pilot")
    assert second == first
    assert len(jira.updates) == 1

    with pytest.raises(PermissionError, match="approver"):
        tickets.execute_update(approval["id"], approver="someone-else")

    expired, _, _, _, stale_approval = proposed_and_approved(now)
    expired._now = lambda: now + timedelta(minutes=16)
    with pytest.raises(ValueError, match="expired"):
        expired.execute_update(stale_approval["id"], approver="local-pilot")
