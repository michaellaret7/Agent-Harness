"""Context compaction contracts. Run: uv run --all-packages pytest tests/test_compaction.py"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from openai import OpenAI

from agent_harness import Agent, AgentWorkspace
from agent_harness.client import resolve_context_window
from agent_harness.context.archive import HistoryArchive
from agent_harness.context.compaction import (
    CompactionConfig, Compactor, ContextOverflowError, estimate_tokens, find_cut,
    render_compacted_block, render_transcript, summarize,
)
from agent_harness.messages import assistant_msg, system_msg, tool_msg, user_msg
from agent_harness.sinks.base import CompactionEvent
from agent_harness.sinks.stdout import StdoutSink
from agent_harness.sub_agent import SubAgent
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult
from agent_harness.usage import Usage
from contract_support import RecordingSink, message_text, scripted_agent, tool_call, tool_delta


#     ================================
# --> Helper funcs
#     ================================


def models_client(entries: list[dict[str, Any]]) -> OpenAI:
    """An OpenAI client whose `/models` endpoint returns `entries`, served in memory."""
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith('/models')

        return httpx.Response(200, json={'object': 'list', 'data': entries})

    http_client = httpx.Client(transport=httpx.MockTransport(respond))

    return OpenAI(api_key='test-only', base_url='https://harness.invalid/v1',
                  http_client=http_client, max_retries=0)


def tool_exchange(call_id: str, result: str) -> list[dict[str, Any]]:
    """One assistant tool call and its matching result."""
    return [
        assistant_msg('', [tool_call('add', {'a': 1, 'b': 2}, call_id)]),
        tool_msg(call_id, result),
    ]


def completion_client(replies: list[tuple[int, str]], requests: list[dict[str, Any]]) -> OpenAI:
    """An OpenAI client answering each non-streaming chat call with the next (status, text) reply."""
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        status, text = replies[len(requests) - 1]

        if status != 200:
            return httpx.Response(status, json={'error': {'message': text}})

        return httpx.Response(200, json={
            'id': 'summary', 'object': 'chat.completion', 'created': 0, 'model': 'contract-model',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text}}],
            'usage': {'prompt_tokens': 900, 'completion_tokens': 120, 'total_tokens': 1020},
        })

    http_client = httpx.Client(transport=httpx.MockTransport(respond))

    return OpenAI(api_key='test-only', base_url='https://harness.invalid/v1',
                  http_client=http_client, max_retries=0)


@dataclass
class Target:
    """The agent state compaction reads and rewrites (`CompactionTarget`)."""

    messages: list[dict]
    client: OpenAI | None
    provider: str = 'openrouter'
    model: str | None = 'contract-model'
    plan: list[dict] = field(default_factory=list)
    turn_task: dict | None = None


class CompactionSink(RecordingSink):
    """Also records compaction and usage events."""

    def on_compaction(self, event: CompactionEvent) -> None:
        self.record('compaction', event)

    def on_usage(self, usage: Usage) -> None:
        self.record('usage', usage)


def long_history(exchanges: int = 8, result_chars: int = 800) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A system message, a task, `exchanges` tool round trips, and a closing reply. Returns (history, task)."""
    task = user_msg('Price every ticker in the watchlist and flag outliers.')
    messages = [system_msg('You are a careful analyst.'), task]

    for n in range(exchanges):
        messages += tool_exchange(f'call_{n}', f'quote {n}: ' + 'p' * result_chars)

    messages.append(assistant_msg('Halfway through the watchlist.'))

    return messages, task


def quote_tool() -> Callable:
    """A tool returning a large quote payload, so a few calls fill a small context window."""
    @agent_tool
    def fetch_quote(ticker: str) -> ToolResult:
        """Fetch the full quote record for one ticker."""
        return ToolResult(f'{ticker}: ' + 'q' * 2_000, status='ok')

    return fetch_quote


