"""Workspace long-term memory contracts. Run: uv run --all-packages pytest tests/test_memory.py"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_harness import AgentWorkspace
from agent_harness.workspace.memory.store import Memory, MemoryStore
from contract_support import message_text, scripted_agent, tool_call, tool_delta

LEASE_LESSON = 'FMP totalDebt for MSFT includes about $88B of lease obligations, so interest/debt understates its borrowing cost.'


#     ================================
# --> Helper funcs
#     ================================


def call(name: str, args: dict, call_id: str) -> list[dict]:
    """One model turn that calls `name` with `args`."""
    return [tool_delta(tool_call(name, args, call_id))]


def tool_reply(request: dict) -> str:
    """Text of the last tool result the model saw in `request`."""
    return message_text(next(m for m in reversed(request['messages']) if m['role'] == 'tool'))


def use_count(workspace: AgentWorkspace, memory_id: int) -> int:
    """Read a memory's use counter straight from the store."""
    with workspace.memory._connect() as db:
        return db.execute('SELECT use_count FROM memories WHERE id = ?', (memory_id,)).fetchone()[0]


#     ================================
# --> Contract tests
#     ================================


@pytest.fixture(autouse=True)
def harness_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every workspace and memory store in this test's temp folder."""
    monkeypatch.setenv('AGENT_HARNESS_HOME', str(tmp_path))


def test_remembered_lesson_is_recalled_in_a_later_task() -> None:
    """A memory saved in one run is found by Recall in a later run, and is never injected on its own."""
    first = [
        call('Remember', {'content': LEASE_LESSON, 'kind': 'lesson', 'importance': 'high'}, 'save'),
        [{'content': 'Valued MSFT.'}],
    ]

    with scripted_agent(first) as (agent, api, sink):
        AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).attach(agent)
        agent.run('Run a DCF on MSFT.', sink=sink)

    assert tool_reply(api.requests[1]) == 'Remembered as [1].'

    # A fresh agent and workspace object, as in a new process
    second = [call('Recall', {'query': 'MSFT debt leases'}, 'look'), [{'content': 'done'}]]

    with scripted_agent(second) as (agent, api, sink):
        workspace = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder())
        workspace.attach(agent)
        agent.run('What is the cost of debt for MSFT?', sink=sink)

    # Not in the first request: memory only reaches the model through Recall
    assert LEASE_LESSON not in json.dumps(api.requests[0]['messages'])

    assert tool_reply(api.requests[1]) == f'- [1] lesson, high: {LEASE_LESSON}'
    assert use_count(workspace, 1) == 1


def test_ranking_weighs_relevance_and_importance() -> None:
    """The best match on the query's words ranks first; importance breaks near-ties."""
    # Keyword-only: to an embedder both NVDA memories say the same thing, so it would keep just one
    store = AgentWorkspace('analyst', memory=True, embedder=DownEmbedder()).memory
    store.add('NVDA reports earnings in late February.', 'fact', 'low')
    store.add('NVDA earnings beats have preceded falls three quarters running.', 'lesson', 'critical')
    store.add('The user prefers tables over prose for valuations.', 'preference', 'medium')

    ranked = [memory.content for memory in store.search('NVDA earnings', limit=5, touch=False)]

    assert ranked[0] == 'NVDA earnings beats have preceded falls three quarters running.'
    assert 'The user prefers tables over prose for valuations.' not in ranked


def test_duplicates_are_not_stored_twice() -> None:
    """Remembering identical content returns the existing memory instead of a copy."""
    with scripted_agent([
        call('Remember', {'content': LEASE_LESSON, 'kind': 'lesson', 'importance': 'high'}, 'a'),
        call('Remember', {'content': f'  {LEASE_LESSON} ', 'kind': 'fact', 'importance': 'low'}, 'b'),
        [{'content': 'done'}],
    ]) as (agent, api, sink):
        workspace = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder())
        workspace.attach(agent)
        agent.run('Save the lease lesson.', sink=sink)

    assert tool_reply(api.requests[2]) == 'Already remembered as [1]; nothing saved.'
    assert workspace.memory.count() == 1


