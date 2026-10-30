"""Curate memory in the background after each run, skipping overlapping passes.

    loop_end -> reconcile new memories -> advance bookmark -> prune -> cap

Reconciliation asks a judge to keep, merge, or supersede new memories and their
neighbors. Pruning removes old, rarely used memories of at most medium
importance; capping evicts low-value memories. Both inspect the whole store,
but never prune or evict critical memories. Each change commits independently,
allowing interrupted passes to resume from the bookmark.
"""
from __future__ import annotations

import sys
import threading
import time
from array import array
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_harness.workspace.memory.embeddings import similarity
from agent_harness.workspace.memory.judge import Decision, Judge
from agent_harness.workspace.memory.ranking import recency
from agent_harness.workspace.memory.store import IMPORTANCE, Memory, MemoryStore

if TYPE_CHECKING:
    from agent_harness.hooks import HookContext

# At or above this cosine two memories may be the same fact, an update of it, or just related: the judge decides.
# Measured on voyage-4 (2026-10-15): reworded duplicates scored 0.69-0.97, so this catches all of them.
# No upper bound: duplicates saved while embedding was down skipped the check in `add` and land here too
RELATED_SIMILARITY = 0.65

# LLM cost ceiling: clusters judged per pass; the rest stay new and wait for the next pass
MAX_JUDGE_CALLS = 10

# Memories per cluster, the new one included: keeps each judge prompt short
MAX_CLUSTER = 6

# A memory is stale, and pruned, when it is older than this, rarely used and at most medium importance.
# Recency 0.2 after 30 days idle takes about six uses to stay above; a memory never recalled drops below it within a day
PRUNE_AFTER_DAYS = 30
PRUNE_BELOW_RECENCY = 0.2

# Hard ceiling on the store's size, enforced after pruning
MAX_MEMORIES = 500

DAY = 86_400


@dataclass(frozen=True)
class CurationReport:
    """What one pass did: memories it covered, and clusters the judge merged or superseded."""

    new: int
    reconciled: int
    pruned: int
    evicted: int


#     ================================
# --> Helper funcs
#     ================================


def _neighbours(memory_id: int, vectors: dict[int, array], skip: set[int]) -> list[int]:
    """Ids of the memories closest to `memory_id` at or above RELATED_SIMILARITY, at most MAX_CLUSTER - 1."""
    vector = vectors[memory_id]
    scores = {
        other_id: similarity(vector, other) for other_id, other in vectors.items()
        if other_id != memory_id and other_id not in skip
    }
    related = [other_id for other_id, score in scores.items() if score >= RELATED_SIMILARITY]

    return sorted(related, key=lambda other_id: -scores[other_id])[:MAX_CLUSTER - 1]


def _stale(memory: Memory, now: float) -> bool:
    """Old, rarely used and at most medium importance: safe to forget."""
    old = now - memory.created_at > PRUNE_AFTER_DAYS * DAY
    unused = recency(memory.use_count, memory.last_used_at, now) < PRUNE_BELOW_RECENCY
    minor = IMPORTANCE[memory.importance] <= IMPORTANCE['medium']

    return old and unused and minor


def _value(memory: Memory, now: float) -> float:
    """How much a memory is worth keeping (0..1): its importance, lifted by recent use."""
    importance = IMPORTANCE[memory.importance] / max(IMPORTANCE.values())

    return importance * (0.5 + 0.5 * recency(memory.use_count, memory.last_used_at, now))


#     ================================
# --> Curator
#     ================================


