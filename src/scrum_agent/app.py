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
from scrum_agent.jira.errors import JiraApiError, JiraError


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
        return _probe(settings)
    except JiraError as error:
        print(f"Jira error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
