"""MemoryStore: one agent's long-term memory, a SQLite file of short records plus search indexes.

Each memory is a sentence or two the agent chose to keep: a fact, lesson,
preference or event, tagged with an importance level. Three tables:

    memories         the records (source of truth)
    memories_fts     full-text index over content, for keyword search
    memory_vectors   one embedding per (memory, model), for semantic search

Search ranks candidates from both indexes by

    score = relevance + 0.5 x recency + 0.5 x importance      (each 0..1)
    relevance = 0.7 x semantic + 0.3 x keyword

where keyword is the BM25 match as a fraction of the best match, semantic is
cosine similarity (candidates below the embedder's `min_similarity` are dropped), recency is a sigmoid of an ACT-R style activation from how
often and how lately a memory was used, and importance is the agent's level
divided by the top level. Nothing is min-max scaled across candidates: that
would turn a near-tie in relevance into a 1-vs-0 gap importance could never
overcome. Only a Recall counts as a use; a search with `touch=False`
(inspection, tests) leaves rankings alone.

Vectors are derived data. If embedding fails when a memory is saved, the memory
is still stored and keyword-searchable, and the vector is backfilled the next
time a store opens with an embedder. Switching embedder models re-embeds into
new rows; the records are never touched.

The file lives outside the sandbox and is only reached through the memory
tools; the agent's code is never told where it is.
"""
from __future__ import annotations

import math
import re
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from agent_harness.workspace.memory.embeddings import Embedder, pack, similarity, unpack

MEMORY_KINDS = ('fact', 'lesson', 'preference', 'event')
IMPORTANCE = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}

# Candidates each index contributes before scoring
CANDIDATES = 50

# Relevance mix when both indexes are available (NOOA's weights)
SEMANTIC_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3

# Words too common to say anything about which memory is relevant
STOPWORDS = frozenset(
    'a an and are as at be by for from has have in is it its of on or that the this to was were will with'.split()
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,  -- ids are shown to the model, so never reused
    content       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    importance    INTEGER NOT NULL,
    created_at    REAL NOT NULL,
    last_used_at  REAL NOT NULL,
    use_count     INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content);
CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id     INTEGER NOT NULL,
    model         TEXT NOT NULL,
    vector        BLOB NOT NULL,
    PRIMARY KEY (memory_id, model)
);
"""


@dataclass(frozen=True)
class Memory:
    """One stored memory, with the score it ranked at and its relevance to the query (0 when not from a search)."""

    id: int
    content: str
    kind: str
    importance: str
    created_at: float
    last_used_at: float
    use_count: int
    score: float = 0.0
    relevance: float = 0.0


#     ================================
# --> Helper funcs
#     ================================


def _match_expression(query: str) -> str | None:
    """Turn free text into an FTS5 OR-query of its meaningful words; None when it has none."""
    words = {word for word in re.findall(r'[a-z0-9]+', query.lower()) if len(word) > 1 and word not in STOPWORDS}

    if not words:
        return None

    # Quoted, so FTS5 never reads a word as an operator (AND, NEAR, column names)
    return ' OR '.join(f'"{word}"' for word in sorted(words))


def _recency(use_count: int, last_used_at: float, now: float) -> float:
    """ACT-R style activation squashed to 0..1: grows with use, decays with time since last use."""
    hours_idle = max(now - last_used_at, 0.0) / 3600
    activation = math.log1p(use_count) - 0.5 * math.log1p(hours_idle)

    return 1 / (1 + math.exp(-activation))


def _level(importance: int) -> str:
    """Map the stored number back to the word the model uses."""
    return next(word for word, number in IMPORTANCE.items() if number == importance)


def _to_memory(row: sqlite3.Row, score: float, relevance: float) -> Memory:
    return Memory(
        id=row['id'], content=row['content'], kind=row['kind'], importance=_level(row['importance']),
        created_at=row['created_at'], last_used_at=row['last_used_at'], use_count=row['use_count'], score=score,
        relevance=relevance,
    )


#     ================================
# --> Store
#     ================================


class MemoryStore:
    """Create, search and delete memories in one SQLite file, with keyword and semantic search."""

    def __init__(self, path: Path, embedder: Embedder) -> None:
        self.path = path
        self.embedder = embedder
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as db:
            db.executescript(SCHEMA)

        self.backfill()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One connection per operation: committed on success, rolled back on error, always closed."""
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row

            with db:
                yield db

    def add(self, content: str, kind: str, importance: str) -> tuple[int, bool]:
        """Store a memory. Returns (id, created); identical content returns the existing id instead."""
        content = content.strip()
        now = time.time()

        with self._connect() as db:
            existing = db.execute('SELECT id FROM memories WHERE content = ?', (content,)).fetchone()

            if existing is not None:
                return existing['id'], False

            cursor = db.execute(
                'INSERT INTO memories (content, kind, importance, created_at, last_used_at) VALUES (?, ?, ?, ?, ?)',
                (content, kind, IMPORTANCE[importance], now, now),
            )

            memory_id = cursor.lastrowid
            assert memory_id is not None

            db.execute('INSERT INTO memories_fts (rowid, content) VALUES (?, ?)', (memory_id, content))

        # After the commit: a failed embedding must never lose the memory itself
        self._embed([(memory_id, content)])

        return memory_id, True

    def backfill(self) -> int:
        """Embed every memory that has no vector for the current model. Returns how many were embedded."""
        with self._connect() as db:
            missing = db.execute(
                'SELECT id, content FROM memories WHERE id NOT IN '
                '(SELECT memory_id FROM memory_vectors WHERE model = ?)',
                (self.embedder.model,),
            ).fetchall()

        return self._embed([(row['id'], row['content']) for row in missing])

    def _embed(self, items: list[tuple[int, str]]) -> int:
        """Store vectors for `items`; on any embedding failure, log and leave them for a later backfill."""
        if not items:
            return 0

        try:
            vectors = self.embedder.embed([content for _, content in items])

        except Exception as e:
            print(f'[MEMORY] embedding failed, keyword search only until backfill: {e!r}', file=sys.stderr)

            return 0

        with self._connect() as db:
            db.executemany(
                'INSERT OR REPLACE INTO memory_vectors (memory_id, model, vector) VALUES (?, ?, ?)',
                [(memory_id, self.embedder.model, pack(vector)) for (memory_id, _), vector in zip(items, vectors)],
            )

        return len(items)

    def forget(self, memory_id: int) -> bool:
        """Delete a memory and its index entries. Returns False when no memory has that id."""
        with self._connect() as db:
            deleted = db.execute('DELETE FROM memories WHERE id = ?', (memory_id,)).rowcount
            db.execute('DELETE FROM memories_fts WHERE rowid = ?', (memory_id,))
            db.execute('DELETE FROM memory_vectors WHERE memory_id = ?', (memory_id,))

        return deleted > 0

    def _keyword_scores(self, query: str) -> dict[int, float]:
        """{id: BM25 match as a fraction of the best match} for the top full-text candidates."""
        
        match = _match_expression(query)

        if match is None:
            return {}

        with self._connect() as db:
            rows = db.execute(
                'SELECT rowid, bm25(memories_fts) AS rank FROM memories_fts '
                'WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?',
                (match, CANDIDATES),
            ).fetchall()

        # BM25 is lower-is-better, so negate it
        scores = {row['rowid']: -row['rank'] for row in rows}
        best = max(scores.values(), default=0.0)

        return {memory_id: (score / best if best > 0 else 1.0) for memory_id, score in scores.items()}

    def _semantic_scores(self, query: str) -> dict[int, float]:
        """{id: cosine similarity} for the closest memories at or above the embedder's floor."""
        try:
            query_vector = unpack(pack(self.embedder.embed([query])[0]))

        except Exception as e:
            print(f'[MEMORY] query embedding failed, keyword search only: {e!r}', file=sys.stderr)

            return {}

        with self._connect() as db:
            rows = db.execute(
                'SELECT memory_id, vector FROM memory_vectors WHERE model = ?', (self.embedder.model,)
            ).fetchall()

        scores = {row['memory_id']: similarity(query_vector, unpack(row['vector'])) for row in rows}
        floor = self.embedder.min_similarity
        close = sorted((item for item in scores.items() if item[1] >= floor), key=lambda item: -item[1])

        return dict(close[:CANDIDATES])

    def search(self, query: str, limit: int, touch: bool) -> list[Memory]:
        """Return the best `limit` memories for `query`. `touch` records the result as a deliberate use."""
        keyword = self._keyword_scores(query)
        semantic = self._semantic_scores(query)
        candidates = keyword.keys() | semantic.keys()

        if not candidates:
            return []

        with self._connect() as db:
            marks = ','.join('?' * len(candidates))
            rows = db.execute(f'SELECT * FROM memories WHERE id IN ({marks})', tuple(candidates)).fetchall()

        now = time.time()
        top_level = max(IMPORTANCE.values())
        scored: list[Memory] = []

        for row in rows:
            memory_id = row['id']

            relevance = SEMANTIC_WEIGHT * semantic.get(memory_id, 0.0) + KEYWORD_WEIGHT * keyword.get(memory_id, 0.0)

            recency = _recency(row['use_count'], row['last_used_at'], now)
            importance = row['importance'] / top_level

            scored.append(_to_memory(row, relevance + 0.5 * recency + 0.5 * importance, relevance))

        # sort the memories by their score and only return the top limit from the list
        best = sorted(scored, key=lambda memory: memory.score, reverse=True)[:limit]

        if touch and best:
            with self._connect() as db:
                db.executemany(
                    'UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE id = ?',
                    [(now, memory.id) for memory in best],
                )

        return best

    def count(self) -> int:
        """Number of stored memories."""
        with self._connect() as db:
            return db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
