"""In-memory storage fake mirroring PgStorage's method semantics (Week 4).

Same interface as ``scrum_agent.storage.repository.PgStorage`` so collector
tests run without Postgres; the gated real-database tests in
``tests/test_storage.py`` cover the SQL itself.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

_RUN_STATUSES = ("running", "success", "error", "interrupted")


class InMemoryStorage:
    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now if now is not None else (lambda: datetime.now(UTC))
        self.runs: list[dict] = []
        self.snapshots: dict[str, dict] = {}  # issue_id -> snapshot
        self.events: dict[tuple[str, str, int], dict] = {}
        self.board_configs: list[dict] = []
        self.checkpoints: dict[str, dict] = {}
        self.report_jobs: dict[int, dict] = {}

    # -- collection runs ---------------------------------------------------

    def start_run(self, trigger: str = "manual") -> int:
        for run in self.runs:
            if run["status"] == "running":
                run["status"] = "interrupted"
                run["finished_at"] = self._now()
                run["error"] = "superseded by a newer run"
        run_id = len(self.runs) + 1
        self.runs.append(
            {
                "id": run_id,
                "started_at": self._now(),
                "finished_at": None,
                "status": "running",
                "trigger": trigger,
                "issues_seen": 0,
                "events_seen": 0,
                "pages_fetched": 0,
                "error": None,
            }
        )
        return run_id

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        issues_seen: int = 0,
        events_seen: int = 0,
        pages_fetched: int = 0,
        error: str | None = None,
    ) -> None:
        assert status in _RUN_STATUSES and status != "running"
        run = self.runs[run_id - 1]
        run.update(
            finished_at=self._now(),
            status=status,
            issues_seen=issues_seen,
            events_seen=events_seen,
            pages_fetched=pages_fetched,
            error=error,
        )

    def last_success(self) -> datetime | None:
        stamps = [run["finished_at"] for run in self.runs if run["status"] == "success"]
        return max(stamps) if stamps else None

    # -- issue snapshots ---------------------------------------------------

    def upsert_snapshot(
        self,
        *,
        issue_id: str,
        issue_key: str,
        summary: str,
        status: str,
        issue_type: str,
        assignee: str | None,
        updated: datetime | None,
        fields: dict,
        run_id: int,
    ) -> None:
        existing = self.snapshots.get(issue_id)
        self.snapshots[issue_id] = {
            "issue_id": issue_id,
            "issue_key": issue_key,
            "summary": summary,
            "status": status,
            "issue_type": issue_type,
            "assignee": assignee,
            "updated": updated,
            "fields": fields,
            "run_id": run_id,
            "first_seen_at": existing["first_seen_at"] if existing else self._now(),
            "deleted_at": None,
        }

    def tombstone_issue(self, issue_key: str) -> bool:
        changed = False
        for snapshot in self.snapshots.values():
            if snapshot["issue_key"] == issue_key and snapshot["deleted_at"] is None:
                snapshot["deleted_at"] = self._now()
                changed = True
        return changed

    # -- changelog events --------------------------------------------------

    def insert_events(self, issue_id: str, rows: list[tuple], run_id: int) -> int:
        inserted = 0
        for row in rows:
            key = (issue_id, row[0], row[1])  # (issue_id, changelog_id, item_index)
            if key in self.events:
                continue
            self.events[key] = {
                "issue_id": issue_id,
                "changelog_id": row[0],
                "item_index": row[1],
                "field": row[2],
                "field_id": row[3],
                "from_id": row[4],
                "from_value": row[5],
                "to_id": row[6],
                "to_value": row[7],
                "author": row[8],
                "occurred_at": row[9],
                "run_id": run_id,
            }
            inserted += 1
        return inserted

    # -- board configuration versions ---------------------------------------

    def latest_board_config(self, board_id: int) -> dict | None:
        versions = [c for c in self.board_configs if c["board_id"] == board_id]
        return versions[-1] if versions else None

    def insert_board_config(
        self, *, board_id: int, config_hash: str, config: dict, run_id: int
    ) -> None:
        self.board_configs.append(
            {
                "board_id": board_id,
                "config_hash": config_hash,
                "config": config,
                "run_id": run_id,
                "captured_at": self._now(),
            }
        )

    # -- checkpoints and freshness ------------------------------------------

    def set_checkpoint(self, target: str, at: datetime, run_id: int) -> None:
        self.checkpoints[target] = {"last_success_at": at, "run_id": run_id}

    def freshness(self) -> dict:
        successes = [run["finished_at"] for run in self.runs if run["status"] == "success"]
        last = max(successes) if successes else None
        return {
            "last_success_at": last,
            "success_age": (self._now() - last) if last else None,
            "failures_since_success": sum(
                1
                for run in self.runs
                if run["status"] in ("error", "interrupted")
                and last is not None
                and run["started_at"] > last
            ),
            "runs_in_flight": sum(1 for run in self.runs if run["status"] == "running"),
            "live_issues": sum(1 for s in self.snapshots.values() if s["deleted_at"] is None),
            "tombstoned_issues": sum(
                1 for s in self.snapshots.values() if s["deleted_at"] is not None
            ),
            "events_total": len(self.events),
        }

    # -- report inputs (Week 5) ----------------------------------------------

    def live_snapshots(self) -> list[dict]:
        return [
            {
                key: s[key]
                for key in (
                    "issue_id",
                    "issue_key",
                    "summary",
                    "status",
                    "issue_type",
                    "assignee",
                    "updated",
                    "fields",
                )
            }
            for s in self.snapshots.values()
            if s["deleted_at"] is None
        ]

    def events_for_issue_ids(self, issue_ids: list[str]) -> list[dict]:
        return sorted(
            (event for event in self.events.values() if event["issue_id"] in set(issue_ids)),
            key=lambda event: (
                event["issue_id"],
                event["occurred_at"],
                event["changelog_id"],
                event["item_index"],
            ),
        )

    # -- report jobs (Week 5) --------------------------------------------------

    def submit_report_job(
        self, *, request_key: str, board_id: int, sprint_id: int, estimate_seconds: int
    ) -> tuple[int, bool]:
        for job in self.report_jobs.values():
            if job["request_key"] == request_key:
                return job["id"], False
        job_id = max(self.report_jobs, default=0) + 1
        self.report_jobs[job_id] = {
            "id": job_id,
            "request_key": request_key,
            "kind": "sprint_report",
            "board_id": board_id,
            "sprint_id": sprint_id,
            "status": "queued",
            "attempts": 0,
            "estimate_seconds": estimate_seconds,
            "requested_at": self._now(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "report": None,
        }
        return job_id, True

    def claim_next_report_job(self) -> dict | None:
        for job in self.report_jobs.values():  # restart recovery, like the SQL
            if job["status"] == "running":
                job["status"] = "queued"
                job["error"] = "requeued after restart"
        for job in sorted(self.report_jobs.values(), key=lambda j: j["requested_at"]):
            if job["status"] == "queued":
                job["status"] = "running"
                job["started_at"] = self._now()
                job["attempts"] += 1
                return {
                    "id": job["id"],
                    "board_id": job["board_id"],
                    "sprint_id": job["sprint_id"],
                    "attempts": job["attempts"],
                }
        return None

    def finish_report_job(
        self, job_id: int, *, status: str, error: str | None = None, report: dict | None = None
    ) -> None:
        job = self.report_jobs[job_id]
        job.update(status=status, finished_at=self._now(), error=error, report=report)

    def get_report_job(self, job_id: int) -> dict | None:
        return self.report_jobs.get(job_id)
