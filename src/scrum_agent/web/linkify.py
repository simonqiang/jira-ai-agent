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
    """Escape ``text``, then turn issue keys into browse links."""
    escaped = html.escape(text, quote=True)

    def replace(match: re.Match[str]) -> str:
        key = match.group(0)
        return (
            f'<a href="https://{jira_site}/browse/{key}" target="_blank" rel="noopener">{key}</a>'
        )

    return Markup(pattern.sub(replace, escaped))


def localtime(timestamp: str, timezone_name: str) -> str:
    """Render an ISO-8601 UTC timestamp in the configured timezone."""
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if moment.tzinfo is None:
        return timestamp
    return moment.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M %Z")
