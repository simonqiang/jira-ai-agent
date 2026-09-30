"""Week 5 report tests: checked metrics, exports, durable jobs, revalidation.

The checked dataset is the Week 2 fixture Jira collected into in-memory
storage, so report numbers verify against known truth: sprint 78 holds
PAY-1 (In Progress, 3 points, blocked) / PAY-2 (Done, 2 points) / PAY-3
(To Do, no estimate, unassigned).
"""

from __future__ import annotations

import csv

import httpx

from scrum_agent.agent.chat import ChatService
from scrum_agent.jira.client import JiraClient
from scrum_agent.jira.models import BoardColumn, BoardConfiguration, Sprint
from scrum_agent.reports.exports import sanitize_cell, to_csv, to_markdown
from scrum_agent.reports.jobs import ReportJobs
from scrum_agent.reports.metrics import (
    build_narrative,
    build_report,
    is_blocked,
    sprint_ids_in_fields,
)
from scrum_agent.sync.collector import CollectorService
from scrum_agent.sync.freshness import FreshnessReport
from tests.agent_fakes import FakeLlm, ScriptedStep, tool_call
from tests.checked_queries import DEFAULT_SPRINTS, FakeJira
from tests.conftest import make_settings
from tests.storage_fakes import InMemoryStorage

_DB_SETTINGS = dict(database_url="postgresql://localhost/scrum_agent")


def collect_into_storage(jira: FakeJira | None = None) -> InMemoryStorage:
    """Run the real collector over the fixture Jira into in-memory storage."""
    jira = jira if jira is not None else FakeJira()
    client = JiraClient(make_settings(), transport=httpx.MockTransport(jira.handler))
    storage = InMemoryStorage()
    CollectorService(client, storage).run(full=True)
    client.close()
    return storage


def make_jobs(
    jira: FakeJira | None = None, storage: InMemoryStorage | None = None
) -> tuple[ReportJobs, FakeJira, InMemoryStorage]:
    jira = jira if jira is not None else FakeJira()
    storage = storage if storage is not None else collect_into_storage(jira)
    client = JiraClient(make_settings(**_DB_SETTINGS), transport=httpx.MockTransport(jira.handler))
    jobs = ReportJobs(make_settings(**_DB_SETTINGS), client=client, storage=storage)
    return jobs, jira, storage


def generate(jobs: ReportJobs, sprint_reference: str | int = 78) -> dict:
    handle = jobs.submit(sprint_reference)
    jobs.run_once()
    return jobs.storage().get_report_job(handle["job_id"])["report"]


# -- checked metrics --------------------------------------------------------------


def test_checked_dataset_metrics_match_hand_computed_truth() -> None:
    jobs, _, _ = make_jobs()
    report = generate(jobs)

    assert report["scope"]["total"] == 3
    assert report["scope"]["by_type"] == {"Bug": 2, "Story": 1}
    assert report["status_counts"] == {"Done": 1, "In Progress": 1, "To Do": 1}
    assert report["done"]["count"] == 1
    assert report["done"]["not_done_count"] == 2
    assert report["estimates"]["total"] == 5.0
    assert report["estimates"]["done_total"] == 2.0
    assert report["estimates"]["not_done_total"] == 3.0
    assert report["estimates"]["with_estimate"] == 2
    assert report["estimates"]["missing_estimate"] == 1
    assert report["estimates"]["missing_estimate_keys"] == ["PAY-3"]
    assert report["blockers"] == [{"key": "PAY-1", "evidence": "label 'blocked'"}]
    assert report["attention"]["unassigned_not_done"] == ["PAY-3"]
    assert report["excluded"] == {"count": 0, "keys": []}
    assert report["completeness"] == "complete"
    assert [issue["key"] for issue in report["issues"]] == ["PAY-1", "PAY-2", "PAY-3"]
    assert report["metric_policy_version"] == "adr-0002/current-state"


def test_narrative_stays_evidence_backed_and_human_confirmed() -> None:
    jobs, _, _ = make_jobs()
    report = generate(jobs)
    narrative = report["narrative"]

    assert "achievement: unknown (human-confirmed" in narrative
    assert "not set in Jira" in narrative  # fixture sprint has no goal
    assert "PAY-1 (label 'blocked')" in narrative
    assert "estimate PAY-3" in narrative
    assert "unavailable" in narrative  # commitment history stays Week 6
    assert "complete" in report["completeness"]


def test_sprint_goal_appears_when_jira_has_one() -> None:
    jobs, _, _ = make_jobs()
    report = generate(jobs)
    report["sprint"]["goal"] = "Payments R2 live"
    assert "Payments R2 live" in build_narrative(report)


