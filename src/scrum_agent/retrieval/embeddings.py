"""OpenAI-compatible embedding client for the retrieval index.

The endpoint is any service exposing POST {base_url}/embeddings with the
OpenAI request/response shape (z.ai, OpenAI, Gemini's compatibility layer,
Ollama, …), configured through SCRUM_AGENT_EMBEDDING_*.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

_BATCH = 64


class EmbeddingError(RuntimeError):
    """The embedding endpoint failed or returned a malformed response."""


class EmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.model = model
        self._url = f"{base_url}/embeddings"
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts in bounded batches; output order matches input order."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH):
            vectors.extend(self._embed_batch(list(texts[start : start + _BATCH])))
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            response = self._client.post(self._url, json={"model": self.model, "input": batch})
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding endpoint unreachable: {exc}") from exc
        if response.status_code != 200:
            raise EmbeddingError(
                f"embedding endpoint returned {response.status_code}: {response.text[:200]}"
            )
        data: Any = response.json().get("data")
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError("embedding response does not match the request batch")
        vectors = []
        for item in data:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not vector:
                raise EmbeddingError("embedding response carries no usable vector")
            vectors.append([float(value) for value in vector])
        return vectors
