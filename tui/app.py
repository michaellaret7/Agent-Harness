"""TUIApp — assembles panels, key bindings, runs the prompt_toolkit Application.

Architecture:
- prompt_toolkit Application runs on the asyncio event loop.
- On Enter, the user's prompt is dispatched to a worker thread via
  `asyncio.to_thread(agent.run, prompt, sink, cancel_event)`.
- The Sink mutates History from the worker; UI repaints via
  `loop.call_soon_threadsafe(app.invalidate)`.
- Esc sets `cancel_event` and closes the in-flight stream.
- Ctrl+C: first press cancels, second press within 2s exits.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import traceback
from typing import TYPE_CHECKING

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.styles import Style

from tui.history import History
from tui.panels import InputPanel, OutputPanel, StatusBar
from tui.sink import TUISink

if TYPE_CHECKING:
    from agent.agent import Agent

CTRL_C_DOUBLE_TAP_SECONDS = 2.0

#     ================================
# --> Style
#     ================================

# No background colors anywhere — terminal theme shows through.
APP_STYLE = Style.from_dict({
    'status': 'fg:ansibrightblack',
    'status.running': 'fg:ansiyellow',
    'status.locked': 'fg:ansibrightblue',
    'status.copy': 'fg:ansigreen bold',
})

#     ================================
# --> App
#     ================================


class TUIApp:
    def __init__(self, agent: 'Agent') -> None:
        self.agent = agent

        self.history = History()
        self.output = OutputPanel(self.history)
        self.input = InputPanel()
        self.status = StatusBar(self._get_status)

        self.cancel_event = threading.Event()
        self.worker_task: asyncio.Task | None = None
        self.last_ctrl_c: float = 0.0
        # Toggle via Ctrl+G. When True, mouse capture is disabled so the
        # terminal's native click-drag selection works for copying.
        self.copy_mode = False

        self.application: Application = self._build_application()

        self.sink = TUISink(history=self.history, app=self.application)

    # ----------------------------------------
    # Layout
    # ----------------------------------------

    def _build_application(self) -> Application:
        root = HSplit([
            self.output.window,
            Window(height=1, char='─', style='class:status'),
            self.input.area,
            self.status.window,
        ])

        kb = self._build_key_bindings()

        return Application(
            layout=Layout(root, focused_element=self.input.area),
            key_bindings=kb,
            style=APP_STYLE,
            full_screen=True,
            # Dynamic: ON by default for wheel scroll; OFF in copy mode so the
            # terminal's native click-drag selection works. Toggled with Ctrl+G.
            # The renderer evaluates this filter every frame and emits the
            # appropriate enable/disable mouse-mode escape codes.
            mouse_support=Condition(lambda: not self.copy_mode),
            # Default would auto-enable in full_screen mode and bind pageup/down
            # to scroll the *focused* window — which is the input. We own
            # pageup/down ourselves to scroll the OutputPanel's slice.
            enable_page_navigation_bindings=False,
        )

    # ----------------------------------------
    # Key bindings
    # ----------------------------------------

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add('enter')
        def _on_enter(event: KeyPressEvent) -> None:
            if self._is_running():
                return  # silently no-op while a turn is in flight

            text = self.input.text.strip()

            if not text:
                return

            self.input.clear()
            self._submit(text)

        @kb.add('c-j')
        def _on_ctrl_j(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text('\n')

        @kb.add('escape', eager=True)
        def _on_escape(event: KeyPressEvent) -> None:
            if self._is_running():
                self.cancel_event.set()
            else:
                self.input.clear()

        @kb.add('c-c')
        def _on_ctrl_c(event: KeyPressEvent) -> None:
            now = time.monotonic()

            if self._is_running():
                self.cancel_event.set()
                self.last_ctrl_c = now
                return

            if now - self.last_ctrl_c < CTRL_C_DOUBLE_TAP_SECONDS:
                event.app.exit()
                return

            self.last_ctrl_c = now

        @kb.add('c-d')
        def _on_ctrl_d(event: KeyPressEvent) -> None:
            if not self.input.text:
                event.app.exit()

        # Alt+arrows are deliberately not bound — `escape, eager=True` above
        # consumes the lead byte and they would never fire anyway.
        @kb.add('pageup')
        @kb.add('c-up')
        def _on_pgup(event: KeyPressEvent) -> None:
            self.output.page_up()
            event.app.invalidate()

        @kb.add('pagedown')
        @kb.add('c-down')
        def _on_pgdn(event: KeyPressEvent) -> None:
            self.output.page_down()
            event.app.invalidate()

        @kb.add('end')
        def _on_end(event: KeyPressEvent) -> None:
            self.output.jump_to_bottom()
            event.app.invalidate()

        @kb.add('c-g')
        def _on_toggle_copy_mode(event: KeyPressEvent) -> None:
            self.copy_mode = not self.copy_mode
            event.app.invalidate()

        @kb.add('tab')
        def _on_tab(event: KeyPressEvent) -> None:
            event.app.layout.focus_next()

        return kb

    # ----------------------------------------
    # Worker dispatch
    # ----------------------------------------

    def _is_running(self) -> bool:
        return self.worker_task is not None and not self.worker_task.done()

    def _submit(self, prompt: str) -> None:
        self.cancel_event.clear()
        self.sink.on_user_message(prompt)

        self.worker_task = self.application.create_background_task(self._run_turn(prompt))

    async def _run_turn(self, prompt: str) -> None:
        try:
            await asyncio.to_thread(self.agent.run, prompt, self.sink, self.cancel_event)

        except Exception as e:
            tb = ''.join(traceback.format_exception(type(e), e, e.__traceback__)[-10:])
            self.sink.on_error(f'{type(e).__name__}: {e}\n{tb}')

        finally:
            self.application.invalidate()

    # ----------------------------------------
    # Status
    # ----------------------------------------

    def _get_status(self) -> dict:
        return {
            'provider': self.agent.provider,
            'model': self.agent.model,
            'cwd': os.getcwd(),
            'running': self._is_running(),
            'scroll_locked': not self.output.follow_tail,
            'scroll_y': self.output._scroll_target,
            'copy_mode': self.copy_mode,
        }

    # ----------------------------------------
    # Resize handling — keep History.width in sync
    # ----------------------------------------

    def _refresh_size(self) -> None:
        size = self.application.output.get_size()
        new_width = max(20, size.columns - 2)

        if new_width != self.history.width:
            self.history.width = new_width
            self.history.rerender_all()

    # ----------------------------------------
    # Run
    # ----------------------------------------

    async def run_async(self) -> None:
        # Periodic resize check — prompt_toolkit fires invalidate on resize,
        # but we need to re-render cells when width changes.
        async def watch_size() -> None:
            while True:
                self._refresh_size()
                await asyncio.sleep(0.25)

        self._refresh_size()

        size_task = asyncio.create_task(watch_size())

        try:
            await self.application.run_async()

        finally:
            size_task.cancel()
