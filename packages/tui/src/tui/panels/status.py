"""StatusBar — single dim line under the input frame.

Pulls live state via a callback so it can repaint without owning any
mutable state itself. Usage segments (`X ctx · Y out · $Z turn · $Z
session`) hide when there's nothing to report — a fresh launch shows
just provider/model · cwd until the first LLM call lands.
"""
from __future__ import annotations

from typing import Callable

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from agent_harness.usage import Usage
from tui.sprites import frame_at

#     ================================
# --> Helper funcs
#     ================================


def _format_tokens(n: int) -> str:
    """Compact token count: 1234 -> '1.2k', 421 -> '421'."""
    if n < 1000:
        return str(n)

    return f'{n / 1000:.1f}k'


def _format_usage_segment(
    last_call: Usage | None,
    last_turn: Usage | None,
    session: Usage | None,
) -> str:
    """Build the dim usage segment for the status bar.

    Returns '' when no LLM call has completed yet, so the bar stays clean
    on a fresh launch. The `$ turn` / `$ session` parts hide themselves
    when cost is 0 (vLLM endpoints don't return cost — better to omit
    than to lie with '$0.00').
    """
    if last_call is None:
        return ''

    turn = last_turn if last_turn is not None else Usage.zero()
    session_total = session if session is not None else Usage.zero()

    # `ctx` is the prompt size on the *last* call (current context occupancy);
    # `cached` shows prompt-cache hits when the upstream model supports caching
    # (Anthropic/Gemini/DeepSeek via OpenRouter); hidden at 0 to stay clean on
    # models that don't cache. `out` is the completion total *across the turn* —
    # completion tokens sum cleanly across the tool-call loop, prompt tokens
    # don't. `out` is shown unconditionally once a call has landed (0 is a
    # legitimate state for a tool-call-only response and worth surfacing).
    # `$` segments hide at 0 because vLLM doesn't supply cost and "$0.00"
    # would be a lie.
    parts: list[str] = [
        f'{_format_tokens(last_call.prompt_tokens)} ctx',
    ]

    if last_call.cached_tokens > 0:
        pct = last_call.cached_tokens / last_call.prompt_tokens * 100
        parts.append(f'{_format_tokens(last_call.cached_tokens)} cached ({pct:.0f}%)')

    parts.append(f'{_format_tokens(turn.completion_tokens)} out')

    if turn.cost > 0:
        parts.append(f'${turn.cost:.4f} turn')

    if session_total.cost > 0:
        parts.append(f'${session_total.cost:.4f} session')

    return '  ·  ' + '  ·  '.join(parts)

#     ================================
# --> Status bar
#     ================================


class StatusBar:
    """Single-line dim status: provider/model · cwd · iter · keybinds."""

    def __init__(self, get_status: Callable[[], dict]) -> None:
        self.get_status = get_status

        self.control = FormattedTextControl(text=self._get_text)

        self.window = Window(
            content=self.control,
            height=1,
        )

    def _get_text(self) -> FormattedText:
        s = self.get_status()
        provider = s.get('provider', '?')
        model = s.get('model', '?')
        cwd = s.get('cwd', '?')
        running = s.get('running', False)
        scroll_locked = s.get('scroll_locked', False)
        scroll_y = s.get('scroll_y', 0)
        copy_mode = s.get('copy_mode', False)

        left_segments: list[tuple[str, str]] = []

        if running:
            sprite = s.get('sprite')
            elapsed = s.get('sprite_elapsed', 0.0)

            if sprite is not None:
                frame = frame_at(sprite, elapsed)
                left_segments.append(('class:status.running', f' running {frame}  ·  '))
            else:
                left_segments.append(('class:status.running', ' running…  ·  '))
        else:
            left_segments.append(('class:status', ' '))

        left_segments.append(('class:status', f'{provider}/{model}  ·  {cwd}'))

        usage_text = _format_usage_segment(
            s.get('last_call_usage'),
            s.get('last_turn_usage'),
            s.get('session_usage'),
        )

        if usage_text:
            left_segments.append(('class:status', usage_text))

        if scroll_locked:
            left_segments.append(('class:status.locked', f'  ·  [scrolled y={scroll_y}]'))

        if copy_mode:
            left_segments.append(('class:status.copy', '  ·  [COPY MODE — drag to select, Ctrl+T to exit]'))
            right = '  Enter send · End jump · Ctrl+T exit copy · Ctrl+C exit '
        else:
            right = '  Enter send · click tool to expand · Ctrl+T copy mode · Esc cancel · Ctrl+C exit '

        return FormattedText(left_segments + [('class:status', '   '), ('class:status', right)])
