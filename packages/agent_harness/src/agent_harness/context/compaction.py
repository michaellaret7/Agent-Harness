"""Context compaction: keep a long-running agent's history under the model's context window.

When the live history nears `threshold × context_window`, the older span is archived
to disk, replaced by a rolling summary, and the recent tail is kept word for word:

    [system, task, ...old span..., ...tail...]  ->  [system, task, <compacted_history>, ...tail...]

The previous `<compacted_history>` sits inside the next old span, so each summary is
built from the one before it. Full design: `docs/agents/context_compaction.md`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openai import APIError, OpenAI

from agent_harness.base_tools.plan import render_plan
from agent_harness.client import resolve_context_window
from agent_harness.context.archive import HistoryArchive
from agent_harness.messages import system_msg, user_msg
from agent_harness.sinks.base import CompactionEvent, Sink
from agent_harness.usage import Usage

log = logging.getLogger(__name__)

SUMMARY_PROMPT = (Path(__file__).parent / 'compaction_prompt.md').read_text(encoding='utf-8').strip()

TOOL_RESULT_CLIP = 2_000  # chars of each tool result shown to the summarizer; the archive keeps all
TOOL_ARGS_CLIP = 300      # chars of each tool call's arguments shown to the summarizer


class ContextOverflowError(RuntimeError):
    """The request is still over the threshold after compaction: what cannot be archived is too big."""


class CompactionTarget(Protocol):
    """The agent state compaction reads and rewrites. `Agent` satisfies it structurally."""

    messages: list[dict]
    tools: list[dict[str, Any]]
    client: OpenAI | None
    provider: str
    model: str | None
    plan: list[dict]
    turn_task: dict | None


@dataclass(frozen=True)
class CompactionConfig:
    """When and how much to compact. Fractions are of the model's context window.

    Args:
        threshold: Compact once the estimated prompt reaches this fraction of the window.
        keep_recent: Token budget, as a fraction of the window, for the verbatim tail.
        context_window: The model's max context in tokens; None asks the provider once.
        summary_max_tokens: Output cap for the summarizer call.
        archive_dir: Where archived segments go; None keeps no archive.
            `AgentWorkspace.attach` points it at the sandbox.
    """

    threshold: float = 0.80
    keep_recent: float = 0.25
    context_window: int | None = None
    summary_max_tokens: int = 4_000
    archive_dir: Path | None = None

    def __post_init__(self) -> None:
        if not 0 < self.keep_recent < self.threshold < 1:
            raise ValueError(
                f'CompactionConfig: need 0 < keep_recent < threshold < 1, '
                f'got keep_recent={self.keep_recent}, threshold={self.threshold}'
            )

        if self.context_window is not None and self.context_window <= 0:
            raise ValueError(f'CompactionConfig: context_window must be positive, got {self.context_window}')


#     ================================
# --> Helper funcs
#     ================================


def _clip(text: str, limit: int) -> str:
    """Cut `text` to `limit` chars, saying how much was dropped."""
    if len(text) <= limit:
        return text

    return f'{text[:limit]}… [{len(text) - limit:,} more chars in archive]'


def _content_text(message: dict[str, Any]) -> str:
    """A message's text, whether content is a string, a list of parts, or None."""
    content = message.get('content')

    if isinstance(content, list):
        return ''.join(part.get('text', '') for part in content if isinstance(part, dict))

    return content or ''


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token count: serialized JSON characters / 4.

    Only used where the provider's exact count is unavailable: messages appended
    since the last model call, and sizing the verbatim tail.
    """
    return sum(len(json.dumps(message, ensure_ascii=False)) for message in messages) // 4


def find_cut(messages: list[dict[str, Any]], tail_budget: int) -> int:
    """Return the index where the verbatim tail starts.

    Walks backwards from the end, keeping messages while they fit in
    `tail_budget` tokens, then moves forward past any `tool` messages so the tail
    never opens mid tool-exchange (a tool result without its assistant call is
    rejected by the API). Never returns 0: the system message is not part of
    either side. A result of `len(messages)` means an empty tail.
    """
    cut = len(messages)
    tail_tokens = 0

    while cut > 1:
        size = estimate_tokens([messages[cut - 1]])

        if tail_tokens + size > tail_budget:
            break

        cut -= 1
        tail_tokens += size

    while cut < len(messages) and messages[cut]['role'] == 'tool':
        cut += 1

    return cut


def render_transcript(span: list[dict[str, Any]]) -> str:
    """Render archived messages as plain text for the summarizer.

    `#n` matches the message's line number in its archive segment, so the summary
    can cite `(archive #n)`. Tool results and arguments are clipped; the archive
    keeps them in full.
    """
    blocks: list[str] = []

    for n, message in enumerate(span, start=1):
        text = _content_text(message)

        if message['role'] == 'tool':
            text = _clip(text, TOOL_RESULT_CLIP)

        lines = [f'[#{n} {message["role"]}] {text}'.rstrip()]

        for call in message.get('tool_calls') or []:
            function = call['function']

            lines.append(f'  -> {function["name"]}({_clip(function["arguments"], TOOL_ARGS_CLIP)})')

        blocks.append('\n'.join(lines))

    return '\n\n'.join(blocks)


def summarize(client: OpenAI, model: str, span: list[dict[str, Any]], max_tokens: int, sink: Sink) -> str | None:
    """One non-streaming, tool-less call that summarizes `span`. None if the provider fails.

    The span goes in as a text transcript, not as chat messages: that sidesteps
    tool-pairing rules and keeps the model from continuing the conversation.
    Only provider errors are caught; anything else is a bug and propagates.
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[system_msg(SUMMARY_PROMPT), user_msg(render_transcript(span))],
            max_tokens=max_tokens,
        )

    except APIError as e:
        log.warning('compaction: summary call failed, keeping an archive pointer only: %s', e)

        return None

    if response.usage is not None:
        sink.on_usage(Usage.from_response(response.usage))  # the summary's cost stays visible

    text = response.choices[0].message.content if response.choices else None

    return text.strip() if text and text.strip() else None


