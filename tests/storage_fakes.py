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
        self.chunks: dict[tuple[str, str, int], dict] = {}  # (issue_id, kind, index)

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

    # -- retrieval chunks (Week 10; mirrors the PgStorage SQL semantics) ------

    def chunk_signatures(self) -> dict[str, tuple[datetime | None, str]]:
        return {
            chunk["issue_id"]: (chunk["source_updated"], chunk["embedding_model"])
            for chunk in self.chunks.values()
        }

    def replace_chunks(
        self,
        *,
        issue_id: str,
        issue_key: str,
        site: str,
        project_key: str,
        source_updated: datetime | None,
        rows: list[tuple],
        embedding_model: str,
    ) -> None:
        self.chunks = {
            key: chunk for key, chunk in self.chunks.items() if chunk["issue_id"] != issue_id
        }
        for kind, index, heading, content, content_hash, embedding in rows:
            self.chunks[(issue_id, kind, index)] = {
                "issue_id": issue_id,
                "issue_key": issue_key,
                "site": site,
                "project_key": project_key,
                "chunk_kind": kind,
                "chunk_index": index,
                "heading": heading,
                "content": content,
                "content_hash": content_hash,
                "source_updated": source_updated,
                "source_url": f"https://{site}/browse/{issue_key}",
                "embedding": embedding,
                "embedding_model": embedding_model,
                "indexed_at": self._now(),
            }

    def prune_orphan_chunks(self) -> None:
        tombstoned = {s["issue_id"] for s in self.snapshots.values() if s["deleted_at"]}
        self.chunks = {
            key: chunk for key, chunk in self.chunks.items() if chunk["issue_id"] not in tombstoned
        }

    def delete_chunks(self, *, issue_key: str) -> None:
        self.chunks = {key: c for key, c in self.chunks.items() if c["issue_key"] != issue_key}

    def hybrid_search(
        self,
        *,
        query: str,
        query_embedding: list[float],
        embedding_model: str,
        limit: int,
    ) -> list[dict]:
        live_keys = {
            s["issue_id"]: s["summary"] for s in self.snapshots.values() if not s["deleted_at"]
        }
        candidates = [c for c in self.chunks.values() if c["embedding_model"] == embedding_model]
        terms = [t.casefold() for t in query.split()]
        by_rank: dict[int, list[float]] = {}

        def fuse(ranking: list[int]) -> None:
            for position, chunk_id in enumerate(ranking):
                by_rank.setdefault(chunk_id, []).append(1.0 / (60 + position + 1))

        vector_hits = sorted(
            (c for c in candidates if c["issue_id"] in live_keys),
            key=lambda c: _cosine(query_embedding, c["embedding"]),
            reverse=True,
        )
        fuse([id(c) for c in vector_hits])
        text_hits = [
            c
            for c in candidates
            if all(term in f"{c['heading'] or ''} {c['content']}".casefold() for term in terms)
        ]
        fuse([id(c) for c in text_hits])
        best: dict[int, dict] = {}
        for chunk_id, ranks in by_rank.items():
            chunk = next(c for c in candidates if id(c) == chunk_id)
            score = sum(ranks)
            similarity = _cosine(query_embedding, chunk["embedding"])
            if chunk["issue_id"] not in live_keys:
                continue
            if chunk["issue_id"] not in best or score > best[chunk["issue_id"]]["score"]:
                best[chunk["issue_id"]] = {
                    "issue_key": chunk["issue_key"],
                    "chunk_kind": chunk["chunk_kind"],
                    "heading": chunk["heading"],
                    "content": chunk["content"],
                    "source_updated": chunk["source_updated"],
                    "source_url": chunk["source_url"],
                    "title": live_keys[chunk["issue_id"]],
                    "similarity": similarity,
                    "score": score,
                }
        return sorted(best.values(), key=lambda row: row["score"], reverse=True)[:limit]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = (sum(x * x for x in a) * sum(y * y for y in b)) ** 0.5
    return dot / norm if norm else 0.0