def tool_pairs_complete(messages: list[dict[str, Any]]) -> bool:
    """True when every tool result follows its call and every call has a result."""
    open_calls: set[str] = set()

    for message in messages:
        if message['role'] == 'tool':
            if message['tool_call_id'] not in open_calls:
                return False

            open_calls.discard(message['tool_call_id'])
            continue

        if open_calls:
            return False

        open_calls = {call['id'] for call in message.get('tool_calls') or []}

    return not open_calls


#     ================================
# --> Context window
#     ================================


def test_context_window_reads_each_providers_field() -> None:
    """OpenRouter's `context_length` and vLLM's `max_model_len` both resolve to the window."""
    openrouter = models_client([
        {'id': 'other/model', 'object': 'model', 'created': 0, 'owned_by': 'x', 'context_length': 8_000},
        {'id': 'anthropic/claude', 'object': 'model', 'created': 0, 'owned_by': 'x', 'context_length': 200_000},
    ])
    vllm = models_client([
        {'id': 'qwen', 'object': 'model', 'created': 0, 'owned_by': 'vllm', 'max_model_len': 32_768},
    ])

    assert resolve_context_window(openrouter, 'openrouter', 'anthropic/claude') == 200_000
    assert resolve_context_window(vllm, 'vllm', 'qwen') == 32_768


def test_context_window_unknown_model_raises() -> None:
    """A model the provider does not list, or lists without a window, fails fast."""
    client = models_client([
        {'id': 'listed', 'object': 'model', 'created': 0, 'owned_by': 'x'},
    ])

    with pytest.raises(RuntimeError, match='context_window='):
        resolve_context_window(client, 'openrouter', 'missing')

    with pytest.raises(RuntimeError, match='context_window='):
        resolve_context_window(client, 'openrouter', 'listed')


#     ================================
# --> Config and cut point
#     ================================


def test_config_rejects_inverted_fractions() -> None:
    """keep_recent must sit below threshold, and both inside (0, 1)."""
    CompactionConfig()

    bad_configs: list[dict[str, Any]] = [{'keep_recent': 0.9}, {'threshold': 1.0}, {'keep_recent': 0.0}, {'context_window': 0}]

    for bad in bad_configs:
        with pytest.raises(ValueError):
            CompactionConfig(**bad)


def test_cut_never_opens_the_tail_on_a_tool_result() -> None:
    """For every budget, the tail starts on a non-tool message and every tail result has its call."""
    messages = [system_msg('system'), user_msg('task')]

    for n in range(6):
        messages += tool_exchange(f'call_{n}', 'x' * 200 * (n + 1))

    messages.append(assistant_msg('done'))

    for budget in range(0, estimate_tokens(messages) + 100, 25):
        cut = find_cut(messages, budget)
        tail = messages[cut:]
        call_ids = {tc['id'] for m in tail for tc in m.get('tool_calls', [])}

        assert 1 <= cut <= len(messages)
        assert not tail or tail[0]['role'] != 'tool'
        assert all(m['tool_call_id'] in call_ids for m in tail if m['role'] == 'tool')


def test_cut_respects_the_budget_and_spares_the_system_message() -> None:
    """The tail fits its budget; an unlimited budget keeps everything after the system message."""
    messages = [system_msg('system'), user_msg('task'), *tool_exchange('a', 'y' * 400), assistant_msg('ok')]

    assert find_cut(messages, 10**9) == 1
    assert find_cut(messages, 0) == len(messages)

    budget = estimate_tokens(messages[-1:])

    assert find_cut(messages, budget) == len(messages) - 1


#     ================================
# --> Archive
#     ================================


def test_archive_segments_round_trip_and_number_in_order(tmp_path: Path) -> None:
    """Each write is a new numbered segment whose lines load back to the original messages."""
    archive = HistoryArchive(tmp_path / 'history')
    first = [user_msg('task'), *tool_exchange('a', 'résultat ✓')]
    second = [assistant_msg('more'), user_msg('next')]

    first_path = archive.write(first)
    second_path = archive.write(second)

    assert first_path.startswith('history/') and first_path.endswith('/segment_001.jsonl')
    assert second_path.endswith('/segment_002.jsonl')
    assert Path(first_path).parent == Path(second_path).parent

    for relative, original in ((first_path, first), (second_path, second)):
        lines = (tmp_path / relative).read_text(encoding='utf-8').splitlines()

        assert [json.loads(line) for line in lines] == original


