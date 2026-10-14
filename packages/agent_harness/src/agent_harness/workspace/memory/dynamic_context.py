"""DynamicMemoryContext: puts the memories most relevant to the latest request into the dynamic context.

A dynamic context provider (see `context/renderer.py`): the renderer calls it
before every model call, and its `<memories>` block lands in the trailing
`<dynamic_context>` message, after the cached prefix, so it never breaks the
prompt cache.

    latest user message ──► store.search(touch=False) ──► keep relevance >= MIN_RELEVANCE ──► <memories>

Three rules keep it cheap and honest:
- It searches only when the latest user message changes; every other call
  reuses the cached block, so a long tool loop costs one embedding call.
- `touch=False`: an injection is not a deliberate use. Counting it would keep
  boosting whatever was injected, a loop that locks the same memories on top.
- It filters on relevance, not score. Score adds recency and importance, so an
  off-topic memory still scores well; only relevance says it matches the request.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_harness.workspace.memory.store import MemoryStore
from agent_harness.workspace.memory.tools import memory_line

if TYPE_CHECKING:
    from agent_harness.agent import Agent

MEMORY_CONTEXT_PROMPT = """
<memory_context_protocol>
A `<memories>` block in the dynamic context shows saved memories that closely match the latest request.
It is a starting point, not a full search: call Recall for subjects it does not cover.
</memory_context_protocol>
"""

# Kept small: only the clearly relevant few, Recall is there for more
LIMIT = 3

# Keyword relevance tops out at 0.3 (its weight), so a keyword-only match never clears this:
# a memory needs real semantic similarity to be injected unasked
MIN_RELEVANCE = 0.35


#     ================================
# --> Helper funcs
#     ================================


def _latest_user_text(messages: list[dict[str, Any]]) -> str | None:
    """Text of the most recent user message, or None when there is none."""
    for message in reversed(messages):
        if message['role'] != 'user':
            continue

        content = message.get('content')

        return content if isinstance(content, str) and content.strip() else None

    return None


#     ================================
# --> Provider
#     ================================


class DynamicMemoryContext:
    """Dynamic context provider: the memories that match the agent's latest request."""

    def __init__(self, store: MemoryStore, agent: Agent) -> None:
        self.store = store
        self.agent = agent
        self._query: str | None = None
        self._block: str | None = None

    def __call__(self) -> str | None:
        """Render the `<memories>` block, or None when nothing is relevant enough."""
        # pull the latest user message from the agent object's message list 
        query = _latest_user_text(self.agent.messages)

        # Rendered before every model call, so search only when the request changes
        if query != self._query:
            self._query = query
            self._block = self._render(query)

        return self._block

    def _render(self, query: str | None) -> str | None:
        if query is None:
            return None

        # search the memory store for relevant context 
        hits = self.store.search(query, LIMIT, touch=False)
        relevant = [memory for memory in hits if memory.relevance >= MIN_RELEVANCE]

        if not relevant:
            return None

        # return the relevant memories as a formatted block
        return '<memories>\n' + '\n'.join(memory_line(memory) for memory in relevant) + '\n</memories>'
