"""Week 9 reviewed issue updates: diff, approve, re-read, write, verify.

Flow: fetch the live issue, diff the requested field values against it, freeze
the diff as a proposal, get a human approval, then re-read the issue and apply
only if every changed field still matches what was reviewed. Unrelated fields
are never sent to Jira, so concurrent edits elsewhere survive our write.

Known race (documented, not fixable client-side): Jira Cloud REST offers no
atomic precondition on issue updates (no If-Match/ETag), so an external edit
landing between our pre-write re-read and the PUT can still be overwritten.
The window is seconds and the changelog records both edits; the pilot accepts
this residual risk rather than inventing a false guarantee.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Protocol

from scrum_agent.jira.errors import JiraApiError
from scrum_agent.jira.models import Issue
from scrum_agent.ticketing.approvals import _APPROVAL_LIFETIME, _payload_hash

# Friendly key -> (Jira field id, Issue reader). Adding a field means one
# entry here plus its branch in _normalize; nothing else changes.
_UPDATABLE_FIELDS: dict[str, tuple[str, Callable[[Issue], object]]] = {
    "summary": ("summary", lambda issue: issue.summary or None),
    "description": ("description", lambda issue: issue.description),
    "acceptance_criteria": ("customfield_10350", lambda issue: issue.acceptance_criteria),
    "labels": ("labels", lambda issue: sorted(set(issue.labels))),
    "due_date": ("duedate", lambda issue: issue.due_date),
}

_STRING_FIELDS = frozenset({"summary", "description", "acceptance_criteria"})


class UpdateStorage(Protocol):
    def create_update_proposal(self, **row: object) -> int: ...

    def get_update_proposal(self, proposal_id: int) -> dict | None: ...

    def create_update_approval(self, **row: object) -> int: ...

    def get_update_approval(self, approval_id: int) -> dict | None: ...

    def create_update_execution(self, **row: object) -> int: ...

    def get_update_execution_by_approval(self, approval_id: int) -> dict | None: ...

    def finish_update_execution(
        self, execution_id: int, *, status: str, verified: dict, finished_at: datetime
    ) -> None: ...


class JiraUpdateClient(Protocol):
    def get_issue(self, issue_key: str) -> Issue: ...

    def update_issue(self, issue_key: str, fields: dict) -> None: ...


def _normalize(field: str, value: object) -> object:
    """Canonical form for one field value; proposal and re-read both use it."""
    if field == "labels":
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(label, str) for label in value
        ):
            raise ValueError("labels must be a list of strings")
        labels = sorted({label.strip() for label in value if label.strip()})
        if any(" " in label for label in labels):
            raise ValueError("Jira labels cannot contain spaces")
        return labels
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if field == "due_date":
        if not value.strip():
            return None
        try:
            date.fromisoformat(value.strip())
        except ValueError:
            raise ValueError("due_date must be an ISO date (YYYY-MM-DD)") from None
        return value.strip()
    if field in _STRING_FIELDS:
        return value or None
    raise ValueError(f"Unsupported update field {field!r}")


def _current_values(issue: Issue, fields: dict[str, object]) -> dict[str, object]:
    # Unset fields read back as None; that is a legal current value, unlike a
    # proposed one (clearing is expressed as an empty string).
    return {
        key: (None if raw is None else _normalize(key, raw))
        for key in fields
        for raw in (_UPDATABLE_FIELDS[key][1](issue),)
    }


def _jira_fields(changes: dict[str, object]) -> dict[str, object]:
    return {_UPDATABLE_FIELDS[key][0]: value for key, value in changes.items()}


def _verify(issue: Issue, changes: dict[str, object]) -> dict[str, dict]:
    current = _current_values(issue, changes)
    return {
        key: {"expected": value, "actual": current.get(key), "match": current.get(key) == value}
        for key, value in changes.items()
    }


class TicketUpdateService:
    """Exact, one-use approvals around a reviewed field-level diff."""

    def __init__(
        self,
        storage: UpdateStorage,
        jira: JiraUpdateClient,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._jira = jira
        self._now = now or (lambda: datetime.now(UTC))

    def propose_update(self, issue_key: str, changes: dict, *, creator: str) -> dict:
        if not isinstance(issue_key, str) or not issue_key.strip():
            raise ValueError("issue_key must be a Jira issue key such as PAY-3")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty mapping of field to new value")
        issue = self._jira.get_issue(issue_key.strip())
        wanted = {key: _normalize(key, value) for key, value in changes.items()}
        current = _current_values(issue, wanted)
        diff = {
            key: {"old": current[key], "new": value}
            for key, value in wanted.items()
            if current[key] != value
        }
        if not diff:
            raise ValueError("Every requested value already matches the issue; nothing to change")
        changes = {key: item["new"] for key, item in diff.items()}
        # The stale-check base covers exactly the fields this proposal writes;
        # no-op fields from the request are not part of the write.
        base = {key: current[key] for key in changes}
        payload_hash = _payload_hash({"issue_key": issue.key, "base": base, "changes": changes})
        proposal_id = self._storage.create_update_proposal(
            creator=creator,
            issue_key=issue.key,
            base=base,
            changes=changes,
            payload_hash=payload_hash,
            created_at=self._now(),
        )
        return {
            "id": proposal_id,
            "issue_key": issue.key,
            "summary": issue.summary,
            "diff": diff,
            "unchanged_fields_untouched": True,
            "payload_hash": payload_hash,
        }

    def approve_update(self, proposal_id: int, *, approver: str) -> dict:
        proposal = self._storage.get_update_proposal(proposal_id)
        if proposal is None:
            raise ValueError("Proposal not found")
        approved_at = self._now()
        approval_id = self._storage.create_update_approval(
            proposal_id=proposal_id,
            approver=approver,
            payload_hash=proposal["payload_hash"],
            approved_at=approved_at,
            expires_at=approved_at + _APPROVAL_LIFETIME,
        )
        return {"id": approval_id, "expires_at": (approved_at + _APPROVAL_LIFETIME).isoformat()}

    def execute_update(self, approval_id: int, *, approver: str) -> dict:
        approval = self._storage.get_update_approval(approval_id)
        if approval is None:
            raise ValueError("Approval not found")
        if approval["approver"] != approver:
            raise PermissionError("Only the recorded approver can execute this approval")
        if self._now() >= approval["expires_at"]:
            raise ValueError("Approval has expired")

        prior = self._storage.get_update_execution_by_approval(approval_id)
        if prior is not None:
            return {
                "status": prior["status"],
                "execution_id": prior["id"],
                "issue_key": prior["issue_key"],
                "verified": prior.get("verified"),
            }

        proposal = self._storage.get_update_proposal(approval["proposal_id"])
        if proposal is None or proposal["payload_hash"] != approval["payload_hash"]:
            raise ValueError("Proposal changed after approval; approve its current diff")

        execution_id = self._storage.create_update_execution(
            approval_id=approval_id,
            payload_hash=proposal["payload_hash"],
            issue_key=proposal["issue_key"],
            status="executing",
            requested=proposal["changes"],
            started_at=self._now(),
        )

        # Pre-write re-read: reject the proposal when any field it changes no
        # longer matches the reviewed base. Edits to *other* fields do not
        # block the write and are preserved because we never send them.
        issue = self._jira.get_issue(proposal["issue_key"])
        current = _current_values(issue, proposal["changes"])
        conflicts = {
            key: {"reviewed": proposal["base"][key], "now": current[key]}
            for key in proposal["changes"]
            if current[key] != proposal["base"][key]
        }
        reconciled = False
        if conflicts:
            status, verified = "rejected_stale", {"conflicts": conflicts}
        else:
            try:
                self._jira.update_issue(issue.key, _jira_fields(proposal["changes"]))
                verified = _verify(self._jira.get_issue(issue.key), proposal["changes"])
                status = (
                    "succeeded"
                    if all(item["match"] for item in verified.values())
                    else "verification_failed"
                )
            except JiraApiError:
                # The PUT may have landed despite the error (timeout after the
                # server applied it). Updates are idempotent, so trust the
                # read-back: the same check that would have run anyway.
                verified = _verify(self._jira.get_issue(issue.key), proposal["changes"])
                if all(item["match"] for item in verified.values()):
                    status, reconciled = "succeeded", True
                else:
                    status = "failed"
        self._storage.finish_update_execution(
            execution_id, status=status, verified=verified, finished_at=self._now()
        )
        return {
            "status": status,
            "execution_id": execution_id,
            "issue_key": proposal["issue_key"],
            "verified": verified,
            **({"reconciled": True} if reconciled else {}),
        }