def test_forget_deletes_and_reports_unknown_ids() -> None:
    """Forget removes a memory from storage and search; an unknown id is an error the model sees."""
    with scripted_agent([
        call('Remember', {'content': LEASE_LESSON, 'kind': 'lesson', 'importance': 'high'}, 'a'),
        call('Forget', {'memory_id': 1}, 'b'),
        call('Forget', {'memory_id': 99}, 'c'),
        [{'content': 'done'}],
    ]) as (agent, api, sink):
        workspace = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder())
        workspace.attach(agent)
        agent.run('Tidy memory.', sink=sink)

    assert tool_reply(api.requests[2]) == 'Forgot [1].'
    assert tool_reply(api.requests[3]) == 'error: no memory [99]'
    assert workspace.memory.count() == 0
    assert workspace.memory.search('MSFT leases', limit=5, touch=False) == []


def test_invalid_kind_is_rejected_before_storage() -> None:
    """The tool's enum is enforced, so a made-up kind never reaches the store."""
    with scripted_agent([
        call('Remember', {'content': 'x is y', 'kind': 'rumour', 'importance': 'high'}, 'a'),
        [{'content': 'done'}],
    ]) as (agent, api, sink):
        workspace = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder())
        workspace.attach(agent)
        agent.run('Save something.', sink=sink)

    assert 'kind' in tool_reply(api.requests[1])
    assert workspace.memory.count() == 0


def test_store_lives_apart_from_the_sandbox_and_its_path_is_never_shown(tmp_path: Path) -> None:
    """The store is in the memory branch, not beside the sandbox, and no request reveals where."""
    with scripted_agent([
        call('Remember', {'content': LEASE_LESSON, 'kind': 'lesson', 'importance': 'high'}, 'a'),
        call('Recall', {'query': 'MSFT leases'}, 'b'),
        [{'content': 'done'}],
    ]) as (agent, api, sink):
        workspace = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder())
        workspace.attach(agent)
        agent.run('Run a DCF on MSFT.', sink=sink)

    assert workspace.memory.path == tmp_path / 'memory' / 'analyst.sqlite'
    assert workspace.memory.path.exists()
    assert [p.name for p in workspace.root.iterdir()] == ['sandbox']

    everything_sent = json.dumps(api.requests)
    assert 'analyst.sqlite' not in everything_sent
    assert str(tmp_path / 'memory').replace('\\', '\\\\') not in everything_sent


def test_memory_is_off_unless_asked_for(tmp_path: Path) -> None:
    """A workspace without memory=True adds no memory tools or protocol and creates no store."""
    with scripted_agent([[{'content': 'done'}]]) as (agent, api, sink):
        workspace = AgentWorkspace('analyst')
        workspace.attach(agent)
        agent.run('Run a DCF on MSFT.', sink=sink)

    tool_names = {t['function']['name'] for t in api.requests[0]['tools']}

    assert workspace.memory is None
    assert {'Remember', 'Recall', 'Forget'}.isdisjoint(tool_names)
    assert 'ExecuteCode' in tool_names
    assert '<memory_protocol>' not in message_text(api.requests[0]['messages'][0])
    assert not (tmp_path / 'memory').exists()


#     ================================
# --> Semantic search
#     ================================

# Words that mean the same thing share one dimension, the way a real embedding model places them close together
CONCEPTS = {
    'microsoft': 0, 'msft': 0, 'lease': 1, 'leases': 1, 'leasing': 1, 'debt': 2, 'borrowing': 2,
    'nvidia': 3, 'nvda': 3, 'earnings': 4, 'results': 4, 'paris': 5, 'france': 5,
}


class ConceptEmbedder:
    """Deterministic offline embedder: one dimension per concept, synonyms share it."""

    min_similarity = 0.3

    def __init__(self, model: str = 'concepts-v1') -> None:
        self.model = model
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        vectors = []

        for text in texts:
            vector = [0.0] * 6

            for word in text.lower().replace("'", ' ').split():
                index = CONCEPTS.get(word.strip('.,'))

                if index is not None:
                    vector[index] += 1.0

            vectors.append(vector)

        return vectors


class DownEmbedder:
    """An embedding API that is unavailable."""

    model = 'concepts-v1'
    min_similarity = 0.3

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ConnectionError('embedding API unreachable')


MSFT_LEASES = "Microsoft's lease obligations inflate its reported borrowing."


def vector_models(workspace_path: Path) -> list[tuple[int, str]]:
    """(memory_id, model) for every stored vector."""
    import sqlite3

    with sqlite3.connect(workspace_path) as db:
        return sorted(db.execute('SELECT memory_id, model FROM memory_vectors').fetchall())