def test_blocker_evidence_accepts_blocks_link_and_rejects_other_links() -> None:
    blocks = {"type": {"name": "Blocks"}, "outwardIssue": {"key": "PAY-9"}}
    relates = {"type": {"name": "Relates"}, "outwardIssue": {"key": "PAY-9"}}
    assert is_blocked({"issuelinks": [blocks]}) == "blocks-issue link"
    assert is_blocked({"issuelinks": [relates]}) is None
    assert is_blocked({"labels": ["BLOCKED"]}) == "label 'blocked'"
    assert is_blocked({}) is None


# -- access revalidation ----------------------------------------------------------


def test_revoked_issue_is_excluded_from_details_and_totals() -> None:
    storage = collect_into_storage()  # collected with PAY-1 visible
    revoked = FakeJira(issues=tuple(issue for issue in FakeJira().issues if issue.key != "PAY-1"))
    jobs, _, _ = make_jobs(jira=revoked, storage=storage)
    report = generate(jobs)

    assert report["excluded"] == {"count": 1, "keys": ["PAY-1"]}
    assert [issue["key"] for issue in report["issues"]] == ["PAY-2", "PAY-3"]
    assert report["scope"]["total"] == 2
    assert report["estimates"]["total"] == 2.0  # PAY-1's 3 points are gone too
    assert report["blockers"] == []
    assert report["completeness"] == "partial"
    assert any("PAY-1" in note for note in report["completeness_notes"])


# -- exports ----------------------------------------------------------------------


def test_export_totals_match_the_report_on_both_formats() -> None:
    jobs, _, _ = make_jobs()
    report = generate(jobs)

    markdown = to_markdown(report)
    assert "| Scope (current) | 3 |" in markdown
    assert "| Done (in done column) | 1 |" in markdown
    assert "| Estimates total (Story Points) | 5 |" in markdown
    assert "| Status: In Progress | 1 |" in markdown
    assert report["narrative"] in markdown

    rows = list(csv.reader(to_csv(report).splitlines()))
    totals = {row[0]: row[1] for row in rows if len(row) == 2 and row[0] != "metric"}
    assert totals["Scope (current)"] == "3"
    assert totals["Done (in done column)"] == "1"
    assert totals["Not done"] == "2"
    assert totals["Estimates total (Story Points)"] == "5"
    assert totals["Status: To Do"] == "1"
    issue_rows = [row for row in rows if row and row[0].startswith("PAY-")]
    assert len(issue_rows) == 3


def test_exports_resist_formula_and_markup_injection() -> None:
    evil_summary = "=1+1 | @SUM(A1)\nrow2"
    row = {
        "issue_id": "1",
        "issue_key": "PAY-9",
        "summary": evil_summary,
        "status": "To Do",
        "issue_type": "Bug",
        "assignee": None,
        "updated": None,
        "fields": {"sprint": ["x[id=78,y]"], "status": {"id": "1"}, "labels": ["=cmd"]},
    }
    config = BoardConfiguration(
        id=42,
        name="Payments Scrum Board",
        columns=(
            BoardColumn(name="To Do", statuses=("1",)),
            BoardColumn(name="Done", statuses=("5",)),
        ),
        estimation_type="field",
        estimate_field_id="customfield_10002",
        estimate_field_name="Story Points",
    )
    report = build_report(
        snapshot_rows=[row],
        accessible_keys={"PAY-9"},
        config=config,
        sprint=Sprint(id=78, name="Payments R2", state="active", origin_board_id=42),
        site="test.atlassian.net",
        timezone="Asia/Hong_Kong",
        freshness=FreshnessReport(
            last_success_at=None,
            success_age=None,
            credential_status="unknown",
            days_until_expiry=None,
            live_issues=1,
            tombstoned_issues=0,
            events_total=0,
            failures_since_success=0,
        ),
    )

    assert sanitize_cell("=cmd") == "'=cmd"
    markdown = to_markdown(report)
    assert "'=1+1 \\| @SUM(A1) row2" in markdown  # guarded, pipes escaped, one line
    csv_text = to_csv(report)
    assert "\"'=1+1" in csv_text  # formula guard present inside the quoted cell
    assert csv_text.count("\r\n") > 0  # valid line termination


# -- durable job engine -----------------------------------------------------------


def test_duplicate_submission_reuses_one_job_per_cutoff_hour() -> None:
    jobs, _, _ = make_jobs()
    first = jobs.submit("Payments R2")
    second = jobs.submit("R2")
    other = jobs.submit(79)

    assert second["job_id"] == first["job_id"]
    assert second["reused"] is True
    assert first["reused"] is False
    assert other["job_id"] != first["job_id"]
    assert other["sprint_id"] == 79


