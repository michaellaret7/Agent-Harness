"""TUIApp — assembles panels, key bindings, runs the prompt_toolkit Application.

Architecture:
- prompt_toolkit Application runs on the asyncio event loop.
- On Enter, the user's task is dispatched to a worker thread via
  `asyncio.to_thread(agent.run, task, sink, cancel_event)`.
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
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.styles import Style

from agent_harness.sinks import Sink
from tui import sprites
from tui.history import History
from tui.keybindings import build_key_bindings
from tui.panels import InputPanel, OutputPanel, StatusBar
from tui.sink import TUISink
from tui.sprites import Sprite

if TYPE_CHECKING:
    from agent_harness.agent import Agent

ANIM_TICK_S = 1 / 12  # 12 Hz repaint while a turn is in flight

#     ================================
# --> Style
#     ================================

# No background colors anywhere — terminal theme shows through.
APP_STYLE = Style.from_dict({
    'status': 'fg:ansibrightblack',
    'status.running': 'fg:ansiyellow',
    'status.locked': 'fg:ansibrightblue',
    'status.copy': 'fg:ansigreen bold',
    'frame.border': 'fg:ansibrightblack',
    'input.placeholder': 'fg:ansibrightblack italic',
})

#     ================================
# --> App
#     ================================


class TUIApp:
    def __init__(self, agent: 'Agent') -> None:
        self.agent = agent

        self.history = History()
        self.history.append_header(
            provider=agent.provider,
            model=agent.model,
            cwd=os.getcwd(),
            tools=tuple(agent.tool_functions.keys()),
        )

        self.output = OutputPanel(self.history)
        self.input = InputPanel()
        self.status = StatusBar(self._get_status)

        self.cancel_event = threading.Event()
        self.worker_task: asyncio.Task | None = None
        self.last_ctrl_c: float = 0.0
        # Toggle via Ctrl+G. When True, mouse capture is disabled so the
        # terminal's native click-drag selection works for copying.
        self.copy_mode = False

        # Sprite for the running indicator. Picked at random per turn.
        self.active_sprite: Sprite | None = None
        self.sprite_started_at: float = 0.0

        self.application: Application = self._build_application()

        # `tui_sink` is the concrete TUISink used for accumulator reads
        # in `_get_status`. Ambient observability sinks (Langfuse, etc.)
        # are composed in by `Agent.run` via `compose_sinks`, so the
        # TUI does not need to construct them here.
        self.tui_sink: TUISink = TUISink(history=self.history, app=self.application)
        self.sink: Sink = self.tui_sink

    # ----------------------------------------
    # Layout
    # ----------------------------------------

    def _build_application(self) -> Application:
        root = HSplit([
            self.output.window,
            self.input.container,
            self.status.window,
        ])

        kb = build_key_bindings(self)

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
            # Cap render rate at 60 Hz. Smooth-scroll mice fire 60-120 wheel
            # events/sec; without this floor each one races through a full
            # re-render. 60 Hz is the smallest interval the eye can resolve
            # and gives the renderer headroom to coalesce burst events.
            min_redraw_interval=1 / 60,
        )

    # ----------------------------------------
    # Worker dispatch
    # ----------------------------------------

    def _is_running(self) -> bool:
        return self.worker_task is not None and not self.worker_task.done()

    def _submit(self, task: str) -> None:
        self.cancel_event.clear()
        self.sink.on_user_message(task)

        self.active_sprite = sprites.pick()
        self.sprite_started_at = time.monotonic()

        self.worker_task = self.application.create_background_task(self._run_turn(task))

    async def _run_turn(self, task: str) -> None:
        try:
            await asyncio.to_thread(self.agent.run, task, self.sink, self.cancel_event)

        except Exception as e:
            tb = ''.join(traceback.format_exception(type(e), e, e.__traceback__)[-10:])
            self.sink.on_error(f'{type(e).__name__}: {e}\n{tb}')

        finally:
            self.active_sprite = None
            self.application.invalidate()

    # ----------------------------------------
    # Status
    # ----------------------------------------

    def _get_status(self) -> dict:
        running = self._is_running()
        sprite = self.active_sprite if running else None
        elapsed = (time.monotonic() - self.sprite_started_at) if running else 0.0

        return {
            'provider': self.agent.provider,
            'model': self.agent.model,
            'cwd': os.getcwd(),
            'running': running,
            'sprite': sprite,
            'sprite_elapsed': elapsed,
            'scroll_locked': not self.output.follow_tail,
            'scroll_y': self.output._scroll_target,
            'copy_mode': self.copy_mode,
            'last_call_usage': self.tui_sink.last_call_usage,
            'last_turn_usage': self.tui_sink.last_turn_usage,
            'session_usage': self.tui_sink.session_usage,
        }

    # ----------------------------------------
    # Animation ticker — repaint while a turn is in flight
    # ----------------------------------------

    async def _animate_loop(self) -> None:
        """Repaint sprites during active turns, choosing a new one after each cycle.

        Frames use elapsed time; tick frequency controls repaint smoothness.
        Idle ticks do no work.
        """
        while True:
            if self._is_running() and self.active_sprite is not None:
                self._maybe_rotate_sprite()
                self.application.invalidate()

            await asyncio.sleep(ANIM_TICK_S)

    def _maybe_rotate_sprite(self) -> None:
        """Swap to a new random sprite once the current one finishes a cycle."""
        if self.active_sprite is None:
            return

        elapsed = time.monotonic() - self.sprite_started_at

        if elapsed >= sprites.cycle_seconds(self.active_sprite):
            self.active_sprite = sprites.pick_different(self.active_sprite)
            self.sprite_started_at = time.monotonic()

    # ----------------------------------------
    # Resize handling — keep History.width in sync
    # ----------------------------------------

    RESIZE_DEBOUNCE_S = 0.3
    RESIZE_POLL_S = 0.05

    def _current_width(self) -> int:
        return max(20, self.application.output.get_size().columns - 2)

    def _sync_width_now(self) -> None:
        """Immediate width sync. Used once on startup."""
        new_width = self._current_width()

        if new_width != self.history.width:
            self.history.width = new_width
            self.history.rerender_all()

    async def _watch_size(self) -> None:
        """Wait for terminal width to settle, then rerender cells in a worker thread.

        Debouncing avoids repeated Rich renders during resize; background work keeps
        UI input responsive.
        """
        last_seen = self.history.width
        changed_at: float | None = None

        while True:
            new_width = self._current_width()

            if new_width != last_seen:
                last_seen = new_width
                changed_at = time.monotonic()

            elif changed_at is not None and time.monotonic() - changed_at >= self.RESIZE_DEBOUNCE_S:
                if new_width != self.history.width:
                    self.history.width = new_width
                    await asyncio.to_thread(self.history.rerender_all)
                    self.application.invalidate()

                changed_at = None

            await asyncio.sleep(self.RESIZE_POLL_S)

    # ----------------------------------------
    # Run
    # ----------------------------------------

    async def run_async(self) -> None:
        self._sync_width_now()

        size_task = asyncio.create_task(self._watch_size())
        anim_task = asyncio.create_task(self._animate_loop())

        try:
            await self.application.run_async()

        finally:
            size_task.cancel()
            anim_task.cancel()
