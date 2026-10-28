"""Inject relevant memories after the cached prompt prefix.

    latest user message -> search(touch=False) -> semantic matches -> <memories>

The renderer calls this provider before each request. Results are cached until
the latest user message changes. Injection does not record memory use, and only
matches above the embedder's similarity floor qualify.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_harness.workspace.memory.ranking import CANDIDATES
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
        # Rank every candidate, so keyword-only hits never crowd semantic matches out of the top LIMIT
        hits = self.store.search(query, CANDIDATES, touch=False)
        relevant = [memory for memory in hits if memory.semantic > 0][:LIMIT]

        if not relevant:
            return None

        # return the relevant memories as a formatted block
        return '<memories>\n' + '\n'.join(memory_line(memory) for memory in relevant) + '\n</memories>'
