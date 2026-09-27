"""Week 1 CLI: probe the configured Jira connection.

Fetches the configured known issue, board and board configuration, and prints a
sanitized summary (no credentials). Exit codes: 0 success, 1 Jira/API error,
2 configuration error.
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
from scrum_agent.jira.errors import JiraError


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scrum-agent", description="Scrum Master Jira assistant (personal pilot)"
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="probe",
        choices=["probe"],
        help="probe: fetch the known issue and board metadata (default)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def _probe(settings: Settings) -> int:
    now_utc = datetime.now(UTC)
    print(f"Jira connection probe - fetched {now_utc.isoformat(timespec='seconds')}")
    print(f"Report timezone: {settings.report_timezone}")

    with JiraClient(settings) as client:
        issue = client.get_issue(settings.known_issue_key)
        board = client.get_board(settings.jira_board_id)
        configuration = client.get_board_configuration(settings.jira_board_id)

    local = now_utc.astimezone(ZoneInfo(settings.report_timezone))
    print(f"Local time ({settings.report_timezone}): {local.isoformat(timespec='seconds')}")

    print(f"\nIssue {issue.key}: {issue.summary}")
    print(f"  status={issue.status} type={issue.issue_type}", end="")
    if issue.assignee:
        print(f" assignee={issue.assignee}", end="")
    if issue.updated:
        print(f" updated={issue.updated}", end="")
    print()

    print(f"\nBoard {board.id}: {board.name} ({board.type})")
    if board.project_name:
        print(f"  project={board.project_name}")

    print("\nBoard columns (done rule resolves from this mapping, not a status name):")
    for column_name, statuses in configuration.statuses_by_column().items():
        print(f"  {column_name}: {', '.join(statuses) or '(none)'}")

    print("\nProbe OK.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
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
        return _probe(settings)
    except JiraError as error:
        print(f"Jira error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