def test_semantic_search_finds_what_keywords_miss() -> None:
    """Different words, same meaning: keyword search misses it, the embedder finds it."""
    # With the embedding API down, only keyword search is left
    keyword_only = AgentWorkspace('analyst', memory=True, embedder=DownEmbedder()).memory
    keyword_only.add(MSFT_LEASES, 'lesson', 'high')

    assert keyword_only.search('MSFT leasing', limit=5, touch=False) == []

    semantic = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    found = semantic.search('MSFT leasing', limit=5, touch=False)

    assert [memory.content for memory in found] == [MSFT_LEASES]


def test_semantic_search_ignores_unrelated_memories() -> None:
    """Below the similarity floor a memory is not returned, even when it is the only one."""
    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    store.add(MSFT_LEASES, 'lesson', 'high')

    assert store.search('Paris France', limit=5, touch=False) == []


def test_failed_embedding_keeps_the_memory_and_backfills_later(tmp_path: Path) -> None:
    """An API outage never loses a memory; the next store opened with a working embedder fills the vector in."""
    down = AgentWorkspace('analyst', memory=True, embedder=DownEmbedder()).memory
    memory_id, created = down.add(MSFT_LEASES, 'lesson', 'high')

    assert created and down.count() == 1
    assert vector_models(down.path) == []

    embedder = ConceptEmbedder()
    healed = AgentWorkspace('analyst', memory=True, embedder=embedder).memory

    assert vector_models(healed.path) == [(memory_id, 'concepts-v1')]
    assert healed.search('MSFT leasing', limit=5, touch=False)[0].id == memory_id


def test_semantic_match_outranks_one_shared_keyword() -> None:
    """Importance boosts relevance but cannot replace it: a critical memory sharing one word loses to a real match."""
    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    store.add('MSFT leases debt.', 'fact', 'low')
    store.add('Cloud budgets in Paris doubled.', 'lesson', 'critical')

    ranked = [memory.content for memory in store.search('Microsoft cloud', limit=5, touch=False)]

    assert ranked == ['MSFT leases debt.', 'Cloud budgets in Paris doubled.']


class BatchLimitedEmbedder(ConceptEmbedder):
    """An embedding API that rejects requests over its batch limit, as real ones do."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        from agent_harness.workspace.memory.store import EMBED_BATCH

        if len(texts) > EMBED_BATCH:
            raise ValueError(f'batch of {len(texts)} exceeds the API limit')

        return super().embed(texts)


def test_backfill_splits_large_stores_into_batches() -> None:
    """A store bigger than one API batch is still fully embedded on backfill."""
    from agent_harness.workspace.memory.store import EMBED_BATCH

    down = AgentWorkspace('analyst', memory=True, embedder=DownEmbedder()).memory

    for n in range(EMBED_BATCH * 2 + 1):
        down.add(f'MSFT lease note {n}.', 'fact', 'low')

    healed = AgentWorkspace('analyst', memory=True, embedder=BatchLimitedEmbedder()).memory

    assert len(vector_models(healed.path)) == EMBED_BATCH * 2 + 1


def test_switching_models_re_embeds_without_touching_records() -> None:
    """A new embedding model gets its own vectors; old vectors stay, and Forget removes every one."""
    first = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder('concepts-v1')).memory
    memory_id, _ = first.add(MSFT_LEASES, 'lesson', 'high')

    second = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder('concepts-v2')).memory

    assert vector_models(second.path) == [(memory_id, 'concepts-v1'), (memory_id, 'concepts-v2')]
    assert second.count() == 1

    second.forget(memory_id)

    assert vector_models(second.path) == []


def test_embedder_without_memory_is_rejected() -> None:
    """An embedder only makes sense with memory on, so the mistake fails at construction."""
    with pytest.raises(ValueError, match='embedder needs memory=True'):
        AgentWorkspace('analyst', embedder=ConceptEmbedder())


def test_api_embedder_needs_a_measured_floor() -> None:
    """Each model scores similarity differently, so an unmeasured model must be given its own floor."""
    from agent_harness.workspace import ApiEmbedder

    assert ApiEmbedder().model == 'voyageai/voyage-4'
    assert ApiEmbedder().min_similarity == 0.27
    assert ApiEmbedder('some/new-model', min_similarity=0.4).min_similarity == 0.4

    with pytest.raises(ValueError, match='no measured similarity floor'):
        ApiEmbedder('some/new-model')


def test_memory_comes_with_semantic_search_by_default() -> None:
    """memory=True alone gives the voyage-4 embedder; nothing else has to be wired."""
    from agent_harness.workspace import ApiEmbedder

    store = AgentWorkspace('analyst', memory=True).memory

    assert store is not None
    assert isinstance(store.embedder, ApiEmbedder)
    assert store.embedder.model == 'voyageai/voyage-4'


#     ================================
# --> Automatic recall (dynamic context)
#     ================================

TABLES = 'The user prefers tables over prose for valuations.'


def test_auto_recall_injects_matching_memories_once_per_request() -> None:
    """With auto_memory_recall, matching memories appear in every request's dynamic context, searched once and never counted as a use."""
    embedder = ConceptEmbedder()
    workspace = AgentWorkspace('analyst', memory=True, embedder=embedder, auto_memory_recall=True)
    workspace.memory.add(MSFT_LEASES, 'lesson', 'high')
    workspace.memory.add(TABLES, 'preference', 'medium')
    calls_before = embedder.calls

    with scripted_agent([call('Forget', {'memory_id': 99}, 'a'), [{'content': 'done'}]]) as (agent, api, sink):
        workspace.attach(agent)
        agent.run('What is the cost of debt for MSFT?', sink=sink)

    assert '<memory_context_protocol>' in message_text(api.requests[0]['messages'][0])

    # Both model calls carry the block; the unrelated preference is left out
    for request in api.requests:
        block = message_text(request['messages'][-1])
        assert f'- [1] lesson, high: {MSFT_LEASES}' in block
        assert TABLES not in block

    # One query embedding for the whole run, and injection does not boost rankings
    assert embedder.calls == calls_before + 1
    assert use_count(workspace, 1) == 0


