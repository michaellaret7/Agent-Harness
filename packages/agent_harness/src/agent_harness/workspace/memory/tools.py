"""Memory tools and their prompt protocol.

    Remember(content, kind, importance) -> store a memory
    Recall(query, limit)               -> search and record use
    Forget(memory_id)                  -> delete a memory

Optional auto_memory_recall also injects a dynamic <memories> block.
The store's path is never exposed to model code.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from agent_harness.tooling.decorator import Param, agent_tool, bind_tool
from agent_harness.tooling.result import ToolResult
from agent_harness.workspace.memory.store import Memory, MemoryStore

MemoryKind = Literal['fact', 'lesson', 'preference', 'event']
Importance = Literal['critical', 'high', 'medium', 'low']

MEMORY_PROMPT = """<memory_protocol>
You have a long-term memory that persists across tasks. It is separate from skills:
- Memory holds facts, lessons, preferences and events: what is true or what happened.
- Skills hold procedures: how to do something. Never store the same knowledge in both.
Use only the Remember, Recall and Forget tools. Never look for or open the memory store through code.

Reading:
- At the start of a task that may relate to past work, call Recall with the task's key subjects (companies, data sources, methods, etc.).

Writing:
- Most tasks should save nothing or one or two memories. Save only what a future task would need and could
  not easily rediscover: a data quirk, a user preference, a mistake to avoid, a dated conclusion.
- Write each memory as one or two self-contained sentences that name their subject (never "it" or "the
  company"), and include the date for anything that changes over time.
- Recall before saving so you do not store duplicates. Forget a memory you find to be wrong.
- kind: fact | lesson | preference | event. importance: critical | high | medium | low.
</memory_protocol>"""


#     ================================
# --> Helper funcs
#     ================================


def memory_line(memory: Memory) -> str:
    """One memory as the model sees it; the id lets it Forget the memory later."""
    return f'- [{memory.id}] {memory.kind}, {memory.importance}: {memory.content}'


#     ================================
# --> Tools
#     ================================


@agent_tool(name='Remember')
def remember(
    content: Annotated[str, Param(description='One or two self-contained sentences naming their subject.')],
    kind: Annotated[MemoryKind, Param(description='fact, lesson, preference or event.')],
    importance: Annotated[Importance, Param(description='How much a future task would lose without it.')],
    _store: MemoryStore | None = None,
) -> ToolResult:
    """Save a memory for future tasks. Use sparingly: most tasks should save nothing or one or two memories."""
    assert _store is not None, 'Remember must be bound to a MemoryStore'

    if not content.strip():
        return ToolResult('error: content is empty', status='error')

    memory_id, created = _store.add(content, kind, importance)

    if not created:
        return ToolResult(f'Already remembered as [{memory_id}]; nothing saved.', status='ok')

    return ToolResult(f'Remembered as [{memory_id}].', status='ok')


@agent_tool(name='Recall', safe_parallel=True)
def recall(
    query: Annotated[str, Param(description='Words describing what you want to know.')],
    limit: Annotated[int, Param(description='Maximum memories to return.', min_val=1, max_val=20)] = 5,
    _store: MemoryStore | None = None,
) -> ToolResult:
    """Search long-term memory. Returns matching memories with their ids, best first."""
    assert _store is not None, 'Recall must be bound to a MemoryStore'

    memories = _store.search(query, limit, touch=True)

    if not memories:
        return ToolResult('No matching memories.', status='ok')

    return ToolResult('\n'.join(memory_line(memory) for memory in memories), status='ok')


@agent_tool(name='Forget')
def forget(
    memory_id: Annotated[int, Param(description='The id shown in brackets, e.g. 12 for [12].')],
    _store: MemoryStore | None = None,
) -> ToolResult:
    """Delete a memory you found to be wrong or no longer useful."""
    assert _store is not None, 'Forget must be bound to a MemoryStore'

    if not _store.forget(memory_id):
        return ToolResult(
            f'error: no memory [{memory_id}]. It may have been merged or pruned (memory curation '
            f'runs in the background, so ids quoted earlier can go stale). Recall to see current ids; '
            f'do not retry.',
            status='error',
        )

    return ToolResult(f'Forgot [{memory_id}].', status='ok')


def memory_tools(store: MemoryStore) -> list[dict[str, Any]]:
    """The three memory tools bound to `store`."""
    return [bind_tool(tool, _store=store) for tool in (remember, recall, forget)]
