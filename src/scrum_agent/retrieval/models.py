"""Result models for retrieval — parity with the search package's models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RetrievedChunk(BaseModel):
    """One verified chunk; its text was re-checked against live Jira."""

    issue_key: str
    source_url: str
    title: str
    chunk_kind: str
    heading: str | None
    snippet: str
    similarity: float  # raw cosine to the query; the no-match floor applies here
    score: float  # fused vector+full-text rank


class ExcludedSource(BaseModel):
    """A candidate hit whose text must not reach the model, with the reason."""

    issue_key: str
    reason: str  # 'stale' | 'revoked_or_deleted'


class RetrievalResult(BaseModel):
    query: str
    hits: tuple[RetrievedChunk, ...]
    excluded: tuple[ExcludedSource, ...]
    jql: str
    fetched_at: datetime