def test_auto_recall_injects_nothing_for_an_unrelated_request() -> None:
    """No memory clears the relevance bar, so no block is added at all."""
    workspace = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder(), auto_memory_recall=True)
    workspace.memory.add(MSFT_LEASES, 'lesson', 'high')

    with scripted_agent([[{'content': 'done'}]]) as (agent, api, sink):
        workspace.attach(agent)
        agent.run('Plan a trip to Paris.', sink=sink)

    assert '<memories>' not in json.dumps(api.requests[0]['messages'][1:])


def test_auto_recall_without_memory_is_rejected() -> None:
    """auto_memory_recall reads the memory store, so it fails at construction without one."""
    with pytest.raises(ValueError, match='auto_memory_recall needs memory=True'):
        AgentWorkspace('analyst', auto_memory_recall=True)


#     ================================
# --> Curation primitives
#     ================================

NVDA_RESULTS = 'NVDA earnings beat on data center demand.'


def set_history(store: MemoryStore, memory_id: int, created_at: float, last_used_at: float, uses: int) -> None:
    """Backdate a memory, as if it had been saved and used long ago."""
    with store._connect() as db:
        db.execute(
            'UPDATE memories SET created_at = ?, last_used_at = ?, use_count = ? WHERE id = ?',
            (created_at, last_used_at, uses, memory_id),
        )


def test_curation_reads_cover_only_what_is_new() -> None:
    """The curator's bookmark persists, and only memories created after it count as new."""
    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    old, _ = store.add(MSFT_LEASES, 'lesson', 'high')
    new, _ = store.add(NVDA_RESULTS, 'fact', 'medium')
    set_history(store, old, created_at=100.0, last_used_at=100.0, uses=0)

    assert store.last_curated_at() == 0.0
    assert store.ids_created_after(0.0) == [old, new]

    store.mark_curated(100.0)
    reopened = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory

    assert reopened.last_curated_at() == 100.0
    assert reopened.ids_created_after(100.0) == [new]
    assert set(reopened.vectors()) == {old, new}
    assert [memory.content for memory in reopened.get_many([new, old, 999])] == [MSFT_LEASES, NVDA_RESULTS]


def test_replace_swaps_a_cluster_for_one_memory_with_its_history() -> None:
    """The consolidated memory is searchable, keeps the cluster's history, and the originals are gone."""
    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    first, _ = store.add(MSFT_LEASES, 'lesson', 'high')
    second, _ = store.add('MSFT debt matters.', 'fact', 'low')
    other, _ = store.add(NVDA_RESULTS, 'fact', 'medium')
    set_history(store, first, created_at=100.0, last_used_at=150.0, uses=1)
    set_history(store, second, created_at=200.0, last_used_at=250.0, uses=4)

    merged_id = store.replace([first, second], 'MSFT lease obligations count as borrowing.', 'lesson', 'high')

    [merged] = store.get_many([merged_id])

    assert (merged.created_at, merged.last_used_at, merged.use_count) == (100.0, 250.0, 5)
    assert store.count() == 2
    assert vector_models(store.path) == [(other, 'concepts-v1'), (merged_id, 'concepts-v1')]
    assert [memory.id for memory in store.search('Microsoft leasing', limit=5, touch=False)] == [merged_id]