def test_archive_sessions_never_share_a_folder(tmp_path: Path) -> None:
    """Two archives on the same root, created back to back, write to different sessions."""
    one = HistoryArchive(tmp_path / 'history').write([user_msg('a')])
    two = HistoryArchive(tmp_path / 'history').write([user_msg('b')])

    assert Path(one).parent != Path(two).parent


#     ================================
# --> Sink event
#     ================================


def test_stdout_reports_compaction(capsys: pytest.CaptureFixture[str]) -> None:
    """The headless sink prints one line with the token drop and whether the summary worked."""
    StdoutSink().on_compaction(CompactionEvent(9_000, 2_500, 40, 'history/s/segment_001.jsonl', True))
    StdoutSink().on_compaction(CompactionEvent(9_000, 2_600, 40, None, False))

    lines = capsys.readouterr().out.splitlines()

    assert '[compaction]' in lines[0] and '9,000' in lines[0] and '40 messages' in lines[0]
    assert 'summary failed' in lines[1]


#     ================================
# --> Summarizer
#     ================================


def test_transcript_numbers_messages_and_clips_tool_output() -> None:
    """`#n` follows archive line order, tool calls show, and long tool results are clipped."""
    span = [user_msg('task'), *tool_exchange('a', 'z' * 5_000), assistant_msg('done')]

    transcript = render_transcript(span)

    assert transcript.index('[#1 user] task') < transcript.index('[#2 assistant]') < transcript.index('[#3 tool]')
    assert '-> add({"a": 1, "b": 2})' in transcript
    assert 'z' * 5_000 not in transcript and 'more chars in archive' in transcript
    assert '[#4 assistant] done' in transcript


def test_summarize_returns_text_and_reports_usage() -> None:
    """A good reply comes back stripped; its usage reaches the sink; the request carries no tools."""
    requests: list[dict[str, Any]] = []
    sink = CompactionSink()
    client = completion_client([(200, '  ## Goal\nPrice the watchlist.  ')], requests)

    text = summarize(client, 'contract-model', [user_msg('task')], 500, sink)

    assert text == '## Goal\nPrice the watchlist.'
    assert sink.values('usage')[0].prompt_tokens == 900
    assert requests[0]['max_tokens'] == 500
    assert 'tools' not in requests[0] and requests[0].get('stream') in (None, False)
    assert '[#1 user] task' in requests[0]['messages'][1]['content']


def test_summarize_returns_none_when_the_provider_fails() -> None:
    """A provider error is absorbed: compaction proceeds on the archive alone."""
    client = completion_client([(500, 'upstream down')], [])

    assert summarize(client, 'contract-model', [user_msg('task')], 500, CompactionSink()) is None


def test_compacted_block_points_at_the_archive_and_carries_the_plan() -> None:
    """The block names the segment, holds the summary, and renders the plan; failures say so."""
    plan = [{'text': 'Fetch quotes', 'status': 'completed'}, {'text': 'Flag outliers', 'status': 'in_progress'}]

    block = render_compacted_block('## Goal\nX', plan, 'history/s/segment_002.jsonl', 12)
    bare = render_compacted_block(None, [], None, 12)

    assert block.startswith('<compacted_history archived_messages="12" archive="history/s/segment_002.jsonl">')
    assert '## Goal\nX' in block and '[x] Fetch quotes' in block and '[-] Flag outliers' in block
    assert 'in history/s/, numbered oldest first' in block
    assert 'Summary unavailable' in bare and '<plan>' not in bare and 'No archive was kept' in bare


#     ================================
# --> Compactor
#     ================================


