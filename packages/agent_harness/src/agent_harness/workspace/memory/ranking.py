"""Pure scoring functions for Recall; store.py handles SQL and candidate retrieval.

    score = relevance x (1 + 0.5 x recency + 0.5 x importance)
    relevance = 0.7 x semantic + 0.3 x keyword

Components are 0..1: semantic rescales cosine above the embedder's floor;
keyword combines relative BM25 with query-word coverage; recency uses a sigmoid
of usage frequency and age; importance divides by the top level. Recency and
importance multiply relevance. Avoid candidate-wise min-max scaling, which
exaggerates small relevance differences.
"""
from __future__ import annotations

import math
import re
import sqlite3
from array import array

from agent_harness.workspace.memory.embeddings import similarity

# Candidates each index contributes before scoring
CANDIDATES = 50

# Relevance mix when both indexes are available (NOOA's weights)
SEMANTIC_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3

# Words too common to say anything about which memory is relevant
STOPWORDS = frozenset(
    'a an and are as at be by for from has have in is it its of on or that the this to was were will with'.split()
)


def meaningful_words(text: str) -> set[str]:
    """Lowercased words of `text` that say something about relevance (no stopwords or single letters)."""
    return {word for word in re.findall(r'[a-z0-9]+', text.lower()) if len(word) > 1 and word not in STOPWORDS}


def match_expression(words: set[str]) -> str | None:
    """Turn query words into an FTS5 OR-query; None when there are none."""
    if not words:
        return None

    # Quoted, so FTS5 never reads a word as an operator (AND, NEAR, column names)
    return ' OR '.join(f'"{word}"' for word in sorted(words))


def keyword_scores(words: set[str], hits: list[sqlite3.Row]) -> dict[int, float]:
    """{id: BM25 match as a fraction of the best match, times the share of query words matched}.

    `hits` are full-text index rows with `rowid`, `content` and `rank` (the raw BM25).
    """
    # BM25 is lower-is-better, so negate it
    scores = {row['rowid']: -row['rank'] for row in hits}
    best = max(scores.values(), default=0.0)

    # BM25 ÷ best is relative, so the top hit is 1.0 even on one shared word; coverage makes it absolute
    coverage = {row['rowid']: len(words & meaningful_words(row['content'])) / len(words) for row in hits}

    return {
        memory_id: (score / best if best > 0 else 1.0) * coverage[memory_id] for memory_id, score in scores.items()
    }


def semantic_scores(query_vector: array, vectors: dict[int, array], floor: float) -> dict[int, float]:
    """{id: cosine similarity} for the closest memories at or above the embedder's floor."""
    scores = {memory_id: similarity(query_vector, vector) for memory_id, vector in vectors.items()}
    close = sorted((item for item in scores.items() if item[1] >= floor), key=lambda item: -item[1])

    return dict(close[:CANDIDATES])


def recency(use_count: int, last_used_at: float, now: float) -> float:
    """ACT-R style activation squashed to 0..1: grows with use, decays with time since last use."""
    hours_idle = max(now - last_used_at, 0.0) / 3600
    activation = math.log1p(use_count) - 0.5 * math.log1p(hours_idle)

    return 1 / (1 + math.exp(-activation))


def score(cosine: float, keyword: float, floor: float, recency_score: float, importance: float) -> float:
    """One candidate's rank score. `importance` is the level divided by the top level (0..1)."""
    # Rescale so the floor maps to 0: no jump from 0 to `floor` at the cutoff, and the full 0..1 range is used
    closeness = (cosine - floor) / (1 - floor) if cosine else 0.0
    relevance = SEMANTIC_WEIGHT * closeness + KEYWORD_WEIGHT * keyword

    # Recency and importance boost relevance; they never stand in for it
    return relevance * (1 + 0.5 * recency_score + 0.5 * importance)
