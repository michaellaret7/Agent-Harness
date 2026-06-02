"""Key-binding factory for TUIApp.

Lifted out of `app.py` so the orchestrator stays focused on layout and
lifecycle. Every binding here is bound against `app` state — input/output
panels, the cancel event, the running-task flag — so the factory takes the
TUIApp as its sole argument.

Tab is intentionally unbound at the app level so it stays as in-buffer
text insertion in the input. A previous binding cycled focus via
`focus_next()`, which (combined with the output panel being focusable)
routinely moved focus to the output and made the TUI look frozen — keys
typed there go nowhere visible.

Alt+arrows are also unbound — `escape, eager=True` below consumes the
lead byte and they would never fire anyway.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent

if TYPE_CHECKING:
    from tui.app import TUIApp

CTRL_C_DOUBLE_TAP_SECONDS = 2.0


def build_key_bindings(app: 'TUIApp') -> KeyBindings:
    kb = KeyBindings()

    @kb.add('enter')
    def _on_enter(event: KeyPressEvent) -> None:
        if app._is_running():
            return  # silently no-op while a turn is in flight

        text = app.input.text.strip()

        if not text:
            return

        app.input.clear()
        app._submit(text)

    @kb.add('c-j')
    def _on_ctrl_j(event: KeyPressEvent) -> None:
        event.current_buffer.insert_text('\n')

    @kb.add('escape', eager=True)
    def _on_escape(event: KeyPressEvent) -> None:
        if app._is_running():
            app.cancel_event.set()
        else:
            app.input.clear()

    @kb.add('c-c')
    def _on_ctrl_c(event: KeyPressEvent) -> None:
        now = time.monotonic()

        if app._is_running():
            app.cancel_event.set()
            app.last_ctrl_c = now
            return

        if now - app.last_ctrl_c < CTRL_C_DOUBLE_TAP_SECONDS:
            event.app.exit()
            return

        app.last_ctrl_c = now

    @kb.add('c-d')
    def _on_ctrl_d(event: KeyPressEvent) -> None:
        if not app.input.text:
            event.app.exit()

    @kb.add('pageup')
    @kb.add('c-up')
    def _on_pgup(event: KeyPressEvent) -> None:
        app.output.page_up()
        event.app.invalidate()

    @kb.add('pagedown')
    @kb.add('c-down')
    def _on_pgdn(event: KeyPressEvent) -> None:
        app.output.page_down()
        event.app.invalidate()

    @kb.add('end')
    def _on_end(event: KeyPressEvent) -> None:
        app.output.jump_to_bottom()
        event.app.invalidate()

    @kb.add('c-t')
    def _on_toggle_copy_mode(event: KeyPressEvent) -> None:
        app.copy_mode = not app.copy_mode
        event.app.invalidate()

    return kb
