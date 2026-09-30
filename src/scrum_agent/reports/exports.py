"""Markdown and CSV exports of the computed report (Week 5).

Issue text is untrusted: CSV cells beginning with ``=``, ``+``, ``-`` or ``@``
are formula-injection vectors and get a ``'`` guard prefix; Markdown table
cells escape pipes and newlines. Exports carry the same authorized totals as
the on-screen report — they are renderings of one report object, never a
recomputation.
"""

from __future__ import annotations

import csv
import io

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value: object) -> str:
    """Stringify a cell and neutralize spreadsheet formula prefixes."""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def _md_cell(value: object) -> str:
    """Sanitized, pipe-safe single-line Markdown table cell."""
    flat = sanitize_cell(value).replace("\\", "\\\\").replace("|", "\\|")
    return flat.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _fmt_estimate(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def to_markdown(report: dict) -> str:
    sprint = report["sprint"]
    lines: list[str] = [
        f"# Sprint report: {sprint['name']} (sprint {sprint['id']}, {sprint['state']})",
        "",
        f"- Site: {report['site']} | Board: {report['board_id']} | Timezone: {report['timezone']}",
        f"- Cutoff: {report['cutoff_at']} | Generated: {report['generated_at']}",
        f"- Metric policy: {report['metric_policy_version']}",
        f"- Completeness: {report['completeness']}",
        f"- Freshness: last success {report['freshness']['last_success_at'] or 'never'} "
        f"({report['freshness']['success_age'] or 'n/a'} ago)",
    ]
    for note in report["completeness_notes"]:
        lines.append(f"- Note: {note}")
    lines += ["", "## Totals", "", "| Metric | Value |", "|---|---|"]
    for metric, value in _totals(report):
        lines.append(f"| {_md_cell(metric)} | {_md_cell(value)} |")

    lines += [
        "",
        "## Issues",
        "",
        "| Key | Summary | Type | Status | Done | Assignee | Estimate | Blocked |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for issue in report["issues"]:
        lines.append(
            "| "
            + " | ".join(
                _md_cell(cell)
                for cell in (
                    issue["key"],
                    issue["summary"],
                    issue["issue_type"],
                    issue["status"],
                    "yes" if issue["done"] else "no",
                    issue["assignee"] or "Unassigned",
                    _fmt_estimate(issue["estimate"]),
                    issue["blocked_evidence"] or "",
                )
            )
            + " |"
        )
    lines += ["", "## Narrative", "", report["narrative"], ""]
    return "\n".join(lines)


_CSV_ISSUE_COLUMNS = (
    "key",
    "summary",
    "type",
    "status",
    "done",
    "assignee",
    "estimate",
    "blocked_evidence",
)


def _totals(report: dict) -> list[tuple[str, object]]:
    scope, done, estimates = report["scope"], report["done"], report["estimates"]
    rows: list[tuple[str, object]] = [
        ("Scope (current)", scope["total"]),
        ("Done (in done column)", done["count"]),
        ("Not done", done["not_done_count"]),
        (
            f"Estimates total ({estimates['field_name'] or estimates['field'] or 'estimate'})",
            f"{estimates['total']:g}",
        ),
        ("Estimates done", f"{estimates['done_total']:g}"),
        ("Estimates not done", f"{estimates['not_done_total']:g}"),
        ("Issues with estimate", estimates["with_estimate"]),
        ("Missing estimate (unknown)", estimates["missing_estimate"]),
        ("Explicit blockers", len(report["blockers"])),
        ("Excluded (unauthorized/invisible)", report["excluded"]["count"]),
        ("Completeness", report["completeness"]),
    ]
    for status, count in report["status_counts"].items():
        rows.append((f"Status: {status}", count))
    return rows


def to_csv(report: dict) -> str:
    """Issue rows followed by the totals block; the same numbers as Markdown."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CSV_ISSUE_COLUMNS)
    for issue in report["issues"]:
        writer.writerow(
            [
                sanitize_cell(issue["key"]),
                sanitize_cell(issue["summary"]),
                sanitize_cell(issue["issue_type"]),
                sanitize_cell(issue["status"]),
                "yes" if issue["done"] else "no",
                sanitize_cell(issue["assignee"] or "Unassigned"),
                _fmt_estimate(issue["estimate"]),
                sanitize_cell(issue["blocked_evidence"] or ""),
            ]
        )
    writer.writerow([])
    writer.writerow(["metric", "value"])
    for metric, value in _totals(report):
        writer.writerow([sanitize_cell(metric), sanitize_cell(value)])
    writer.writerow([])
    writer.writerow(["narrative", sanitize_cell(report["narrative"])])
    return buffer.getvalue()
