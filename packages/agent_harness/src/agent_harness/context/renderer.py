"""Context renderer: the boundary between stored agent data and the model request.

`Agent` owns the data (`domain_prompt`, `prompt_extensions`, `messages`); the
renderer owns its model-facing presentation; the loop owns execution. Two entry
points, because they run at different times:

- `build_system_message(agent)` — once at init: base prompt + `<domain>` +
  extensions + environment + deferred-tool protocol, with the system cache anchor.
- `render(agent)` — before every model call: an independent copy of history
  with the rolling cache marker applied, then a `<dynamic_context>` block from
  the dynamic context providers. Never written back to `agent.messages`.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from agent_harness.messages import cached_text, system_msg, user_msg

BASE_PROMPT = (Path(__file__).parent / 'system_prompt.md').read_text(encoding='utf-8').strip()

ROLLING_ROLES = ('user', 'assistant', 'tool')

# Zero-arg callable bound to its own state; returns a context block, or None to skip.
DynamicContextProvider = Callable[[], str | None]


class AgentContext(Protocol):
    """The agent state the renderer reads. `Agent` satisfies it structurally."""

    domain_prompt: str | None
    prompt_extensions: list[str]
    deferred_tools: dict[str, dict[str, Any]]
    messages: list[dict]

#     ================================
# --> Helper funcs
#     ================================


def _strip_rolling_markers(messages: list[dict[str, Any]]) -> None:
    """Remove cache_control from non-system parts, keeping every part.

    The system message's anchor is never touched — the loop filters by role.
    """
    for message in messages:
        if message['role'] not in ROLLING_ROLES:
            continue

        content = message.get('content')

        if not isinstance(content, list):
            continue

        for part in content:
            if isinstance(part, dict):
                part.pop('cache_control', None)


def _mark_latest(messages: list[dict[str, Any]]) -> None:
    """Put the rolling cache marker on the latest non-empty non-system text."""
    for message in reversed(messages):
        if message['role'] not in ROLLING_ROLES:
            continue

        content = message.get('content')

        if isinstance(content, str) and content:
            message['content'] = cached_text(content)
            return

        if not isinstance(content, list):
            continue

        for part in reversed(content):
            if isinstance(part, dict) and part.get('type') == 'text' and part.get('text'):
                part['cache_control'] = {'type': 'ephemeral'}
                return


#     ================================
# --> Renderer
#     ================================


class ContextRenderer:
    """Own the model-facing formatting of the agent's context."""

    def __init__(self, dynamic_providers: list[DynamicContextProvider]) -> None:
        # Own copy: the caller's list may be a shared mutable default (Agent's `= []`)
        self.dynamic_providers = list(dynamic_providers)

    def _apply_cache_control(self, messages: list[dict[str, Any]]) -> None:
        _strip_rolling_markers(messages)
        _mark_latest(messages)
    
    def _dynamic_context(self) -> str | None:
        """Join every non-empty provider block into one `<dynamic_context>` block."""
        blocks = [block for provider in self.dynamic_providers if (block := provider())]

        if not blocks:
            return None

        return '<dynamic_context>\n' + '\n\n'.join(blocks) + '\n</dynamic_context>'

    def build_system_message(self, agent: AgentContext) -> dict[str, Any]:
        """Assemble the system message: base prompt, domain, extensions, environment, deferred-tool protocol."""
        parts: list[str] = [BASE_PROMPT]

        # If a domain system prompt is passed, append it under a domain header
        if agent.domain_prompt:
            parts.append('<domain>\n' + agent.domain_prompt.strip() + '\n</domain>')

        # Blocks added in stages via extend_system_prompt (e.g. an org's <organization>)
        parts.extend(block.strip() for block in agent.prompt_extensions)

        environment = (
            '<environment>\n'
            f'- Date: {datetime.now().strftime("%A, %B %d, %Y")}\n'
            '</environment>'
        )

        parts.append(environment)

        # The deferred-tool registry is Python-side state the model can't see —
        # its only in-context signal is the ` [deferred]` description marker.
        # Spell out the protocol here, and only when any deferred tools exist,
        # so agents without them never read about the mechanism.
        if agent.deferred_tools:
            names = ', '.join(sorted(agent.deferred_tools))

            parts.append(
                '<deferred_tools>\n'
                f'Deferred at session start: {names}.\n'
                'A deferred tool ships as a stub: its description ends with the marker '
                '` [deferred]` and its parameter schema is empty. Before calling one, call '
                '`LoadTool(names=[...])` once to fetch its full schema. After loading, the '
                'marker disappears from the tool list and you call the tool directly for the '
                'rest of the session — do not call LoadTool for it again.\n'
                'The tool list is the live source of truth: any tool whose description does '
                'NOT end with ` [deferred]` is already fully loaded. Never call LoadTool on it.\n'
                '</deferred_tools>'
            )

        content = '\n\n'.join(parts)

        return system_msg(content, cache=True)

    def render(self, agent: AgentContext) -> list[dict[str, Any]]:
        """Return an independent request copy of `agent.messages`, cache-formatted.

        Keeps 2 breakpoints (system anchor + rolling tail), under Anthropic's
        limit of 4. Deep-copied so formatting never rewrites the execution record.
        """
        messages = deepcopy(agent.messages)

        # Future context transformations belong here, in explicit order.

        self._apply_cache_control(messages)

        # Appended after the marker so the volatile block stays outside the cached
        # prefix. Last position also never splits an assistant tool_call from its result.
        dynamic = self._dynamic_context()

        if dynamic is not None:
            messages.append(user_msg(dynamic))

        return messages

