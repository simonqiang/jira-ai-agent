"""CLI for the personal pilot: probe, sprints, search, serve and baseline.

Prints sanitized summaries only (no credentials, no assignee names). Exit codes:
0 success, 1 Jira/search error, 2 configuration or input error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from scrum_agent.config import Settings
from scrum_agent.jira.client import JiraClient
from scrum_agent.jira.errors import JiraApiError, JiraError
from scrum_agent.search.errors import AmbiguousSprintError, SearchError
from scrum_agent.search.filters import IssueFilters
from scrum_agent.search.service import SearchService


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scrum-agent", description="Scrum Master Jira assistant (personal pilot)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    subparsers.add_parser("probe", help="fetch the known issue and board metadata (default)")

    sprints_parser = subparsers.add_parser(
        "sprints", help="list the pilot board's sprints (Week 2)"
    )
    sprints_parser.add_argument(
        "--state",
        default="",
        help="comma-separated sprint states to include: future, active, closed "
        "(default: all states)",
    )

    search_parser = subparsers.add_parser(
        "search", help="typed Jira search over the pilot board (Week 2)"
    )
    search_parser.add_argument("--issue", help="look up a single issue key, e.g. PAY-1")
    search_parser.add_argument(
        "--sprint", help="sprint name or numeric ID; ambiguous names prompt a selection"
    )
    search_parser.add_argument("--status", action="append", help="status name (repeatable)")
    search_parser.add_argument(
        "--type", action="append", help="issue type name, e.g. Bug (repeatable)"
    )
    search_parser.add_argument(
        "--assignee",
        action="append",
        help="assignee display name, or 'Unassigned' (repeatable)",
    )
    search_parser.add_argument("--label", action="append", help="label (repeatable)")
    search_parser.add_argument("--unresolved", action="store_true", help="only unresolved issues")

    subparsers.add_parser("serve", help="run the local chat web UI on 127.0.0.1 (Week 3)")

    baseline_parser = subparsers.add_parser(
        "baseline",
        help="run the checked intents against the live model and record "
        "model calls/tokens as the Week 3 cost baseline",
    )
    baseline_parser.add_argument(
        "--out",
        default=None,
        help="output path for the JSON artifact (default: docs notes directory)",
    )

    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "probe"
    return args


def _probe(settings: Settings) -> int:
    now_utc = datetime.now(UTC)
    print(f"Jira connection probe - fetched {now_utc.isoformat(timespec='seconds')}")
    print(f"Report timezone: {settings.report_timezone}")

    with JiraClient(settings) as client:
        issue = client.get_issue(settings.known_issue_key)
        board = client.get_board(settings.jira_board_id)
        if board.type != "scrum":
            raise JiraApiError("The pilot requires a Scrum board; verify jira_board_id")
        configuration = client.get_board_configuration(settings.jira_board_id)

    if board.id != settings.jira_board_id or configuration.id != board.id:
        raise JiraApiError("Jira returned metadata for an unexpected board")
    if not configuration.filter_id or not configuration.done_status_ids:
        raise JiraApiError("Board configuration is missing its filter or mapped statuses")
    if configuration.estimation_type not in ("none", "issueCount", "field"):
        raise JiraApiError("Board estimation configuration is missing or unsupported")
    if configuration.estimation_type == "field" and not configuration.estimate_field_id:
        raise JiraApiError("Board estimation type is field but its field ID is missing")

    local = now_utc.astimezone(ZoneInfo(settings.report_timezone))
    print(f"Local time ({settings.report_timezone}): {local.isoformat(timespec='seconds')}")

    print(f"\nIssue {issue.key}: {issue.summary}")
    print(f"  link=https://{settings.jira_site}/browse/{issue.key}")
    print(f"  status={issue.status} type={issue.issue_type}", end="")
    if issue.updated:
        print(f" updated={issue.updated}", end="")
    print()

    print(f"\nBoard {board.id}: {board.name} ({board.type})")
    if board.project_name:
        print(f"  project={board.project_name}")
    print(f"  filter ID={configuration.filter_id or '(not supplied)'}")
    print(f"  estimation={configuration.estimation_type or '(not supplied)'}")
    if configuration.estimate_field_id:
        print(
            f"  estimate field={configuration.estimate_field_id} "
            f"({configuration.estimate_field_name or 'unnamed'})"
        )

    print("\nBoard columns -> status IDs (not names):")
    for column in configuration.columns:
        print(f"  {column.name}: {', '.join(column.statuses) or '(none)'}")
    print(f"Done status IDs: {', '.join(configuration.done_status_ids) or '(none)'}")
    print("Verify the board filter's project scope in Jira; its location is not its scope.")

    print("\nProbe OK.")
    return 0


def _sprints(settings: Settings, args: argparse.Namespace) -> int:
    states = tuple(state.strip() for state in args.state.split(",") if state.strip())
    with JiraClient(settings) as client:
        sprints = SearchService(client).list_sprints(states=states)

    fetched = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"Sprints on board {settings.jira_board_id} - fetched {fetched}")
    for sprint in sprints:
        print(f"  {sprint.id}: {sprint.name} [{sprint.state}]")
    print(f"\n{len(sprints)} sprint(s).")
    return 0


def _print_issue(settings: Settings, issue) -> None:
    print(f"\n{issue.key}: {issue.summary}")
    print(f"  link=https://{settings.jira_site}/browse/{issue.key}")
    print(f"  status={issue.status} type={issue.issue_type}", end="")
    if issue.updated:
        print(f" updated={issue.updated}", end="")
    print()


def _search(settings: Settings, args: argparse.Namespace) -> int:
    if args.issue and (
        args.sprint or args.status or args.type or args.assignee or args.label or args.unresolved
    ):
        print("Use either --issue or search filters, not both.", file=sys.stderr)
        return 2

    with JiraClient(settings) as client:
        service = SearchService(client)

        if args.issue:
            _print_issue(settings, service.get_issue(args.issue))
            print("\n1 issue.")
            return 0

        if not (
            args.sprint
            or args.status
            or args.type
            or args.assignee
            or args.label
            or args.unresolved
        ):
            print(
                "Specify --issue or at least one filter: --sprint, --status, --type, "
                "--assignee, --label, --unresolved.",
                file=sys.stderr,
            )
            return 2

        sprint = service.resolve_sprint(args.sprint) if args.sprint else None
        filters = IssueFilters(
            statuses=args.status or (),
            issue_types=args.type or (),
            assignees=args.assignee or (),
            labels=args.label or (),
            unresolved_only=args.unresolved,
            sprint_id=sprint.id if sprint is not None else None,
        )
        result = service.search_issues(filters)

    print(f"Search - fetched {result.fetched_at.isoformat(timespec='seconds')}")
    if result.sprint is not None:
        print(f"Sprint: {result.sprint.id} {result.sprint.name} [{result.sprint.state}]")
    print(f"JQL: {result.jql}")
    for issue in result.issues:
        _print_issue(settings, issue)
    if result.is_empty:
        print("\nNo issues match the query (checked: 0 issues).")
    else:
        print(f"\n{result.result_count} issue(s).")
    return 0


def _serve(settings: Settings) -> int:
    """Run the Week 3 chat UI; loopback-only by validated settings."""
    import uvicorn

    from scrum_agent.agent.chat import ChatService
    from scrum_agent.web import create_app

    try:
        chat = ChatService(settings)
    except ValueError as error:
        print(f"Configuration error - {error}", file=sys.stderr)
        return 2
    print(
        f"Serving the chat UI on http://{settings.web_host}:{settings.web_port} "
        "(loopback only; Ctrl-C to stop)"
    )
    uvicorn.run(
        create_app(settings, chat),
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
    )
    return 0


def _baseline(settings: Settings, args: argparse.Namespace) -> int:
    """Run the checked intents against the live model and record the cost baseline."""
    import asyncio

    from scrum_agent.agent.baseline import run_baseline

    try:
        report = asyncio.run(run_baseline(settings, out_path=args.out))
    except ValueError as error:
        print(f"Configuration error - {error}", file=sys.stderr)
        return 2
    except JiraError as error:
        print(f"Jira error: {error}", file=sys.stderr)
        return 1
    tasks = report["tasks"]
    ok = sum(1 for task in tasks if task["answer_ok"])
    print(f"Baseline: {ok}/{len(tasks)} intents answered correctly.")
    print(f"Totals: {report['totals']}")
    print(f"Artifact: {report['artifact_path']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        print("Configuration error - check your environment or .env file:", file=sys.stderr)
        for problem in error.errors():
            location = ".".join(str(part) for part in problem["loc"])
            print(f"  {location or '(root)'}: {problem['msg']}", file=sys.stderr)
        print("See .env.example for the expected variables.", file=sys.stderr)
        return 2
    try:
        if args.command == "sprints":
            return _sprints(settings, args)
        if args.command == "search":
            return _search(settings, args)
        if args.command == "serve":
            return _serve(settings)
        if args.command == "baseline":
            return _baseline(settings, args)
        return _probe(settings)
    except AmbiguousSprintError as error:
        print(f"Jira error: {error}", file=sys.stderr)
        print("Choose one of:", file=sys.stderr)
        for candidate in error.candidates:
            print(f"  {candidate.id}: {candidate.name} [{candidate.state}]", file=sys.stderr)
        return 1
    except SearchError as error:
        print(f"Search error: {error}", file=sys.stderr)
        return 1
    except ValidationError as error:
        print("Invalid search input:", file=sys.stderr)
        for problem in error.errors():
            location = ".".join(str(part) for part in problem["loc"])
            print(f"  {location or '(root)'}: {problem['msg']}", file=sys.stderr)
        return 2
    except JiraError as error:
        print(f"Jira error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
