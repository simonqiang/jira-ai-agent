"""Week 11 held-out evaluation: 50+ labeled queries and quality gates.

The corpus, labels and queries here were authored against the topic list
only — never tuned against the Week 10 development set in
``tests/test_retrieval.py`` (that set drove Week 10's checkpoint; this file
is the measurement). Gates: a relevant authorized result in the top five
for ≥90% of answerable queries, no-match abstention scored separately, and
duplicate-suggestion precision ≥80% across ≥20 reviewed suggestions.
Everything runs on the deterministic embedder and the in-memory storage
fake; the gated Postgres round-trip is covered by ``tests/test_storage.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scrum_agent.jira.models import Issue
from scrum_agent.retrieval.service import RetrievalService
from tests.storage_fakes import InMemoryStorage
from tests.test_retrieval import (
    _SITE,
    FakeEmbedder,
    FakeRecheckClient,
    _adf,
    _bullet_list,
    _heading,
    _issue,
    _issue_model,
    _para,
)

_PROJECT = "PAY"

# -- the held-out corpus: 12 issues, vocabulary disjoint from the dev set ----

_SUMMARIES = {
    "PAY-101": "Federated SSO login rejected",
    "PAY-102": "Onboarding welcome email never sent",
    "PAY-103": "CSV import rejects quoted commas",
    "PAY-104": "Notification digest delivered twice",
    "PAY-105": "Spreadsheet upload fails to parse",
    "PAY-106": "Dark theme contrast too low",
    "PAY-107": "API rate limit returns server errors",
    "PAY-108": "Retention purge skips archived records",
    "PAY-109": "Mobile app crashes on photo upload",
    "PAY-110": "Slack sync lags behind channel",
    "PAY-111": "Deadline shifts by one day across timezones",
    "PAY-112": "Dashboard chart renders empty",
}

_DESCRIPTIONS = {
    "PAY-101": _adf(
        _heading("Details"),
        _para("The identity provider rejects federated accounts during single sign-on."),
        _bullet_list(
            "Affects the corporate tenant",
            "Started after the identity provider certificate rotation",
        ),
    ),
    "PAY-102": _adf(
        _para("New workspace members do not receive the welcome message."),
        _heading("Impact"),
        _para("Onboarding feels broken before the first sign-in."),
    ),
    "PAY-103": _adf(
        _para("Uploading a CSV file with quoted commas aborts the import."),
        _bullet_list(
            "Parser treats the quoted comma as a separator",
            "Row counts diverge from the source file",
        ),
        _para("The upload-side twin is tracked in PAY-105."),
    ),
    "PAY-104": _adf(
        _para("The daily digest arrives twice each morning."),
        _para("Duplicate deliveries resemble the onboarding mail problem in PAY-102."),
    ),
    "PAY-105": _adf(
        _para(
            "Duplicating the CSV import defect tracked in PAY-103: the upload "
            "path chokes on quoted separators."
        ),
    ),
    "PAY-106": _adf(
        _para("Secondary text in the dark palette fails contrast checks."),
        _bullet_list("Settings page descriptions", "Tooltip labels"),
    ),
    "PAY-107": _adf(
        _para(
            "Burst traffic trips the rate limiter and the endpoint answers with 500 instead of 429."
        ),
    ),
    "PAY-108": _adf(
        _para("The retention job deletes active records but skips archived ones."),
        _para("Compliance review flagged the archive discrepancy."),
    ),
    "PAY-109": _adf(
        _para("The mobile client crashes when uploading photos larger than 10 MB."),
        _para("Crash reports reference the image pipeline. Trace attached as PAY-111."),
    ),
    "PAY-110": _adf(_para("Messages synced to Slack arrive minutes late during peak hours.")),
    "PAY-111": _adf(_para("Due dates move a day earlier for users west of UTC.")),
    "PAY-112": _adf(
        _para("The dashboard chart stays blank until reload."),
        _para("Related retention gap noted in PAY-108."),
    ),
}

# PAY-103 <-> PAY-105 are a real duplicate pair in both directions.
_DUPLICATE_LINKS = {
    "PAY-103": (("Is duplicated by", "PAY-105"),),
    "PAY-105": (("Duplicates", "PAY-103"),),
}


def _corpus() -> tuple[list[dict], list[Issue]]:
    snapshots = [_issue(key, _SUMMARIES[key], _DESCRIPTIONS[key]) for key in sorted(_SUMMARIES)]
    live = [
        _issue_model(key, _SUMMARIES[key], linked=_DUPLICATE_LINKS.get(key, ()))
        for key in sorted(_SUMMARIES)
    ]
    return snapshots, live


def _service() -> tuple[RetrievalService, FakeRecheckClient]:
    snapshots, live = _corpus()
    storage = InMemoryStorage()
    storage.start_run()
    for snapshot in snapshots:
        storage.upsert_snapshot(**snapshot, run_id=1)
    client = FakeRecheckClient(live)
    service = RetrievalService(
        storage=storage,
        client=client,
        embedder=FakeEmbedder(),
        site=_SITE,
        project_key=_PROJECT,
    )
    service.reindex()
    return service, client


# -- the held-out labels (fixed at authoring time) ---------------------------
#
# Answerable: (query, expected key that must appear in the top five).
_ANSWERABLE: list[tuple[str, str]] = [
    # PAY-101 federated SSO
    ("single sign-on rejects federated accounts", "PAY-101"),
    ("identity provider certificate rotation broke sign-in", "PAY-101"),
    ("SSO sign-in rejected for our users", "PAY-101"),
    # PAY-102 onboarding email
    ("new members never get their welcome message", "PAY-102"),
    ("onboarding email missing for fresh accounts", "PAY-102"),
    ("no welcome email reaches new members", "PAY-102"),
    # PAY-103 CSV import
    ("import aborts on comma fields", "PAY-103"),
    ("CSV parser treats separators wrong", "PAY-103"),
    ("quoted commas break the CSV import", "PAY-103"),
    # PAY-104 digest
    ("daily digest shows up twice", "PAY-104"),
    ("digest emails arrive duplicated every morning", "PAY-104"),
    ("morning notifications arrive in pairs", "PAY-104"),
    # PAY-105 spreadsheet upload
    ("uploading a spreadsheet fails to parse", "PAY-105"),
    ("separator quirks kill the upload path", "PAY-105"),
    ("file import chokes on quoted separators", "PAY-105"),
    # PAY-106 dark theme
    ("dark palette text is hard to read", "PAY-106"),
    ("contrast checks fail in dark themes", "PAY-106"),
    ("secondary text too dim in the dark theme", "PAY-106"),
    # PAY-108 retention
    ("archived records survive the purge", "PAY-108"),
    ("retention job deletes the wrong rows", "PAY-108"),
    ("compliance flagged the archive cleanup", "PAY-108"),
    # PAY-109 mobile crash
    ("app crashes when uploading large photos", "PAY-109"),
    ("mobile client dies on image upload", "PAY-109"),
    ("photo attachments crash the phone app", "PAY-109"),
    # PAY-111 timezones
    ("due dates move a day earlier abroad", "PAY-111"),
    ("timezone offsets shift deadlines", "PAY-111"),
    ("users west of UTC see wrong due dates", "PAY-111"),
    # PAY-112 dashboard chart
    ("dashboard chart stays blank", "PAY-112"),
    ("chart area empty until page reload", "PAY-112"),
    ("empty widget on the dashboard", "PAY-112"),
    # duplicate-flavored: the pair (103, 105) is one defect
    ("quoted separator problems when importing data", "PAY-103"),
    ("cannot import spreadsheets with commas", "PAY-103"),
    ("import fails on files with commas inside quotes", "PAY-103"),
]

# Identifier cases: a bare key matches only descriptions that mention it
# (the structured baseline resolves exact keys itself).
_IDENTIFIERS: list[tuple[str, str | None]] = [
    ("PAY-103", "PAY-105"),
    ("PAY-105", "PAY-103"),
    ("PAY-111", "PAY-109"),
    ("PAY-108", "PAY-112"),
    ("PAY-102", "PAY-104"),
    ("PAY-107", None),  # mentioned nowhere — must abstain
]

# No-match: vocabulary absent from the corpus — the service must abstain.
_NO_MATCH: list[str] = [
    "printer driver uninstall loops forever",
    "vpn tunnel drops every hour",
    "keyboard shortcuts collide with the editor",
    "font renders blurry on projectors",
    "battery drains in background tabs",
    "ssh session freezes after login banner",
    "database migration locks tables overnight",
    "audio echoes during calls",
    "favicon missing on the marketing site",
    "mouse cursor flickers on external monitors",
]

# Stale/revoked: queries whose true target drifted or was revoked after
# indexing; the key must come back excluded with the right reason and never
# as a hit.
_STALE_REVOKED: list[tuple[str, str, str]] = [
    ("rate limiter answers with server errors", "PAY-107", "stale"),
    ("timezone offsets shift deadlines", "PAY-111", "stale"),
    ("messages synced to Slack arrive late", "PAY-110", "revoked_or_deleted"),
    ("dashboard chart stays blank until reload", "PAY-112", "revoked_or_deleted"),
]


# Duplicate-suggestion precision: (query, keys a useful suggestion covers).
# The pair (103, 105) is one defect, so either side counts as relevant.
def _precision_cases() -> list[tuple[str, set[str]]]:
    pair = {"PAY-103", "PAY-105"}
    cases: list[tuple[str, set[str]]] = [
        ("quoted commas abort the import", pair),
        ("spreadsheet separator handling", pair),
        ("CSV import broken", pair),
        ("upload fails to parse files", pair),
    ]
    for query, expected in _ANSWERABLE:
        if expected in pair:
            continue
        cases.append((query, {expected}))
    return cases


def _evaluate(service: RetrievalService) -> dict:
    """Run the held-out labels; hits are post-recheck, so they are authorized."""
    top5 = 0
    for query, expected in _ANSWERABLE:
        hits = service.search(query).hits[:5]
        top5 += any(hit.issue_key == expected for hit in hits)
    abstained = 0
    abstention_cases = list(_NO_MATCH) + [
        query for query, expected in _IDENTIFIERS if expected is None
    ]
    for query in abstention_cases:
        result = service.search(query)
        abstained += not result.hits and not result.excluded
    return {
        "answerable_total": len(_ANSWERABLE),
        "top5_hits": top5,
        "top5_hit_rate": top5 / len(_ANSWERABLE),
        "abstention_total": len(abstention_cases),
        "abstained": abstained,
        "abstention_rate": abstained / len(abstention_cases),
    }


def _duplicate_precision(service: RetrievalService) -> dict:
    """Pooled precision over every suggestion the eval set reviews."""
    returned = 0
    relevant = 0
    for query, expected in _precision_cases():
        keys = {hit.issue_key for hit in service.search(query).hits}
        returned += len(keys)
        relevant += len(keys & expected)
    return {
        "reviewed_suggestions": returned,
        "relevant_suggestions": relevant,
        "precision": relevant / returned if returned else 0.0,
    }


# -- gates --------------------------------------------------------------------


def test_eval_set_has_fifty_plus_labeled_cases() -> None:
    total = len(_ANSWERABLE) + len(_IDENTIFIERS) + len(_NO_MATCH) + len(_STALE_REVOKED)
    assert total >= 50
    assert len(_ANSWERABLE) + len(_IDENTIFIERS) + len(_NO_MATCH) == 49
    assert len(_STALE_REVOKED) == 4


async def test_answerable_top5_hit_rate_at_least_90_percent() -> None:
    service, _ = _service()
    metrics = _evaluate(service)
    print(f"held-out answerable metrics: {metrics}")
    assert metrics["top5_hit_rate"] >= 0.90, metrics
    assert metrics["top5_hits"] >= 30


async def test_no_match_always_abstains() -> None:
    service, _ = _service()
    metrics = _evaluate(service)
    print(f"held-out abstention metrics: {metrics}")
    assert metrics["abstained"] == metrics["abstention_total"], metrics


async def test_duplicate_suggestion_precision_at_least_80_percent() -> None:
    service, _ = _service()
    metrics = _duplicate_precision(service)
    print(f"held-out duplicate-suggestion metrics: {metrics}")
    assert metrics["reviewed_suggestions"] >= 20, metrics
    assert metrics["precision"] >= 0.80, metrics


def test_confirmed_duplicate_labels_match_jira_links() -> None:
    service, _ = _service()
    for query, _expected in _precision_cases()[:4]:
        for hit in service.search(query).hits:
            if hit.issue_key in ("PAY-103", "PAY-105"):
                other = "PAY-105" if hit.issue_key == "PAY-103" else "PAY-103"
                assert hit.duplicate_keys == (other,)
            else:
                assert hit.duplicate_keys == ()


def test_stale_and_revoked_are_excluded_with_reason() -> None:
    service, client = _service()
    drifted = datetime.now(UTC) + timedelta(minutes=5)
    for key in ("PAY-107", "PAY-111"):
        client.issues[key] = _issue_model(key, _SUMMARIES[key], drifted)
    for key in ("PAY-110", "PAY-112"):
        del client.issues[key]

    for query, key, reason in _STALE_REVOKED:
        result = service.search(query)
        assert key not in [hit.issue_key for hit in result.hits]
        assert (key, reason) in [(item.issue_key, item.reason) for item in result.excluded]


def test_suggestion_text_never_enters_rendered_drafts() -> None:
    """Intent preservation: related-ticket text stays in related_tickets."""
    from scrum_agent.agent.tools import make_tools
    from scrum_agent.retrieval.models import ExcludedSource, RetrievalResult, RetrievedChunk
    from tests.agent_fakes import FakeJira, service_over
    from tests.test_agent_tools import _StubRetrieval

    sentinel_title = "SENTINEL-RELATED-TITLE prior work"
    sentinel_snippet = "SENTINEL-RELATED-SNIPPET copied wording"
    hit = RetrievedChunk(
        issue_key="PAY-103",
        source_url=f"https://{_SITE}/browse/PAY-103",
        title=sentinel_title,
        chunk_kind="summary",
        heading=None,
        snippet=sentinel_snippet,
        similarity=0.8,
        score=0.0328,
    )
    stub = _StubRetrieval(
        RetrievalResult(
            query="draft",
            hits=(hit,),
            excluded=(ExcludedSource(issue_key="PAY-110", reason="stale"),),
            jql="key in (PAY-103)",
            fetched_at=datetime.now(UTC),
        )
    )
    with service_over(FakeJira()) as probe:
        tools = {tool.name: tool.func for tool in make_tools(probe.service, None, stub)}
    payload = tools["draft_ticket"](
        issue_type="Story",
        fields={"goal": "import CSV files with quoted commas reliably"},
    )

    assert payload["ok"] is True
    assert payload["related_tickets"][0]["title"] == sentinel_title
    assert sentinel_title not in payload["rendered"]
    assert sentinel_snippet not in payload["rendered"]
    for section in payload["sections"]:
        assert sentinel_title not in str(section)
        assert sentinel_snippet not in str(section)
