"""Context compaction contracts. Run: uv run --all-packages pytest tests/test_compaction.py"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import OpenAI

from agent_harness.client import resolve_context_window
from agent_harness.context.archive import HistoryArchive
from agent_harness.context.compaction import CompactionConfig, estimate_tokens, find_cut
from agent_harness.messages import assistant_msg, system_msg, tool_msg, user_msg
from contract_support import tool_call


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

    for bad in ({'keep_recent': 0.9}, {'threshold': 1.0}, {'keep_recent': 0.0}, {'context_window': 0}):
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