def test_delete_many_clears_every_index() -> None:
    """Deleted memories vanish from records, keyword search and vectors; unknown ids are not counted."""
    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    first, _ = store.add(MSFT_LEASES, 'lesson', 'high')
    second, _ = store.add(NVDA_RESULTS, 'fact', 'medium')

    assert store.delete_many([first, second, 999]) == 2
    assert store.count() == 0
    assert vector_models(store.path) == []
    assert store.search('MSFT NVDA earnings leases', limit=5, touch=False) == []


def test_near_duplicates_are_caught_on_save() -> None:
    """A reworded memory returns the stored one, raised to the higher importance; a related one is still saved."""
    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    original, _ = store.add(MSFT_LEASES, 'lesson', 'low')

    twin, created = store.add('MSFT leasing counts as debt.', 'fact', 'critical')

    assert (twin, created) == (original, False)
    assert store.get_many([original])[0].importance == 'critical'

    # Shares two of three concepts (cosine 0.82): related, not the same fact, so it is the judge's call later
    related, created = store.add('MSFT debt matters.', 'fact', 'low')

    assert created and related != original
    assert store.count() == 2
    assert vector_models(store.path) == [(original, 'concepts-v1'), (related, 'concepts-v1')]


#     ================================
# --> Judge
#     ================================


def judge_cluster() -> list[Memory]:
    """Two stored memories saying the same thing, as the curator would hand them to the judge."""
    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    first, _ = store.add(MSFT_LEASES, 'lesson', 'high')
    second, _ = store.add('MSFT debt matters.', 'fact', 'low')

    return store.get_many([first, second])


def test_llm_judge_turns_a_json_answer_into_a_decision() -> None:
    """The judge sends the cluster oldest first in JSON mode and returns the validated merge."""
    from agent_harness.workspace.memory.judge import LlmJudge

    cluster = judge_cluster()
    answer = {'action': 'merge', 'ids': [1, 2], 'content': 'MSFT lease obligations count as debt.',
              'kind': 'lesson', 'importance': 'high'}

    with scripted_agent([], summaries=[json.dumps(answer)]) as (agent, api, _):
        judge = LlmJudge(model='contract-model')
        judge._client = agent.client
        decision = judge(cluster)

    [request] = api.summary_requests

    assert decision.model_dump() == answer
    assert request['response_format'] == {'type': 'json_object'}
    assert request['messages'][1]['content'].splitlines()[0].startswith('[1] saved ')
    assert request['messages'][1]['content'].endswith('| fact | low | MSFT debt matters.')


def test_llm_judge_rejects_answers_the_curator_must_not_act_on() -> None:
    """Ids outside the cluster, superseding everything, or a merge with no rewrite all raise instead of reaching the store."""
    from agent_harness.workspace.memory.judge import LlmJudge

    cluster = judge_cluster()
    bad_answers = [
        {'action': 'supersede', 'ids': [7]},
        {'action': 'supersede', 'ids': None},
        {'action': 'supersede', 'ids': [1, 2]},
        {'action': 'merge', 'ids': [1, 2], 'kind': 'lesson', 'importance': 'high'},
        'not json',
    ]

    with scripted_agent([], summaries=[a if isinstance(a, str) else json.dumps(a) for a in bad_answers]) as (agent, _, __):
        judge = LlmJudge(model='contract-model')
        judge._client = agent.client

        for answer in bad_answers:
            with pytest.raises(ValueError):
                judge(cluster)


#     ================================
# --> Curator
#     ================================


def unused_judge(memories: list) -> None:
    """A judge for passes that must not need one."""
    raise AssertionError('the judge was called')


