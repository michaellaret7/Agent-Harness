"""MemoryCurator: keeps one agent's memory store current, in the background after every run.

Near-duplicates never reach the store (`MemoryStore.add` drops them on save).
The curator handles what a similarity cutoff cannot, and only for memories
saved since its last pass, so a pass costs what is new, not the store's size:

    agent.run ends ──► loop_end hook ──► start_background() ──► thread ──► curate()
                                                                            ├─ skip if a pass is running
                                                                            ├─ new = memories since the bookmark
                                                                            └─ move the bookmark past them

Every store change commits on its own, so a pass cut short leaves a
consistent store and the next pass picks up where it stopped.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_harness.workspace.memory.judge import Judge
from agent_harness.workspace.memory.store import MemoryStore

if TYPE_CHECKING:
    from agent_harness.hooks import HookContext


@dataclass(frozen=True)
class CurationReport:
    """What one pass did."""

    new: int


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
            new = self.store.get_many(self.store.ids_created_after(self.store.last_curated_at()))

            # The bookmark is the newest memory covered, not "now": a memory saved mid-pass stays new
            if new:
                self.store.mark_curated(new[-1].created_at)

            return CurationReport(new=len(new))

        finally:
            self._running.release()