def test_worker_executes_job_and_report_is_stored() -> None:
    jobs, _, _ = make_jobs()
    handle = jobs.submit(78)

    assert handle["status"] == "queued"
    ran = jobs.run_once()
    assert ran == handle["job_id"]
    row = jobs.storage().get_report_job(handle["job_id"])
    assert row["status"] == "done"
    assert row["report"]["kind"] == "sprint_report"
    assert row["report"]["scope"]["total"] == 3
    assert jobs.run_once() is None  # nothing queued


def test_restarted_worker_requeues_orphan_and_completes() -> None:
    jobs, _, storage = make_jobs()
    handle = jobs.submit(78)
    crashed = storage.report_jobs[handle["job_id"]]
    crashed["status"] = "running"  # crash after the first claim...
    crashed["attempts"] = 1

    assert jobs.run_once() == handle["job_id"]
    row = storage.get_report_job(handle["job_id"])
    assert row["status"] == "done"
    assert row["attempts"] == 2
    assert row["report"]["scope"]["total"] == 3


def test_execution_failure_is_recorded_not_raised() -> None:
    jobs, _, storage = make_jobs()
    handle = jobs.submit(78)
    # The sprint disappears from Jira before the worker executes the job.
    dead = FakeJira(sprints=tuple(s for s in DEFAULT_SPRINTS if s.id != 78))
    jobs._client = JiraClient(
        make_settings(**_DB_SETTINGS), transport=httpx.MockTransport(dead.handler)
    )

    jobs.run_once()
    row = storage.get_report_job(handle["job_id"])
    assert row["status"] == "error"
    assert "not listed" in row["error"]  # fail-closed sprint scope check


def test_execute_ignores_finished_jobs() -> None:
    jobs, _, storage = make_jobs()
    handle = jobs.submit(78)
    jobs.run_once()
    before = storage.get_report_job(handle["job_id"])
    jobs.execute(handle["job_id"])
    after = storage.get_report_job(handle["job_id"])
    assert before == after


# -- agent tools and chat flow ------------------------------------------------------


def tools_with_jobs():
    from scrum_agent.agent.tools import make_tools
    from scrum_agent.search.service import SearchService

    jobs, jira, storage = make_jobs()
    client = jobs.client()
    tools = {tool.name: tool.func for tool in make_tools(SearchService(client), jobs)}
    return tools, jobs, storage


def test_report_tools_roundtrip_through_job_engine() -> None:
    tools, jobs, _ = tools_with_jobs()
    started = tools["build_sprint_report"](sprint_reference="Payments R2")
    assert started["ok"] is True
    assert started["status"] == "queued"
    assert started["estimate_seconds"] >= 2

    running = tools["get_report"](job_id=started["job_id"])
    assert running["report"] is None  # not done yet

    jobs.run_once()
    done = tools["get_report"](job_id=started["job_id"])
    assert done["job"]["status"] == "done"
    assert done["report"]["scope"]["total"] == 3
    assert done["sources"] == [{"sprint_id": 78}]


def test_report_tools_fail_soft_without_jobs() -> None:
    from scrum_agent.agent.tools import make_tools
    from tests.agent_fakes import service_over

    with service_over(FakeJira()) as probe:
        tools = {tool.name: tool.func for tool in make_tools(probe.service, None)}
        for name, kwargs in (
            ("build_sprint_report", {"sprint_reference": "R2"}),
            ("get_report", {"job_id": 1}),
        ):
            payload = tools[name](**kwargs)
            assert payload["ok"] is False
            assert payload["error"]["kind"] == "invalid_input"
            assert "database" in payload["error"]["message"]


async def test_chat_can_build_and_fetch_a_report() -> None:
    jobs, jira, _ = make_jobs()
    fake = FakeLlm(
        steps=[
            ScriptedStep(tool_calls=(tool_call("build_sprint_report", sprint_reference="R2"),)),
            ScriptedStep(tool_calls=(tool_call("get_report", job_id=1),)),
            ScriptedStep(text="The report for Payments R2 is ready."),
        ]
    )
    chat = ChatService(
        make_settings(),  # no database_url: ADK sessions stay in memory
        llm=fake,
        transport=httpx.MockTransport(jira.handler),
        jobs=jobs,
    )
    result = await chat.run_turn("sess", "report for R2")
    assert result.answer == "The report for Payments R2 is ready."
    assert {"sprint_id": 78} in [dict(source) for source in result.sources]
    chat._client.close()


def test_membership_parses_both_jira_sprint_field_shapes() -> None:
    string_shape = {"sprint": ["com.atlassian.greenhopper...[id=78,rapidViewId=42]"]}
    object_shape = {"customfield_10020": [{"id": 75203, "name": "S", "boardId": 6603}]}
    assert sprint_ids_in_fields(string_shape) == {78}
    assert sprint_ids_in_fields(object_shape) == {75203}
    assert sprint_ids_in_fields({"customfield_10020": None}) == set()