def test_each_pass_covers_only_memories_saved_since_the_last() -> None:
    """The bookmark moves past every memory a pass covered, so the next pass sees only what is new."""
    from agent_harness.workspace.memory.curator import MemoryCurator

    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    curator = MemoryCurator(store, unused_judge)
    store.add(MSFT_LEASES, 'lesson', 'high')
    store.add(NVDA_RESULTS, 'fact', 'medium')

    assert curator.curate().new == 2
    assert curator.curate().new == 0

    store.add('The user prefers tables over prose for valuations.', 'preference', 'medium')

    assert curator.curate().new == 1


def test_a_second_pass_is_skipped_while_one_runs() -> None:
    """Passes never overlap: a pass started mid-pass returns None at once, and the running one finishes."""
    import threading

    from agent_harness.workspace.memory.curator import MemoryCurator

    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    store.add(MSFT_LEASES, 'lesson', 'high')
    curator = MemoryCurator(store, unused_judge)

    # Hold the first pass inside its read until the second one has tried
    entered, release = threading.Event(), threading.Event()
    read = store.ids_created_after

    def held_read(timestamp: float) -> list[int]:
        entered.set()
        release.wait(timeout=5)

        return read(timestamp)

    store.ids_created_after = held_read
    first = curator.start_background()

    assert entered.wait(timeout=5)
    assert curator.curate() is None

    release.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert store.last_curated_at() > 0


class ScriptedJudge:
    """A judge that returns scripted decisions and records the clusters it was shown."""

    def __init__(self, *decisions: dict) -> None:
        self.decisions = list(decisions)
        self.clusters: list[list[str]] = []

    def __call__(self, memories: list):
        from agent_harness.workspace.memory.judge import Decision

        self.clusters.append([memory.content for memory in memories])
        decision = self.decisions.pop(0)

        if isinstance(decision, Exception):
            raise decision

        return Decision(**decision)


# Related to MSFT_LEASES (cosine 0.82): close enough to judge, too far to drop on save
MSFT_DEBT = 'MSFT debt matters.'


def test_judge_merge_replaces_the_cluster_with_one_memory() -> None:
    """A merge leaves one rewritten memory; each related pair is judged once, not once per member."""
    from agent_harness.workspace.memory.curator import MemoryCurator

    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    first, _ = store.add(MSFT_LEASES, 'lesson', 'low')
    second, _ = store.add(MSFT_DEBT, 'fact', 'high')
    judge = ScriptedJudge({'action': 'merge', 'ids': [first, second], 'content': 'MSFT lease obligations are debt.',
                           'kind': 'lesson', 'importance': 'high'})

    report = MemoryCurator(store, judge).curate()

    assert judge.clusters == [[MSFT_LEASES, MSFT_DEBT]]
    assert (report.new, report.reconciled) == (2, 1)
    assert [(m.content, m.importance) for m in store.search('Microsoft leasing', limit=5, touch=False)] == [
        ('MSFT lease obligations are debt.', 'high'),
    ]
    assert store.count() == 1


def test_judge_supersede_deletes_only_the_outdated_memory() -> None:
    """Superseded ids are deleted; the newer memory stays as it was."""
    from agent_harness.workspace.memory.curator import MemoryCurator

    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    old, _ = store.add(MSFT_LEASES, 'lesson', 'high')
    current, _ = store.add(MSFT_DEBT, 'fact', 'high')

    MemoryCurator(store, ScriptedJudge({'action': 'supersede', 'ids': [old]})).curate()

    assert [memory.id for memory in store.get_many([old, current])] == [current]


def test_a_kept_cluster_is_never_judged_again() -> None:
    """Keep changes nothing, and the bookmark stops later passes from paying for the same question."""
    from agent_harness.workspace.memory.curator import MemoryCurator

    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    store.add(MSFT_LEASES, 'lesson', 'high')
    store.add(MSFT_DEBT, 'fact', 'high')
    judge = ScriptedJudge({'action': 'keep'})
    curator = MemoryCurator(store, judge)

    assert curator.curate().reconciled == 0
    assert curator.curate().new == 0
    assert len(judge.clusters) == 1 and store.count() == 2


