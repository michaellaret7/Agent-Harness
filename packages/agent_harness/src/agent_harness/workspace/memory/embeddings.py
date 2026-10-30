"""Embedding interfaces and an OpenAI-compatible API implementation.

Embedder supplies a model name, a model-specific similarity floor, and embed().
ApiEmbedder uses client.build_client. Vectors from different models are never
compared; unit-length float32 storage makes cosine similarity a dot product.
"""
from __future__ import annotations

import math
from array import array
from typing import TYPE_CHECKING, Protocol

from agent_harness.client import build_client

if TYPE_CHECKING:
    from openai import OpenAI


# Cosine floors measured on paraphrased finance memories vs off-topic queries (2026-10-13):
#   text-embedding-3-small  matches 0.28-0.69, off-topic up to 0.08
#   voyage-4                matches 0.30-0.77, off-topic up to 0.23
KNOWN_FLOORS = {
    'openai/text-embedding-3-small': 0.2,
    'voyageai/voyage-4': 0.27,
}


class Embedder(Protocol):
    """Turns texts into vectors. `model` names the vector space; below `min_similarity` a memory is unrelated."""

    model: str
    min_similarity: float

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text, in order."""
        ...


#     ================================
# --> Helper funcs
#     ================================


def pack(vector: list[float]) -> bytes:
    """Unit-normalise a vector and pack it as float32 bytes for a BLOB column."""
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0

    return array('f', (x / norm for x in vector)).tobytes()


def unpack(blob: bytes) -> array:
    """Inverse of `pack`: float32 bytes back to a vector."""
    vector = array('f')
    vector.frombytes(blob)

    return vector


def similarity(a: array, b: array) -> float:
    """Cosine similarity of two unit vectors."""
    return sum(x * y for x, y in zip(a, b))


#     ================================
# --> Embedder
#     ================================


class ApiEmbedder:
    """Embeddings from an OpenAI-compatible endpoint; OpenRouter's `voyageai/voyage-4` by default.

    voyage-4 was chosen for speed (~155 ms per query) and the strongest finance
    retrieval evidence (reports/Embedding models for agent memory.md). For a
    model not in KNOWN_FLOORS, pass `min_similarity` measured on your own data.
    The client is built on first use, so constructing one needs no credentials
    (the same lazy rule as `Agent`).
    """

    def __init__(
        self,
        model: str = 'voyageai/voyage-4',
        provider: str = 'openrouter',
        min_similarity: float | None = None,
    ) -> None:
        if min_similarity is None and model not in KNOWN_FLOORS:
            raise ValueError(f'ApiEmbedder: no measured similarity floor for {model!r}; pass min_similarity=')

        self.model = model
        self.provider = provider
        self.min_similarity = min_similarity if min_similarity is not None else KNOWN_FLOORS[model]
        self._client: OpenAI | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed `texts` in one request."""
        if self._client is None:
            self._client, _ = build_client(self.provider, self.model)

        response = self._client.embeddings.create(model=self.model, input=texts)

        # The API may return items out of order; `index` ties each vector to its text
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