class MemoryCurator:
    """Runs curation passes over one store, one at a time."""

    def __init__(self, store: MemoryStore, judge: Judge) -> None:
        self.store = store
        self.judge = judge
        self._running = threading.Lock()

    def start_background(self, ctx: HookContext | None = None) -> threading.Thread:
        """loop_end hook: start a pass on its own thread and return at once.

        Non-daemon, so a script that exits right after its run waits for the
        pass instead of killing it halfway. Returns the thread (hooks ignore it; tests join it).
        """
        # start the new thread to execute the curate method
        thread = threading.Thread(target=self.curate, name='memory-curator')
        thread.start()

        return thread

    def curate(self) -> CurationReport | None:
        """One full pass. Returns None, doing nothing, when another pass is already running."""
        if not self._running.acquire(blocking=False):
            return None

        try:
            # find the newly saved memories since the last curation run
            bookmark = self.store.last_curated_at()
            new_ids = self.store.ids_created_after(bookmark)
            new = self.store.get_many(new_ids)

            # run the judge on the new memories and their related neighbors
            covered, reconciled = self._reconcile(new)

            # The bookmark is the newest memory covered, not "now": a memory saved mid-pass,
            # or left unjudged once MAX_JUDGE_CALLS ran out, stays new for the next pass
            if covered:
                self.store.mark_curated(covered[-1].created_at)

            # delete what is stale, then the least valuable while the store is over its size limit
            pruned, evicted = self._tidy()

            return CurationReport(new=len(covered), reconciled=reconciled, pruned=pruned, evicted=evicted)

        finally:
            self._running.release()

    def _reconcile(self, new: list[Memory]) -> tuple[list[Memory], int]:
        """Judge each new memory with its related neighbours, oldest first.

        Returns the new memories covered (stops early at MAX_JUDGE_CALLS) and how many clusters changed.
        """
        if not new:
            return [], 0

        vectors = self.store.vectors()
        covered: list[Memory] = []
        gone: set[int] = set()
        calls = reconciled = 0

        for memory in new:
            # No vector (embedding was down) or already merged away this pass: nothing to compare
            if memory.id not in vectors or memory.id in gone:
                covered.append(memory)
                continue

            # Skip new memories already judged this pass, so no pair is judged twice
            neighbours = _neighbours(memory.id, vectors, gone | {done.id for done in covered})

            if neighbours and calls == MAX_JUDGE_CALLS:
                break

            if neighbours:
                calls += 1
                # this runs the memory judgment logic
                # it takes in a list of memory ids and returns a set of ids that were changed
                changed = self._judge([memory.id, *neighbours])
                gone |= changed
                reconciled += bool(changed)

            covered.append(memory)

        return covered, reconciled

    def _judge(self, ids: list[int]) -> set[int]:
        """Ask the judge about one cluster and apply its decision. Returns the ids it removed."""
        try:
            decision = self.judge(self.store.get_many(ids))

        except Exception as e:
            # Logged and left as is: retrying a cluster the judge cannot answer would stall every later pass
            print(f'[MEMORY] curation judge failed, cluster {ids} left unchanged: {e!r}', file=sys.stderr)

            return set()

        return self._apply(decision)

    def _apply(self, decision: Decision) -> set[int]:
        """Carry out a judge decision on the store. Returns the ids it removed."""
        if decision.action == 'merge':
            assert decision.content and decision.kind and decision.importance  # Decision validates a merge
            self.store.replace(decision.ids, decision.content, decision.kind, decision.importance)

        elif decision.action == 'supersede':
            self.store.delete_many(decision.ids)

        return set(decision.ids) if decision.action != 'keep' else set()

    def _tidy(self) -> tuple[int, int]:
        """Prune stale memories, then evict the lowest-value non-critical ones down to MAX_MEMORIES.

        Returns (pruned, evicted). Both are deleted in one transaction.
        """
        now = time.time()
        everything = self.store.get_many(self.store.ids_created_after(0.0))  # 0 = every memory

        stale = {memory.id for memory in everything if _stale(memory, now)}
        kept = [memory for memory in everything if memory.id not in stale]

        overflow = max(len(kept) - MAX_MEMORIES, 0)
        evictable = sorted((m for m in kept if m.importance != 'critical'), key=lambda m: _value(m, now))
        evicted = [memory.id for memory in evictable[:overflow]]

        self.store.delete_many([*stale, *evicted])

        return len(stale), len(evicted)