def test_judge_calls_are_capped_and_the_rest_wait_for_the_next_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past MAX_JUDGE_CALLS a pass stops; the unjudged memories stay new and the next pass judges them."""
    from agent_harness.workspace.memory import curator as curator_module

    monkeypatch.setattr(curator_module, 'MAX_JUDGE_CALLS', 1)
    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory

    for content in (MSFT_LEASES, MSFT_DEBT, NVDA_RESULTS, 'NVDA earnings in Paris.'):
        store.add(content, 'fact', 'medium')

    judge = ScriptedJudge({'action': 'keep'}, {'action': 'keep'})
    curator = curator_module.MemoryCurator(store, judge)

    assert curator.curate().new == 2
    assert curator.curate().new == 2
    assert judge.clusters == [[MSFT_LEASES, MSFT_DEBT], [NVDA_RESULTS, 'NVDA earnings in Paris.']]


def test_a_failed_judge_leaves_the_cluster_and_the_pass_goes_on() -> None:
    """A judge error changes nothing for that cluster; the pass still covers every new memory."""
    from agent_harness.workspace.memory.curator import MemoryCurator

    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    store.add(MSFT_LEASES, 'lesson', 'high')
    store.add(MSFT_DEBT, 'fact', 'high')

    report = MemoryCurator(store, ScriptedJudge(ValueError('not json'))).curate()

    assert (report.new, report.reconciled) == (2, 0)
    assert store.count() == 2


def test_prune_forgets_only_old_unused_minor_memories() -> None:
    """Stale means old, rarely recalled and at most medium importance; critical and well-used memories stay."""
    import time

    from agent_harness.workspace.memory.curator import MemoryCurator

    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    now, day = time.time(), 86_400
    stale, _ = store.add(MSFT_LEASES, 'fact', 'medium')
    critical, _ = store.add(NVDA_RESULTS, 'lesson', 'critical')
    recalled, _ = store.add('Paris office rent doubled.', 'fact', 'low')
    young, _ = store.add('The user prefers tables over prose for valuations.', 'preference', 'low')

    set_history(store, stale, created_at=now - 60 * day, last_used_at=now - 60 * day, uses=0)
    set_history(store, critical, created_at=now - 60 * day, last_used_at=now - 60 * day, uses=0)
    set_history(store, recalled, created_at=now - 60 * day, last_used_at=now - 1 * day, uses=8)
    set_history(store, young, created_at=now - 5 * day, last_used_at=now - 5 * day, uses=0)

    report = MemoryCurator(store, unused_judge).curate()

    assert (report.pruned, report.evicted) == (1, 0)
    assert [memory.id for memory in store.get_many([stale, critical, recalled, young])] == [critical, recalled, young]


def test_cap_evicts_the_lowest_value_and_never_a_critical_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over MAX_MEMORIES, the least important, least used go first; critical ones are exempt."""
    from agent_harness.workspace.memory import curator as curator_module

    monkeypatch.setattr(curator_module, 'MAX_MEMORIES', 2)
    store = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder()).memory
    critical, _ = store.add(MSFT_LEASES, 'lesson', 'critical')
    high, _ = store.add(NVDA_RESULTS, 'fact', 'high')
    low, _ = store.add('Paris office rent doubled.', 'fact', 'low')
    medium, _ = store.add('The user prefers tables over prose for valuations.', 'preference', 'medium')

    report = curator_module.MemoryCurator(store, unused_judge).curate()

    assert (report.pruned, report.evicted) == (0, 2)
    assert [memory.id for memory in store.get_many([critical, high, low, medium])] == [critical, high]


def test_curate_memory_runs_a_pass_in_the_background_after_each_run() -> None:
    """With curate_memory, the run returns its answer and a curator pass then judges what it saved."""
    import threading

    workspace = AgentWorkspace('analyst', memory=True, embedder=ConceptEmbedder(), curate_memory=True)
    workspace.memory.add(MSFT_LEASES, 'lesson', 'high')
    judge = ScriptedJudge({'action': 'keep'})
    workspace.curator.judge = judge

    responses = [
        call('Remember', {'content': MSFT_DEBT, 'kind': 'fact', 'importance': 'medium'}, 'save'),
        [{'content': 'Noted.'}],
    ]

    with scripted_agent(responses) as (agent, _, sink):
        workspace.attach(agent)
        answer = agent.run('Remember that MSFT debt matters.', sink=sink)

    for thread in threading.enumerate():
        if thread.name == 'memory-curator':
            thread.join(timeout=5)

    assert answer == 'Noted.'
    assert judge.clusters == [[MSFT_LEASES, MSFT_DEBT]]
    assert workspace.memory.last_curated_at() > 0


def test_curate_memory_without_memory_is_rejected() -> None:
    """Curation works on the memory store, so asking for it without one fails at construction."""
    with pytest.raises(ValueError, match='curate_memory needs memory=True'):
        AgentWorkspace('analyst', curate_memory=True)