def test_should_compact_uses_provider_count_plus_new_messages() -> None:
    """Below the threshold nothing happens; tool output appended after the last call tips it over."""
    messages, _ = long_history(exchanges=1)
    compactor = Compactor(CompactionConfig(context_window=10_000))
    target = Target(messages, client=None)

    compactor.observe(Usage(prompt_tokens=7_000, completion_tokens=500), messages)

    assert not compactor.should_compact(target)

    messages.append(tool_msg('late', 'r' * 4_000))  # about 1,000 tokens

    assert compactor.should_compact(target)


def test_compact_rewrites_history_archives_the_span_and_pins_the_task(tmp_path: Path) -> None:
    """[system, task, block, tail]; the tail is tool-safe; the archive holds exactly the removed span."""
    messages, task = long_history()
    original = list(messages)
    sink = CompactionSink()
    target = Target(messages, completion_client([(200, '## Goal\nPrice the watchlist.')], []),
                    plan=[{'text': 'Fetch quotes', 'status': 'in_progress'}], turn_task=task)
    window = estimate_tokens(messages) + 500
    compactor = Compactor(CompactionConfig(context_window=window, archive_dir=tmp_path / 'history'))

    compactor.compact(target, sink)

    event = sink.values('compaction')[0]
    block = messages[2]['content']
    tail = messages[3:]

    assert messages[0] is original[0] and messages[1] is task
    assert block.startswith('<compacted_history') and 'Price the watchlist.' in block and 'Fetch quotes' in block
    assert tail == original[len(original) - len(tail):] and tail[0]['role'] != 'tool'
    assert event.summarized and event.archived_messages == len(original) - 1 - len(tail)
    assert event.tokens_after < event.tokens_before < window
    assert event.segment is not None and event.segment in block

    archived = [json.loads(line) for line in (tmp_path / event.segment).read_text(encoding='utf-8').splitlines()]

    assert archived == original[1:len(original) - len(tail)]


def test_second_compaction_rolls_the_previous_summary_forward(tmp_path: Path) -> None:
    """The first summary is inside the second span, so the summarizer sees it and it is archived."""
    messages, task = long_history()
    requests: list[dict[str, Any]] = []
    client = completion_client([(200, 'FIRST SUMMARY'), (200, 'SECOND SUMMARY')], requests)
    target = Target(messages, client, turn_task=task)
    compactor = Compactor(CompactionConfig(context_window=estimate_tokens(messages) + 500,
                                           archive_dir=tmp_path / 'history'))
    sink = CompactionSink()

    compactor.compact(target, sink)

    for n in range(8, 14):
        messages += tool_exchange(f'call_{n}', f'quote {n}: ' + 'q' * 800)

    compactor.compact(target, sink)

    second = sink.values('compaction')[1]

    assert 'FIRST SUMMARY' in requests[1]['messages'][1]['content']
    assert 'SECOND SUMMARY' in messages[2]['content'] and 'FIRST SUMMARY' not in messages[2]['content']
    assert messages[1] is task
    assert second.segment is not None and second.segment.endswith('segment_002.jsonl')
    assert 'FIRST SUMMARY' in (tmp_path / second.segment).read_text(encoding='utf-8')


def test_failed_summary_still_archives_and_flags_the_event(tmp_path: Path) -> None:
    """A provider failure leaves an archive pointer and `summarized=False`, and the run can go on."""
    messages, task = long_history()
    sink = CompactionSink()
    target = Target(messages, completion_client([(500, 'down')], []), turn_task=task)
    compactor = Compactor(CompactionConfig(context_window=estimate_tokens(messages) + 500,
                                           archive_dir=tmp_path / 'history'))

    compactor.compact(target, sink)

    event = sink.values('compaction')[0]

    assert not event.summarized and event.segment is not None
    assert 'Summary unavailable' in messages[2]['content'] and event.segment in messages[2]['content']
    assert (tmp_path / event.segment).exists()


def test_oversized_tool_result_is_archived_with_its_call() -> None:
    """A final tool result bigger than the tail budget leaves an empty tail, never a split pair."""
    messages, task = long_history(exchanges=1)
    messages += tool_exchange('huge', 'h' * 40_000)
    target = Target(messages, completion_client([(200, 'summary')], []), turn_task=task)

    Compactor(CompactionConfig(context_window=20_000)).compact(target, CompactionSink())

    assert [m['role'] for m in messages] == ['system', 'user', 'user']
    assert messages[1] is task


