"""Jinja filters: safe issue-key linking and local-time rendering.

``linkify_issue_keys`` escapes text first and only then wraps issue-key
matches in anchor tags, so markup injected through issue summaries or model
output can never reach the DOM. ``localtime`` renders UTC timestamps in the
configured report timezone (spec guardrail: store UTC, render local).
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from markupsafe import Markup


def linkify_issue_keys(text: str, pattern: re.Pattern[str], jira_site: str) -> Markup:
    """Escape ``text``, render Markdown tables, and link Jira issue keys."""

    lines = text.splitlines()
    rendered: list[Markup] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            headers = _table_cells(lines[index])
            separators = _table_cells(lines[index + 1])
            if headers and _is_table_separator(separators, len(headers)):
                rows: list[list[str]] = []
                cursor = index + 2
                while cursor < len(lines):
                    cells = _table_cells(lines[cursor])
                    if cells is None:
                        break
                    rows.append((cells + [""] * len(headers))[: len(headers)])
                    cursor += 1
                rendered.append(_render_table(headers, rows, pattern, jira_site))
                index = cursor
                continue
        rendered.append(_render_text(lines[index], pattern, jira_site))
        index += 1
    return Markup("\n").join(rendered)


def _table_cells(line: str) -> list[str] | None:
    """Split a pipe table row, respecting escaped literal pipes."""
    if "|" not in line:
        return None
    content = line.strip()
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|") and not content.endswith("\\|"):
        content = content[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in content:
        if char == "|" and not escaped:
            cells.append("".join(current).strip().replace("\\|", "|"))
            current = []
        else:
            current.append(char)
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    cells.append("".join(current).strip().replace("\\|", "|"))
    return cells


def _is_table_separator(cells: list[str] | None, count: int) -> bool:
    return bool(
        cells
        and len(cells) == count
        and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)
    )


def _render_table(
    headers: list[str],
    rows: list[list[str]],
    pattern: re.Pattern[str],
    jira_site: str,
) -> Markup:
    columns = "".join(f'<col class="col-{_column_kind(value)}">' for value in headers)
    head = "".join(
        f'<th class="cell-{_column_kind(value)}" scope="col">'
        f"{_linked_cell(value, pattern, jira_site)}</th>"
        for value in headers
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="cell-{_column_kind(headers[index])}">'
            f"{_linked_cell(value, pattern, jira_site)}</td>"
            for index, value in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return Markup(
        '<div class="answer-table-wrap"><table class="answer-table"><colgroup>'
        f"{columns}</colgroup><thead><tr>"
        f"{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _column_kind(label: str) -> str:
    normalized = re.sub(r"[^a-z]+", "-", label.casefold()).strip("-")
    if normalized in {"key", "issue", "issue-key"}:
        return "key"
    if normalized in {"type", "issue-type"}:
        return "type"
    if normalized == "status":
        return "status"
    if normalized in {"assignee", "owner"}:
        return "assignee"
    return "summary" if normalized in {"summary", "title"} else "other"


def _linked_cell(value: str, pattern: re.Pattern[str], jira_site: str) -> Markup:
    escaped = html.escape(value, quote=True)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return Markup(pattern.sub(lambda match: _issue_link(match, jira_site), escaped))


def _render_text(value: str, pattern: re.Pattern[str], jira_site: str) -> Markup:
    escaped = html.escape(value, quote=True)
    emphasized = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return Markup(pattern.sub(lambda match: _issue_link(match, jira_site), emphasized))


def _issue_link(match: re.Match[str], jira_site: str) -> str:
    key = match.group(0)
    return f'<a href="https://{jira_site}/browse/{key}" target="_blank" rel="noopener">{key}</a>'


def localtime(timestamp: str, timezone_name: str) -> str:
    """Render an ISO-8601 UTC timestamp in the configured timezone."""
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if moment.tzinfo is None:
        return timestamp
    return moment.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M %Z")
