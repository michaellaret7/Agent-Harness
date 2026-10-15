"""MemoryStore: one agent's long-term memory, a SQLite file of short records plus search indexes.

Each memory is a sentence or two the agent chose to keep: a fact, lesson,
preference or event, tagged with an importance level. Four tables:

    memories         the records (source of truth)
    memories_fts     full-text index over content, for keyword search
    memory_vectors   one embedding per (memory, model), for semantic search
    curation         one row: how far the background curator has got (see curator.py)

Search fetches candidates from both indexes here and scores them with the
formula in ranking.py (all SQL stays in this file, all scoring math there). Only
a Recall counts as a use; a search with `touch=False` (inspection, tests) leaves
rankings alone.

Vectors are derived data. If embedding fails when a memory is saved, the memory
is still stored and keyword-searchable, and the vector is backfilled the next
time a store opens with an embedder. Switching embedder models re-embeds into
new rows; the records are never touched.

The file lives outside the sandbox and is only reached through the memory
tools; the agent's code is never told where it is.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from array import array
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from agent_harness.workspace.memory.embeddings import Embedder, pack, similarity, unpack
from agent_harness.workspace.memory.ranking import (
    CANDIDATES, keyword_scores, match_expression, meaningful_words, recency, score, semantic_scores,
)

MEMORY_KINDS = ('fact', 'lesson', 'preference', 'event')
IMPORTANCE = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}

# At or above this cosine a new memory is the same fact as a stored one, so it is not stored again.
# Measured on voyage-4 (2026-10-15): reworded duplicates 0.69-0.97, related-but-distinct facts up to 0.85;
# the duplicates below this line are left to the curator's judge
DUPLICATE_SIMILARITY = 0.90

# Texts per embedding request: embedding APIs reject oversized batches
EMBED_BATCH = 128

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
CREATE TABLE IF NOT EXISTS curation (
    id               INTEGER PRIMARY KEY CHECK (id = 1),  -- one row: where the last curation pass stopped
    last_curated_at  REAL NOT NULL
);
"""


@dataclass(frozen=True)
class Memory:
    """One stored memory, with the score it ranked at and its semantic similarity to the query.

    `semantic` is 0 when the memory was not a semantic match (below the embedder's floor) or not from a search.
    """

    id: int
    content: str
    kind: str
    importance: str
    created_at: float
    last_used_at: float
    use_count: int
    score: float = 0.0
    semantic: float = 0.0


#     ================================
# --> Helper funcs
#     ================================


def _level(importance: int) -> str:
    """Map the stored number back to the word the model uses."""
    return next(word for word, number in IMPORTANCE.items() if number == importance)


def _insert(
    db: sqlite3.Connection, 
    content: str, 
    kind: str, 
    importance: int, 
    created_at: float,
    last_used_at: float, 
    use_count: int
) -> int:

    """Insert one record and its keyword index entry. Returns the new id."""

    cursor = db.execute(
        'INSERT INTO memories (content, kind, importance, created_at, last_used_at, use_count) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (content, kind, importance, created_at, last_used_at, use_count),
    )

    memory_id = cursor.lastrowid
    assert memory_id is not None

    db.execute('INSERT INTO memories_fts (rowid, content) VALUES (?, ?)', (memory_id, content))

    return memory_id


def _delete(db: sqlite3.Connection, ids: list[int]) -> int:
    """Delete records and every index entry for them. Returns how many records existed."""
    marks = ','.join('?' * len(ids))
    deleted = db.execute(f'DELETE FROM memories WHERE id IN ({marks})', ids).rowcount
    db.execute(f'DELETE FROM memories_fts WHERE rowid IN ({marks})', ids)
    db.execute(f'DELETE FROM memory_vectors WHERE memory_id IN ({marks})', ids)

    return deleted