def test_oversized_pinned_task_raises_overflow() -> None:
    """What cannot be archived (the pinned task) still over the threshold fails fast instead of looping."""
    messages, _ = long_history(exchanges=2)
    task = user_msg('Analyze this filing: ' + 'f' * 40_000)
    messages.insert(2, task)
    target = Target(messages, completion_client([(200, 'summary')], []), turn_task=task)

    with pytest.raises(ContextOverflowError):
        Compactor(CompactionConfig(context_window=8_000)).compact(target, CompactionSink())


#     ================================
# --> Wired into the agent
#     ================================


def test_long_run_compacts_and_every_request_stays_valid(tmp_path: Path) -> None:
    """A real Agent fetching ten large quotes compacts more than once, never sends a broken
    history, keeps the task pinned, rolls summaries forward, and loses no tool result."""
    tools: list[dict[str, Any] | Callable] = [quote_tool()]
    base = estimate_tokens(Agent(model='contract-model', tools=tools).messages)
    config = CompactionConfig(context_window=int((base + 2_500) / 0.8), archive_dir=tmp_path / 'history')
    tickers = [f'T{n}' for n in range(10)]
    responses: list[list[Any]] = [[tool_delta(tool_call('fetch_quote', {'ticker': t}, f'call_{t}'))] for t in tickers]
    responses.append([{'content': 'All quotes fetched.'}])
    summaries = [f'SUMMARY {n}' for n in range(1, 11)]

    with scripted_agent(responses, tools=tools, compaction=config, summaries=summaries) as (agent, provider, sink):
        result = agent.run('Fetch every quote in the watchlist.', sink=sink)

    events = sink.values('compaction')
    compacted = [r for r in provider.requests
                 if len(r['messages']) > 2 and message_text(r['messages'][2]).startswith('<compacted_history')]

    assert result == 'All quotes fetched.'
    assert len(events) >= 2 and all(e.summarized for e in events)
    assert len(provider.requests) == len(responses)

    for request in provider.requests:
        assert request['messages'][0]['role'] == 'system'
        assert tool_pairs_complete(request['messages'])

    for request in compacted:
        assert message_text(request['messages'][1]) == 'Fetch every quote in the watchlist.'

    assert 'SUMMARY 1' in provider.summary_requests[1]['messages'][1]['content']

    archived = [
        json.loads(line)
        for event in events if event.segment is not None
        for line in (tmp_path / event.segment).read_text(encoding='utf-8').splitlines()
    ]
    seen = {message_text(m).split(':')[0] for m in [*archived, *agent.messages] if m['role'] == 'tool'}

    assert seen == set(tickers)


def test_workspace_puts_the_archive_in_the_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Attaching a workspace archives under sandbox/history, unless the config named its own folder."""
    monkeypatch.setenv('AGENT_HARNESS_HOME', str(tmp_path))
    workspace = AgentWorkspace('analyst')

    default = Agent(model='contract-model', compaction=CompactionConfig(context_window=10_000))
    explicit = Agent(model='contract-model', compaction=CompactionConfig(context_window=10_000,
                                                                         archive_dir=tmp_path / 'mine'))
    plain = Agent(model='contract-model')

    workspace.attach(default)
    AgentWorkspace('analyst').attach(explicit)
    AgentWorkspace('analyst').attach(plain)

    assert default.compactor is not None and default.compactor.archive is not None
    assert default.compactor.archive.root == workspace.sandbox_dir / 'history'
    assert explicit.compactor is not None and explicit.compactor.archive is not None
    assert explicit.compactor.archive.root == tmp_path / 'mine'
    assert plain.compactor is None


def test_subagents_refuse_compaction() -> None:
    """Compaction belongs to the long-lived parent, not to a short tool-invoked sub-run."""
    with pytest.raises(NotImplementedError, match='compaction'):
        SubAgent(model='contract-model', compaction=CompactionConfig())
