"""Week 10 retrieval: ADF chunking, hybrid search, and the live recheck.

Runs entirely on fakes (in-memory storage, deterministic embedder, scripted
recheck client) so plain ``pytest`` needs neither Postgres nor Jira; the SQL
itself is covered by the gated tests in ``tests/test_storage.py``.
"""

from __future__ import annotations

import re
import zlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scrum_agent.jira.models import Issue, LinkedWorkItem
from scrum_agent.retrieval.chunking import _MAX_CHARS, build_chunks
from scrum_agent.retrieval.embeddings import EmbeddingClient, EmbeddingError
from scrum_agent.retrieval.service import MIN_SIMILARITY, RetrievalService
from tests.storage_fakes import InMemoryStorage

_SITE = "test.atlassian.net"
_PROJECT = "PAY"
_T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# -- fixture builders -------------------------------------------------------


def _adf(*blocks: dict) -> dict:
    return {"type": "doc", "version": 1, "content": list(blocks)}


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _heading(text: str) -> dict:
    return {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": text}]}


def _bullet_list(*items: str) -> dict:
    return {
        "type": "bulletList",
        "content": [{"type": "listItem", "content": [_para(item)]} for item in items],
    }


def _table(*rows: tuple[str, ...]) -> dict:
    return {
        "type": "table",
        "content": [
            {
                "type": "tableRow",
                "content": [{"type": "tableCell", "content": [_para(cell)]} for cell in row],
            }
            for row in rows
        ],
    }


def _issue(key: str, summary: str, description: dict | str | None, updated: datetime = _T0) -> dict:
    return {
        "issue_id": f"100{key}",
        "issue_key": key,
        "summary": summary,
        "status": "Open",
        "issue_type": "Bug",
        "assignee": None,
        "updated": updated,
        "fields": {"description": description},
    }


def _issue_model(
    key: str,
    summary: str,
    updated: datetime = _T0,
    linked: tuple[tuple[str, str], ...] = (),
) -> Issue:
    return Issue(
        key=key,
        id=f"100{key}",
        summary=summary,
        status="Open",
        issue_type="Bug",
        updated="2026-10-01T12:00:00.000+0000"
        if updated == _T0
        else updated.isoformat(timespec="milliseconds").replace("+00:00", "+0000"),
        linked_work_items=tuple(
            LinkedWorkItem(
                relationship=relationship,
                key=other,
                summary=f"Summary of {other}",
                status="Open",
            )
            for relationship, other in linked
        ),
    )


class FakeEmbedder:
    """Deterministic bag-of-stem vectors: shared stems -> high cosine.

    Buckets use crc32 (not hash()) so collisions are stable across processes,
    and 4096 dims keeps unrelated texts near-orthogonal — below the no-match
    floor — for corpus-sized inputs.
    """

    model = "fake-embed-v1"

    def __init__(self, dims: int = 4096):
        self._dims = dims

    def embed(self, texts):
        vectors = []
        for text in texts:
            vec = [0.0] * self._dims
            for token in re.findall(r"[a-z0-9]+", text.casefold()):
                vec[zlib.crc32(token[:4].encode()) % self._dims] += 1.0
            norm = sum(value * value for value in vec) ** 0.5 or 1.0
            vectors.append([value / norm for value in vec])
        return vectors


class FakeRecheckClient:
    """Answers the one batched recheck JQL with the live issue set."""

    def __init__(self, issues: list[Issue]):
        self.issues = {issue.key: issue for issue in issues}
        self.jqls: list[str] = []

    def iter_search_jql(self, jql: str, **_):
        self.jqls.append(jql)
        for key in re.findall(r"[A-Z]+-\d+", jql):
            if key in self.issues:
                yield self.issues[key]


def _service(
    snapshots: list[dict], live: list[Issue]
) -> tuple[RetrievalService, InMemoryStorage, FakeRecheckClient]:
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
    return service, storage, client


# The Week 10 development corpus: 8 issues, deliberately varied wording.
_SUMMARIES = {
    "PAY-1": "Payment retry fails silently",
    "PAY-2": "Refund missing after order cancelled",
    "PAY-3": "Invoice PDF export times out",
    "PAY-4": "Login loop on expired session",
    "PAY-5": "Bulk export exhausts memory",
    "PAY-6": "Cleanup after export path duplication",
    "PAY-7": "Webhook queue drops events",
    "PAY-8": "Footer typo on settings page",
}


def _descriptions() -> dict[str, dict]:
    return {
        "PAY-1": _adf(
            _heading("Steps"),
            _para("A card charge times out and the retry never runs."),
            _bullet_list("Customer sees no error", "The order stays unpaid"),
        ),
        "PAY-2": _adf(
            _para("The order was cancelled but the refund never arrived."),
            _heading("Impact"),
            _para("Customers complain daily; finance reconciles manually."),
        ),
        "PAY-3": _adf(_para("Generating the invoice PDF times out for large accounts.")),
        "PAY-4": _adf(_para("After the session expires the login page reloads forever.")),
        "PAY-5": _adf(
            _heading("Observations"),
            _para("The bulk export grows past available memory and crashes."),
            _table(("Scope", "Peak RAM"), ("1000 rows", "3 GB")),
        ),
        "PAY-6": _adf(_para("Duplicates PAY-3's export path; fix together with that ticket.")),
        "PAY-7": _adf(_para("The webhook queue drops events under load.")),
        "PAY-8": _adf(_para("Typo in the settings page footer text.")),
    }


def _corpus() -> tuple[list[dict], list[Issue]]:
    descriptions = _descriptions()
    snapshots = [_issue(key, _SUMMARIES[key], descriptions[key]) for key in sorted(descriptions)]
    live = [_issue_model(key, _SUMMARIES[key]) for key in sorted(descriptions)]
    return snapshots, live


def _excluded(result, key: str, reason: str) -> bool:
    return [(item.issue_key, item.reason) for item in result.excluded] == [(key, reason)]


# -- chunking ----------------------------------------------------------------


def test_summary_is_its_own_chunk() -> None:
    chunks = build_chunks(summary="Payment retry fails silently", description=_adf(_para("Body")))
    assert [chunk.chunk_kind for chunk in chunks] == ["summary", "description"]
    assert chunks[0].chunk_index == 0 and chunks[1].chunk_index == 0
    assert chunks[0].heading is None
    assert chunks[0].content_hash != chunks[1].content_hash


def test_description_chunks_carry_the_nearest_heading() -> None:
    description = _adf(
        _para("Intro without a heading."),
        _heading("Impact"),
        _para("Customers complain daily."),
        _bullet_list("Daily reconciliation", "Manual workarounds"),
    )
    bodies = [
        c
        for c in build_chunks(summary="S", description=description)
        if c.chunk_kind == "description"
    ]
    assert [chunk.heading for chunk in bodies] == [None, "Impact", "Impact"]
    assert bodies[2].content == "- Daily reconciliation\n- Manual workarounds"


def test_table_rows_render_as_pipe_separated_cells() -> None:
    chunks = build_chunks(
        summary="S", description=_adf(_table(("Scope", "RAM"), ("1000 rows", "3 GB")))
    )
    body = [c for c in chunks if c.chunk_kind == "description"][0]
    assert body.content == "Scope | RAM\n1000 rows | 3 GB"


def test_long_blocks_are_split_never_dropped() -> None:
    text = "x" * (_MAX_CHARS * 2)
    bodies = [
        c
        for c in build_chunks(summary="S", description=_adf(_para(text)))
        if c.chunk_kind == "description"
    ]
    assert len(bodies) == 2
    assert all(len(chunk.content) <= _MAX_CHARS for chunk in bodies)


def test_plain_string_and_empty_descriptions_index_safely() -> None:
    assert [c.chunk_kind for c in build_chunks(summary="S", description="plain text")] == [
        "summary",
        "description",
    ]
    assert [c.chunk_kind for c in build_chunks(summary="S", description=None)] == ["summary"]
    assert build_chunks(summary="", description=None) == []


# -- indexing ----------------------------------------------------------------


def test_reindex_indexes_every_live_issue_then_skips_unchanged() -> None:
    service, storage, _ = _service(*_corpus())

    stats = service.reindex()
    assert stats["issues_indexed"] == 8 and stats["issues_skipped"] == 0
    assert len(storage.chunks) > 8  # summary + description chunks

    again = service.reindex()
    assert again["issues_skipped"] == 8 and again["issues_indexed"] == 0


def test_reindex_refreshes_a_changed_issue_and_prunes_tombstones() -> None:
    snapshots, live = _corpus()
    service, storage, _ = _service(snapshots, live)
    service.reindex()

    storage.tombstone_issue("PAY-8")
    changed = dict(snapshots[0], updated=_T0 + timedelta(hours=1))  # PAY-1 edited
    storage.upsert_snapshot(**changed, run_id=1)

    stats = service.reindex()
    assert stats["issues_indexed"] == 1  # only the changed issue re-embedded
    assert {chunk["issue_key"] for chunk in storage.chunks.values()} == set(_SUMMARIES) - {"PAY-8"}


# -- hybrid search and the live recheck ---------------------------------------


def test_search_deduplicates_to_one_hit_per_issue_and_rechecks_once() -> None:
    service, storage, client = _service(*_corpus())
    service.reindex()

    result = service.search("payment retried but silently fails")
    keys = [hit.issue_key for hit in result.hits]
    assert keys == ["PAY-1"]
    assert len(client.jqls) == 1 and "PAY-1" in client.jqls[0]
    assert result.hits[0].source_url == f"https://{_SITE}/browse/PAY-1"
    assert result.hits[0].similarity >= MIN_SIMILARITY


def test_search_attaches_confirmed_duplicate_keys_from_live_links() -> None:
    snapshots = [
        _issue("PAY-3", "refund duplicated twice", _para("Refund ran two times.")),
        _issue("PAY-6", "duplicate refund attempts", _para("See PAY-3.")),
    ]
    live = [
        _issue_model(
            "PAY-3",
            "refund duplicated twice",
            linked=(("Is duplicated by", "PAY-6"),),
        ),
        _issue_model(
            "PAY-6",
            "duplicate refund attempts",
            linked=(("Duplicates", "PAY-3"),),
        ),
    ]
    service, _, _ = _service(snapshots, live)
    service.reindex()

    hit = service.search("duplicate refund attempts").hits[0]
    assert hit.issue_key == "PAY-6"
    assert hit.duplicate_keys == ("PAY-3",)

    inward = service.search("refund duplicated twice")
    assert inward.hits[0].issue_key == "PAY-3"
    assert inward.hits[0].duplicate_keys == ("PAY-6",)


def test_issues_without_duplicate_links_carry_no_duplicate_keys() -> None:
    snapshots = [_issue("PAY-1", "payment retried but silently fails", _para("Retry gap."))]
    live = [_issue_model("PAY-1", "payment retried but silently fails")]
    service, _, _ = _service(snapshots, live)
    service.reindex()

    assert service.search("payment retried but silently fails").hits[0].duplicate_keys == ()


def test_search_falls_back_to_full_text_for_exact_terms() -> None:
    service, _, _ = _service(*_corpus())
    service.reindex()
    assert service.search("webhook queue drops events").hits[0].issue_key == "PAY-7"


def test_stale_source_is_excluded_and_invalidated() -> None:
    snapshots, live = _corpus()
    service, storage, client = _service(snapshots, live)
    service.reindex()
    client.issues["PAY-1"] = _issue_model("PAY-1", _SUMMARIES["PAY-1"], _T0 + timedelta(minutes=5))

    result = service.search("payment retried but silently fails")
    assert _excluded(result, "PAY-1", "stale")
    assert all(chunk["issue_key"] != "PAY-1" for chunk in storage.chunks.values())


def test_revoked_source_is_excluded_and_invalidated() -> None:
    snapshots, live = _corpus()
    service, storage, client = _service(snapshots, live)
    service.reindex()
    del client.issues["PAY-1"]  # no longer returned by the scoped JQL

    result = service.search("payment retried but silently fails")
    assert _excluded(result, "PAY-1", "revoked_or_deleted")
    assert all(chunk["issue_key"] != "PAY-1" for chunk in storage.chunks.values())


def test_no_match_returns_nothing_without_a_recheck() -> None:
    service, _, client = _service(*_corpus())
    service.reindex()

    result = service.search("kubernetes cluster autoscaling policy")
    assert result.hits == () and result.excluded == () and result.jql == ""
    assert client.jqls == []


def test_blank_query_is_rejected() -> None:
    service, _, _ = _service(*_corpus())
    with pytest.raises(ValueError):
        service.search("   ")


# -- Week 10 development set (20 cases) -----------------------------------------

# Paraphrases share word stems with their target but not its phrasing.
# Identifier queries either hit a chunk that mentions the key or must abstain —
# the structured search baseline (get_issue / search_issues) resolves exact
# keys itself. No-match queries share nothing and must return zero hits.
_PARAPHRASES = [
    ("customer payment retried but silently fails", "PAY-1"),
    ("cancelled order refund never arrived", "PAY-2"),
    ("invoice pdf generation keeps timing out", "PAY-3"),
    ("login page reloads forever after session expires", "PAY-4"),
    ("export uses all available memory and crashes", "PAY-5"),
    ("webhook events dropped under load", "PAY-7"),
    ("typo in the settings footer text", "PAY-8"),
    ("cleanup of the duplicated export path", "PAY-6"),
    ("invoice pdf export never completes for big accounts", "PAY-3"),
    ("session expiry causes an endless login redirect", "PAY-4"),
]
_IDENTIFIERS = [
    ("PAY-3", "PAY-6"),  # mentioned inside PAY-6's description
    ("PAY-1", None),  # never mentioned in any text: retrieval abstains
]
_NO_MATCH = [
    "kubernetes cluster autoscaling policy",
    "office snack budget planning",
    "quantum encryption rollout",
    "holiday party venue booking",
]


@pytest.mark.parametrize("query,expected", _PARAPHRASES, ids=[q for q, _ in _PARAPHRASES])
def test_dev_set_paraphrases_find_the_expected_issue(query: str, expected: str) -> None:
    service, _, _ = _service(*_corpus())
    service.reindex()
    result = service.search(query)
    assert result.hits, f"no hit at all for {query!r}"
    assert result.hits[0].issue_key == expected
    assert result.hits[0].similarity >= MIN_SIMILARITY


@pytest.mark.parametrize("query,expected", _IDENTIFIERS, ids=["mentioned", "unmentioned"])
def test_dev_set_identifiers_never_guess(query: str, expected: str | None) -> None:
    service, _, _ = _service(*_corpus())
    service.reindex()
    result = service.search(query)
    if expected is None:
        assert result.hits == ()
    else:
        assert [hit.issue_key for hit in result.hits] == [expected]


@pytest.mark.parametrize("query", _NO_MATCH)
def test_dev_set_no_match_abstains(query: str) -> None:
    service, _, _ = _service(*_corpus())
    service.reindex()
    result = service.search(query)
    assert result.hits == () and result.excluded == ()


def test_dev_set_stale_and_revoked_cases_are_excluded() -> None:
    """Two stale and two revoked cases against the same live-check baseline."""
    snapshots, live = _corpus()
    service, _, client = _service(snapshots, live)
    service.reindex()
    drifted = _T0 + timedelta(hours=2)
    for key in ("PAY-1", "PAY-7"):
        client.issues[key] = _issue_model(key, _SUMMARIES[key], drifted)
    del client.issues["PAY-2"]
    del client.issues["PAY-5"]

    for query, key, reason in (
        ("payment retried but silently fails", "PAY-1", "stale"),
        ("webhook events dropped under load", "PAY-7", "stale"),
        ("cancelled order refund never arrived", "PAY-2", "revoked_or_deleted"),
        ("export uses all available memory and crashes", "PAY-5", "revoked_or_deleted"),
    ):
        result = service.search(query)
        assert _excluded(result, key, reason), f"{query!r}: expected {key} {reason}"
        assert all(hit.issue_key != key for hit in result.hits)


def test_development_set_has_twenty_cases() -> None:
    assert len(_PARAPHRASES) + len(_IDENTIFIERS) + len(_NO_MATCH) + 4 == 20


# -- embedding client ----------------------------------------------------------


def _embedding_client(handler) -> EmbeddingClient:
    return EmbeddingClient(
        base_url="https://embed.example/api",
        api_key="sk-test",
        model="embed-1",
        transport=httpx.MockTransport(handler),
    )


def test_embedding_client_returns_vectors_in_input_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}] * 3})

    client = _embedding_client(handler)
    assert client.embed(["a", "b", "c"]) == [[0.1, 0.2]] * 3
    client.close()


def test_embedding_client_rejects_error_and_malformed_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if b"boom" in request.read():
            return httpx.Response(500, text="upstream down")
        return httpx.Response(200, json={"data": []})

    client = _embedding_client(handler)
    with pytest.raises(EmbeddingError, match="500"):
        client.embed(["boom"])
    with pytest.raises(EmbeddingError, match="batch"):
        client.embed(["fine"])
