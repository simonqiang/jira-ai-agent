"""Durable asynchronous report jobs (Week 5).

``build_sprint_report`` never blocks the conversation: it resolves the sprint,
persists a job row keyed by request identity (board, sprint, cutoff hour), and
returns a handle with an estimate. A local worker — the web process's
background task, or the CLI inline — claims and executes jobs; job state lives
in PostgreSQL, so a restarted process requeues an orphaned ``running`` row and
finishes the job, and duplicate delivery of the same request returns the
existing job instead of executing twice.

Execution reads persisted inputs (Week 4 snapshots) and revalidates access
with one live scoped search for the sprint: stored issues that no longer
appear are excluded from details and totals, so revoked access cannot
re-enter reports through the historical path.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from scrum_agent.config import Settings, require_database_settings
from scrum_agent.jira.client import JiraClient
from scrum_agent.search.service import SearchService
from scrum_agent.storage.repository import PgStorage


def request_key(board_id: int, sprint_id: int, cutoff_hour: datetime) -> str:
    """Job identity: the same report request delivered twice maps to one row."""
    bucket = cutoff_hour.replace(minute=0, second=0, microsecond=0)
    identity = f"sprint_report:{board_id}:{sprint_id}:{bucket.isoformat()}"
    return hashlib.sha256(identity.encode()).hexdigest()


def job_view(row: dict) -> dict:
    """JSON-safe job row for tool payloads and web rendering."""
    view = {
        "job_id": row["id"],
        "status": row["status"],
        "sprint_id": row["sprint_id"],
        "board_id": row["board_id"],
        "attempts": row["attempts"],
        "estimate_seconds": row["estimate_seconds"],
        "requested_at": row["requested_at"].isoformat(timespec="seconds"),
        "error": row["error"],
    }
    if row["finished_at"] is not None:
        view["finished_at"] = row["finished_at"].isoformat(timespec="seconds")
    if row["report"] is not None:
        view["report"] = row["report"]
    return view


class ReportJobs:
    """Submits, executes and reads sprint-report jobs over durable storage."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: JiraClient | None = None,
        storage: PgStorage | None = None,
    ) -> None:
        require_database_settings(settings)
        self._settings = settings
        self._client = client
        self._storage = storage
        self._conn: object | None = None

    # -- dependencies (lazy so tests can inject fakes) ------------------------

    def client(self) -> JiraClient:
        if self._client is None:
            self._client = JiraClient(self._settings)
        return self._client

    def storage(self) -> PgStorage:
        if self._storage is None:
            from scrum_agent.storage.db import connect

            self._conn = connect(self._settings)
            self._storage = PgStorage(self._conn)
        return self._storage

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        if self._conn is not None:
            self._conn.close()

    # -- submit ---------------------------------------------------------------

    def submit(self, sprint_reference: str | int) -> dict:
        """Resolve the sprint and persist (or find) the job; returns a handle."""
        service = SearchService(self.client())
        sprint = service.resolve_sprint(sprint_reference)
        now = datetime.now(UTC)
        stored = self.storage().live_snapshots()
        estimate = max(2, min(30, 2 + len(stored) // 50))
        key = request_key(self.client().scope.board_id, sprint.id, now)
        job_id, created = self.storage().submit_report_job(
            request_key=key,
            board_id=self.client().scope.board_id,
            sprint_id=sprint.id,
            estimate_seconds=estimate,
        )
        row = self.storage().get_report_job(job_id)
        view = job_view(row)
        view["estimate_seconds"] = estimate
        view["reused"] = not created
        view["sprint_name"] = sprint.name
        return view

    # -- worker ---------------------------------------------------------------

    def run_once(self) -> int | None:
        """Claim and execute at most one job; returns its id, or None."""
        row = self.storage().claim_next_report_job()
        if row is None:
            return None
        self.execute(row["id"])
        return row["id"]

    def execute(self, job_id: int) -> None:
        """Build the report from persisted inputs and store it on the job."""
        storage = self.storage()
        row = storage.get_report_job(job_id)
        if row is None or row["status"] not in ("queued", "running"):
            return
        try:
            report = self._build(row["board_id"], row["sprint_id"])
        except Exception as error:
            message = str(error)
            storage.finish_report_job(job_id, status="error", error=message)
            return
        storage.finish_report_job(job_id, status="done", report=report)

    def _build(self, board_id: int, sprint_id: int) -> dict:
        from scrum_agent.reports.metrics import build_report

        client = self.client()
        storage = self.storage()
        sprint = client.get_sprint(sprint_id)
        stored_config = storage.latest_board_config(board_id)
        if stored_config is not None:
            from scrum_agent.jira.models import BoardConfiguration

            config = BoardConfiguration.model_validate(stored_config["config"])
        else:
            # Cold start before the first collection run: one live read, then
            # the versioned stored configuration is authoritative.
            config = client.get_board_configuration(board_id)
        snapshot_rows = storage.live_snapshots()

        # Access revalidation: one scoped live search defines the authorized
        # set; stored issues absent from it are excluded everywhere.
        accessible = {issue.key for issue in client.iter_search_jql(f"sprint = {sprint_id}")}

        from scrum_agent.sync.freshness import freshness_report as report_freshness

        return build_report(
            snapshot_rows=snapshot_rows,
            accessible_keys=accessible,
            config=config,
            sprint=sprint,
            site=self._settings.jira_site,
            timezone=self._settings.report_timezone,
            freshness=report_freshness(storage, self._settings),
            cutoff_at=datetime.now(UTC),
        )


async def run_forever(jobs: ReportJobs, interval_seconds: float = 1.0) -> None:
    """The web process's local worker: poll for queued jobs in the background."""
    import asyncio

    while True:
        try:
            ran = await asyncio.to_thread(jobs.run_once)
        except Exception:  # never let a job error kill the worker
            ran = False
        await asyncio.sleep(interval_seconds if ran is None else (0.0 if ran else interval_seconds))
