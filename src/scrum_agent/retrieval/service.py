"""Permission-aware semantic search over collected issue chunks.

Reindex chunks+embeddings from the Week 4 snapshots; search fuses
metadata-filtered vector retrieval with Postgres full-text retrieval and
deduplicates by issue. Before any retrieved text is returned (and so reaches
the model), the service rechecks every candidate against live Jira with one
user-scoped JQL query: sources whose revision drifted come back as ``stale``
and sources Jira no longer returns come back as ``revoked_or_deleted`` — in
both cases their chunks are invalidated so the next reindex refreshes them
(spec §7: fetch authoritative revisions or omit stale sources).
"""

from __future__ import annotations

from datetime import UTC, datetime

from scrum_agent.jira.client import JiraClient
from scrum_agent.jira.models import Issue, parse_jira_time
from scrum_agent.retrieval.chunking import build_chunks
from scrum_agent.retrieval.embeddings import EmbeddingClient
from scrum_agent.retrieval.models import ExcludedSource, RetrievalResult, RetrievedChunk

# Hits whose best chunk similarity falls below this floor are not "related
# work" — they are how a no-match request abstains instead of inventing a
# nearest neighbour (the check that Week 3 applies to answers, applied to
# retrieval). Tuned per embedding model if the Week 11 evaluations demand it.
MIN_SIMILARITY = 0.3


def _duplicate_keys(issue: Issue) -> tuple[str, ...]:
    """Keys of issues linked by a real Jira Duplicate link, either direction.

    A retrieval hit is only ever a *potential* duplicate; these links are
    what make a duplicate confirmed.
    """
    return tuple(
        link.key for link in issue.linked_work_items if "duplicat" in link.relationship.casefold()
    )


class RetrievalService:
    def __init__(
        self,
        *,
        storage,
        client: JiraClient,
        embedder: EmbeddingClient,
        site: str,
        project_key: str,
    ):
        self._storage = storage
        self._client = client
        self._embedder = embedder
        self._site = site
        self._project_key = project_key

    @classmethod
    def build(cls, settings):
        """Construct the service from settings (database, Jira, embedding API)."""
        from scrum_agent.config import require_database_settings, require_embedding_settings
        from scrum_agent.jira.client import JiraClient as _Client
        from scrum_agent.storage.db import connect
        from scrum_agent.storage.repository import PgStorage

        require_database_settings(settings)
        base_url, api_key, model = require_embedding_settings(settings)
        return cls(
            storage=PgStorage(connect(settings)),
            client=_Client(settings),
            embedder=EmbeddingClient(base_url=base_url, api_key=api_key, model=model),
            site=settings.jira_site,
            project_key=settings.jira_project_key,
        )

    # -- indexing -----------------------------------------------------------

    def reindex(self) -> dict:
        """Chunk and embed every live snapshot whose revision or model changed.

        Unchanged issues keep their existing chunks (the collector's snapshot
        ``updated`` stamp is the source revision); chunks of tombstoned issues
        are removed.
        """
        signatures = self._storage.chunk_signatures()
        seen = 0
        indexed = 0
        for snapshot in self._storage.live_snapshots():
            seen += 1
            signature = signatures.get(snapshot["issue_id"])
            if signature == (snapshot["updated"], self._embedder.model):
                continue
            chunks = build_chunks(
                summary=snapshot["summary"],
                description=(snapshot["fields"] or {}).get("description"),
            )
            source_updated = snapshot["updated"]
            issue_key = snapshot["issue_key"]
            if chunks:
                vectors = self._embedder.embed([chunk.embed_text for chunk in chunks])
                self._storage.replace_chunks(
                    issue_id=snapshot["issue_id"],
                    issue_key=issue_key,
                    site=self._site,
                    project_key=self._project_key,
                    source_updated=source_updated,
                    rows=[
                        (
                            chunk.chunk_kind,
                            chunk.chunk_index,
                            chunk.heading,
                            chunk.content,
                            chunk.content_hash,
                            vector,
                        )
                        for chunk, vector in zip(chunks, vectors, strict=True)
                    ],
                    embedding_model=self._embedder.model,
                )
                indexed += 1
        self._storage.prune_orphan_chunks()
        return {
            "issues_seen": seen,
            "issues_indexed": indexed,
            "issues_skipped": seen - indexed,
            "embedding_model": self._embedder.model,
        }

    # -- search -------------------------------------------------------------

    def search(self, query: str, *, top_k: int = 5) -> RetrievalResult:
        """Hybrid semantic + full-text search, verified against live Jira."""
        query = query.strip()
        if not query:
            raise ValueError("query must not be blank")
        vector = self._embedder.embed([query])[0]
        rows = self._storage.hybrid_search(
            query=query,
            query_embedding=vector,
            embedding_model=self._embedder.model,
            limit=top_k,
        )
        if not rows:
            return RetrievalResult(
                query=query, hits=(), excluded=(), jql="", fetched_at=datetime.now(UTC)
            )
        rows = [row for row in rows if float(row["similarity"]) >= MIN_SIMILARITY]
        if not rows:
            return RetrievalResult(
                query=query, hits=(), excluded=(), jql="", fetched_at=datetime.now(UTC)
            )
        keys = tuple(row["issue_key"] for row in rows)
        jql = "key in (" + ", ".join(keys) + ")"
        live = {issue.key: issue for issue in self._client.iter_search_jql(jql)}

        hits: list[RetrievedChunk] = []
        excluded: list[ExcludedSource] = []
        for row in rows:
            key = row["issue_key"]
            issue = live.get(key)
            if issue is None:
                reason = "revoked_or_deleted"
            elif parse_jira_time(issue.updated) != row["source_updated"]:
                reason = "stale"
            else:
                hits.append(
                    RetrievedChunk(
                        issue_key=key,
                        source_url=row["source_url"],
                        title=row["title"],
                        chunk_kind=row["chunk_kind"],
                        heading=row["heading"],
                        snippet=row["content"],
                        similarity=round(float(row["similarity"]), 6),
                        score=round(float(row["score"]), 6),
                        duplicate_keys=_duplicate_keys(issue),
                    )
                )
                continue
            excluded.append(ExcludedSource(issue_key=key, reason=reason))
            self._storage.delete_chunks(issue_key=key)
        return RetrievalResult(
            query=query,
            hits=tuple(hits),
            excluded=tuple(excluded),
            jql=jql,
            fetched_at=datetime.now(UTC),
        )