def _to_memory(row: sqlite3.Row, score: float = 0.0, semantic: float = 0.0) -> Memory:
    return Memory(
        id=row['id'], content=row['content'], kind=row['kind'], importance=_level(row['importance']),
        created_at=row['created_at'], last_used_at=row['last_used_at'], use_count=row['use_count'], score=score,
        semantic=semantic,
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
        """Store a memory. Returns (id, created).

        Identical content or a near-duplicate (cosine >= DUPLICATE_SIMILARITY) is not stored again:
        the existing id comes back, raised to the higher importance of the two.
        """
        content = content.strip()
        now = time.time()

        # Embedded before the write: a failed embedding skips the duplicate check, never loses the memory
        vector = self._embed_one(content)
        twin = self._near_duplicate(vector) if vector is not None else None

        with self._connect() as db:
            existing = db.execute('SELECT id FROM memories WHERE content = ?', (content,)).fetchone()
            twin = existing['id'] if existing is not None else twin

            if twin is not None:
                db.execute(
                    'UPDATE memories SET importance = MAX(importance, ?) WHERE id = ?', (IMPORTANCE[importance], twin)
                )

                return twin, False

            memory_id = _insert(db, content, kind, IMPORTANCE[importance], now, now, 0)

            # No vector means the API was down; backfill embeds it the next time a store opens
            if vector is not None:
                db.execute(
                    'INSERT INTO memory_vectors (memory_id, model, vector) VALUES (?, ?, ?)',
                    (memory_id, self.embedder.model, vector.tobytes()),
                )

        return memory_id, True

    def _embed_one(self, content: str) -> array | None:
        """The unit vector for `content`, or None when the embedding API fails."""
        try:
            return unpack(pack(self.embedder.embed([content])[0]))

        except Exception as e:
            print(f'[MEMORY] embedding failed, continuing without vectors: {e!r}', file=sys.stderr)

            return None

    def _near_duplicate(self, vector: array) -> int | None:
        """Id of the stored memory closest to `vector` when it is at or above DUPLICATE_SIMILARITY."""
        scores = {memory_id: similarity(vector, other) for memory_id, other in self.vectors().items()}
        closest = max(scores, key=scores.__getitem__, default=None)

        return closest if closest is not None and scores[closest] >= DUPLICATE_SIMILARITY else None

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
        """Store vectors for `items` in API-sized batches; on a failure, log and leave the rest for a later backfill."""
        embedded = 0

        for start in range(0, len(items), EMBED_BATCH):
            batch = items[start:start + EMBED_BATCH]

            try:
                vectors = self.embedder.embed([content for _, content in batch])

            except Exception as e:
                print(f'[MEMORY] embedding failed, keyword search only until backfill: {e!r}', file=sys.stderr)

                # The API is likely down for every batch, so stop instead of failing each one
                break

            with self._connect() as db:
                db.executemany(
                    'INSERT OR REPLACE INTO memory_vectors (memory_id, model, vector) VALUES (?, ?, ?)',
                    [(memory_id, self.embedder.model, pack(vector)) for (memory_id, _), vector in zip(batch, vectors)],
                )

            embedded += len(batch)

        return embedded

    def forget(self, memory_id: int) -> bool:
        """Delete a memory and its index entries. Returns False when no memory has that id."""
        return self.delete_many([memory_id]) > 0

    def delete_many(self, ids: list[int]) -> int:
        """Delete memories and their index entries in one transaction. Returns how many existed."""
        if not ids:
            return 0

        with self._connect() as db:
            return _delete(db, ids)

    def replace(self, ids: list[int], content: str, kind: str, importance: str) -> int:
        """Swap `ids` for one memory carrying their history: earliest creation, summed uses, latest use.

        Keeping the earliest creation time means the next curation pass does not see it as new.
        """
        content = content.strip()

        with self._connect() as db:
            marks = ','.join('?' * len(ids))
            history = db.execute(
                f'SELECT MIN(created_at), MAX(last_used_at), SUM(use_count) FROM memories WHERE id IN ({marks})', ids
            ).fetchone()

            _delete(db, ids)
            memory_id = _insert(db, content, kind, IMPORTANCE[importance], *history)

        # After the commit, as in `add`: a failed embedding must never lose the memory itself
        self._embed([(memory_id, content)])

        return memory_id

    def _fts_hits(self, words: set[str]) -> list[sqlite3.Row]:
        """The best full-text matches for `words`: rows of rowid, content and raw BM25 rank."""
        match = match_expression(words)

        if match is None:
            return []

        with self._connect() as db:
            return db.execute(
                'SELECT rowid, content, bm25(memories_fts) AS rank FROM memories_fts '
                'WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?',
                (match, CANDIDATES),
            ).fetchall()

    def search(self, query: str, limit: int, touch: bool) -> list[Memory]:
        """Return the best `limit` memories for `query`. `touch` records the result as a deliberate use."""
        floor = self.embedder.min_similarity
        words = meaningful_words(query)
        keyword = keyword_scores(words, self._fts_hits(words))

        # A failed query embedding leaves keyword search only
        query_vector = self._embed_one(query)
        semantic = semantic_scores(query_vector, self.vectors(), floor) if query_vector is not None else {}
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
            cosine = semantic.get(memory_id, 0.0)

            rank = score(
                cosine, keyword.get(memory_id, 0.0), floor,
                recency(row['use_count'], row['last_used_at'], now), row['importance'] / top_level,
            )

            scored.append(_to_memory(row, rank, cosine))

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

    def vectors(self) -> dict[int, array]:
        """{id: unit vector} for every memory embedded with the current model."""
        with self._connect() as db:
            rows = db.execute(
                'SELECT memory_id, vector FROM memory_vectors WHERE model = ?', (self.embedder.model,)
            ).fetchall()

        return {row['memory_id']: unpack(row['vector']) for row in rows}

    def ids_created_after(self, timestamp: float) -> list[int]:
        """Ids of memories created after `timestamp`, oldest first."""
        with self._connect() as db:
            rows = db.execute(
                'SELECT id FROM memories WHERE created_at > ? ORDER BY created_at, id', (timestamp,)
            ).fetchall()

        return [row['id'] for row in rows]

    def get_many(self, ids: list[int]) -> list[Memory]:
        """The memories with these ids, oldest first; unknown ids are skipped."""
        with self._connect() as db:
            marks = ','.join('?' * len(ids))
            rows = db.execute(
                f'SELECT * FROM memories WHERE id IN ({marks}) ORDER BY created_at, id', ids
            ).fetchall()

        return [_to_memory(row) for row in rows]

    def last_curated_at(self) -> float:
        """Creation time of the newest memory the last curation pass covered; 0 before the first pass."""
        with self._connect() as db:
            row = db.execute('SELECT last_curated_at FROM curation').fetchone()

        return row['last_curated_at'] if row else 0.0

    def mark_curated(self, timestamp: float) -> None:
        """Record that curation has covered every memory created up to `timestamp`."""
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO curation (id, last_curated_at) VALUES (1, ?)', (timestamp,))