def render_compacted_block(summary: str | None, plan: list[dict], segment: str | None, archived: int) -> str:
    """The user message that replaces the archived span in history."""
    archive_attr = f' archive="{segment}"' if segment else ''
    lines = [f'<compacted_history archived_messages="{archived}"{archive_attr}>']

    if segment:
        folder = segment.rsplit('/', 1)[0]

        lines.append(
            'Earlier messages in this conversation were compacted to save context. The messages this '
            f'summary covers are in {segment}: JSON Lines, one chat message per line (line n = #n in '
            f'the summary). Every earlier compaction left its own segment in {folder}/, numbered oldest '
            'first. Read or search them with ExecuteCode when you need exact details the summary does not hold.'
        )

    else:
        lines.append('Earlier messages in this conversation were compacted to save context. No archive was kept.')

    lines.append(f'<summary>\n{summary or "Summary unavailable: the summarizer call failed."}\n</summary>')

    if plan:
        lines.append(f'<plan>\n{render_plan(plan)}\n</plan>')

    lines.append('</compacted_history>')

    return '\n\n'.join(lines)


#     ================================
# --> Compactor
#     ================================


class Compactor:
    """Decides when to compact an agent's history, and does it.

    Token accounting: after each model call, `_counted_tokens` is the provider's exact
    count for `messages[:_counted_len]`; messages appended since are estimated. The
    chars/4 estimate drifts by content (JSON numbers cost more tokens per char than
    prose), so `_ratio` (provider tokens / estimated tokens of the last request, tool
    schemas included) scales every estimate. When nothing exact applies (no usage
    yet, or right after a compaction) the whole request is estimated: tools + messages.
    """

    def __init__(self, config: CompactionConfig) -> None:
        self.config = config
        self.window: int | None = config.context_window
        self.archive = HistoryArchive(config.archive_dir) if config.archive_dir is not None else None

        self._counted_tokens = 0
        self._counted_len = 0
        self._ratio = 1.0

    def attach_archive(self, root: Path) -> None:
        """Archive into `root` unless the config already named a directory. Used by `AgentWorkspace.attach`."""
        if self.archive is None:
            self.archive = HistoryArchive(root)

    def observe(self, usage: Usage | None, agent: CompactionTarget) -> None:
        """Record the provider's count right after the assistant message is appended."""
        if usage is None:
            self._counted_len = 0
            return

        # The request covered the tool schemas and every message but the reply just appended
        sent = estimate_tokens(agent.tools) + estimate_tokens(agent.messages[:-1])

        if sent > 0:
            self._ratio = usage.prompt_tokens / sent

        self._counted_tokens = usage.prompt_tokens + usage.completion_tokens
        self._counted_len = len(agent.messages)

    def current_tokens(self, agent: CompactionTarget) -> int:
        """Estimated size of the next request."""
        if self._counted_len == 0:
            return int(self._ratio * (estimate_tokens(agent.tools) + estimate_tokens(agent.messages)))

        return self._counted_tokens + int(self._ratio * estimate_tokens(agent.messages[self._counted_len:]))

    def should_compact(self, agent: CompactionTarget) -> bool:
        """True once the next request would reach the threshold. Resolves the window on first use."""
        if self.window is None:
            assert agent.client is not None and agent.model is not None  # set by Agent.run

            self.window = resolve_context_window(agent.client, agent.provider, agent.model)

        return self.current_tokens(agent) >= self.config.threshold * self.window

    def compact(self, agent: CompactionTarget, sink: Sink) -> None:
        """Archive the old span, summarize it, and rewrite `agent.messages` in place."""
        assert self.window is not None and agent.client is not None and agent.model is not None

        messages = agent.messages
        before = self.current_tokens(agent)
        cut = find_cut(messages, int(self.config.keep_recent * self.window / self._ratio))

        if cut <= 1:
            raise ContextOverflowError(f'nothing to compact: the whole history fits the tail but the request is ~{before:,} tokens')

        span = messages[1:cut]
        segment = self.archive.write(span) if self.archive is not None else None
        summary = summarize(agent.client, agent.model, span, self.config.summary_max_tokens, sink)
        block = render_compacted_block(summary, agent.plan, segment, len(span))

        # Pin the turn's task word for word when the cut swallowed it
        pinned = [agent.turn_task] if agent.turn_task is not None and any(m is agent.turn_task for m in span) else []

        messages[:] = [messages[0], *pinned, user_msg(block), *messages[cut:]]

        self._counted_len = 0
        after = self.current_tokens(agent)

        sink.on_compaction(CompactionEvent(before, after, len(span), segment, summary is not None))

        # What compaction cannot shrink: the tool schemas, the system message, and the pinned task
        if after >= self.config.threshold * self.window:
            fixed = int(self._ratio * estimate_tokens(agent.tools))
            head = int(self._ratio * estimate_tokens(messages[:len(pinned) + 2]))

            raise ContextOverflowError(
                f'request is ~{after:,} tokens after compaction, over {self.config.threshold:.0%} of '
                f'{self.window:,}: ~{fixed:,} is tool schemas, ~{head:,} the system message, pinned task '
                'and summary. Raise context_window or shrink what is always sent.'
            )
