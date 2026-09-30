"""Week 8 ticket creation: freeze, approve, execute once, then verify."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from scrum_agent.drafting.draft import build_draft
from scrum_agent.drafting.templates import default_templates, get_template
from scrum_agent.jira.errors import JiraApiError

_APPROVAL_LIFETIME = timedelta(minutes=15)


class TicketStorage(Protocol):
    def create_draft(self, **row: object) -> int: ...

    def get_draft(self, draft_id: int) -> dict | None: ...

    def create_approval(self, **row: object) -> int: ...

    def get_approval(self, approval_id: int) -> dict | None: ...

    def create_execution(self, **row: object) -> int: ...

    def get_execution_by_approval(self, approval_id: int) -> dict | None: ...

    def update_execution(self, execution_id: int, **changes: object) -> None: ...


class JiraTicketClient(Protocol):
    def validate_create_fields(self, issue_type: str, fields: dict) -> None: ...

    def create_issue(self, fields: dict) -> dict: ...

    def get_issue(self, key: str) -> object: ...

    def find_by_marker(self, marker: str) -> dict | None: ...


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _summary(fields: dict[str, str]) -> str:
    for key in ("summary", "objective", "goal"):
        value = fields.get(key)
        if value:
            return value
    raise ValueError("The draft has no Jira summary field")


class TicketApprovalService:
    """Creates exact, one-use approvals around a frozen Jira create payload."""

    def __init__(
        self,
        storage: TicketStorage,
        jira: JiraTicketClient,
        *,
        project_key: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._jira = jira
        self._project_key = project_key
        self._now = now or (lambda: datetime.now(UTC))

    def create_draft(self, issue_type: str, fields: dict[str, str], *, creator: str) -> dict:
        template = get_template(default_templates(), issue_type)
        draft = build_draft(template, fields)
        if not draft["ready"]:
            raise ValueError("Draft is incomplete; answer every required and team-policy question")
        marker = f"scrum-agent-req-{uuid4()}"
        payload = {
            "project": {"key": self._project_key},
            "issuetype": {"name": template.issue_type},
            "summary": _summary(fields),
            "description": draft["rendered"] + f"\n[Scrum agent request: {marker}]\n",
            "labels": [marker],
        }
        draft_id = self._storage.create_draft(
            creator=creator,
            issue_type=template.issue_type,
            template_version=template.version,
            payload=payload,
            payload_hash=_payload_hash(payload),
            correlation_marker=marker,
            created_at=self._now(),
        )
        return {"id": draft_id, "payload": payload, "payload_hash": _payload_hash(payload)}

    def approve(self, draft_id: int, *, approver: str) -> dict:
        draft = self._storage.get_draft(draft_id)
        if draft is None:
            raise ValueError("Draft not found")
        approved_at = self._now()
        approval_id = self._storage.create_approval(
            draft_id=draft_id,
            approver=approver,
            payload_hash=draft["payload_hash"],
            approved_at=approved_at,
            expires_at=approved_at + _APPROVAL_LIFETIME,
        )
        return {"id": approval_id, "expires_at": (approved_at + _APPROVAL_LIFETIME).isoformat()}

    def execute(self, approval_id: int, *, approver: str) -> dict:
        approval = self._storage.get_approval(approval_id)
        if approval is None:
            raise ValueError("Approval not found")
        if approval["approver"] != approver:
            raise PermissionError("Only the recorded approver can execute this approval")
        if self._now() >= approval["expires_at"]:
            raise ValueError("Approval has expired")

        prior = self._storage.get_execution_by_approval(approval_id)
        if prior is not None:
            return {
                "status": prior["status"],
                "execution_id": prior["id"],
                "issue_key": prior.get("issue_key"),
                "reconciled": prior.get("reconciled", False),
            }

        draft = self._storage.get_draft(approval["draft_id"])
        if draft is None or draft["payload_hash"] != approval["payload_hash"]:
            raise ValueError("Draft changed after approval; approve its current payload")

        execution_id = self._storage.create_execution(
            approval_id=approval_id,
            payload_hash=draft["payload_hash"],
            correlation_marker=draft["correlation_marker"],
            status="executing",
            started_at=self._now(),
        )
        try:
            self._jira.validate_create_fields(draft["issue_type"], draft["payload"])
            created = self._jira.create_issue(draft["payload"])
            issue_key = created.get("key")
            if not isinstance(issue_key, str) or not issue_key:
                raise JiraApiError("Jira create response omitted the issue key")
            self._jira.get_issue(issue_key)
            result = {
                "status": "succeeded",
                "execution_id": execution_id,
                "issue_key": issue_key,
                "reconciled": False,
            }
        except JiraApiError:
            matched = self._jira.find_by_marker(draft["correlation_marker"])
            issue_key = matched.get("key") if matched else None
            if isinstance(issue_key, str) and issue_key:
                self._jira.get_issue(issue_key)
                result = {
                    "status": "succeeded",
                    "execution_id": execution_id,
                    "issue_key": issue_key,
                    "reconciled": True,
                }
            else:
                result = {"status": "outcome_unknown", "execution_id": execution_id}
        self._storage.update_execution(
            execution_id,
            **{key: value for key, value in result.items() if key != "execution_id"},
            finished_at=self._now(),
        )
        return result
