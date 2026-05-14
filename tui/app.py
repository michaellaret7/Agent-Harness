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
import uuid
from typing import TYPE_CHECKING, cast

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.styles import Style

from agent.sinks import MultiSink, Sink
from tui import sprites
from tui.history import History
from tui.panels import InputPanel, OutputPanel, StatusBar
from tui.sink import TUISink
from tui.sprites import Sprite

if TYPE_CHECKING:
    from agent.agent import Agent

CTRL_C_DOUBLE_TAP_SECONDS = 2.0
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
        # in `_get_status`; `sink` is what the agent loop sees (may be a
        # MultiSink wrapping it alongside Langfuse).
        self.tui_sink: TUISink = TUISink(history=self.history, app=self.application)
        self.sink: Sink = self._build_sink()

    def _build_sink(self) -> Sink:
        sinks: list[Sink] = [self.tui_sink]

        # Presence of LANGFUSE_PUBLIC_KEY is the on switch. Lazy-import the
        # sink so the langfuse package is only required when tracing is on.
        if os.getenv('LANGFUSE_PUBLIC_KEY'):
            from agent.sinks.langfuse import LangfuseSink

            # One TUI launch = one Langfuse session. Every turn during this
            # process lifetime gets tagged with the same session_id and
            # appears bundled under one "Session" in the Langfuse UI.
            session_id = uuid.uuid4().hex

            sinks.append(LangfuseSink(
                session_id=session_id,
                metadata={
                    'provider': self.agent.provider,
                    'model': self.agent.model,
                },
            ))

        if len(sinks) == 1:
            return sinks[0]

        # MultiSink uses __getattr__ to fan events out — Pyright can't see
        # that as Protocol-conforming, so cast.
        return cast(Sink, MultiSink(sinks))

    # ----------------------------------------
    # Layout
    # ----------------------------------------

    def _build_application(self) -> Application:
        root = HSplit([
            self.output.window,
            self.input.container,
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
            # Cap render rate at 60 Hz. Smooth-scroll mice fire 60-120 wheel
            # events/sec; without this floor each one races through a full
            # re-render. 60 Hz is the smallest interval the eye can resolve
            # and gives the renderer headroom to coalesce burst events.
            min_redraw_interval=1 / 60,
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

        @kb.add('c-t')
        def _on_toggle_copy_mode(event: KeyPressEvent) -> None:
            self.copy_mode = not self.copy_mode
            event.app.invalidate()

        # Tab is intentionally unbound at the app level so it stays as
        # in-buffer text insertion in the input. A previous binding cycled
        # focus via `focus_next()`, which (combined with the output panel
        # being focusable) routinely moved focus to the output and made
        # the TUI look frozen — keys typed there go nowhere visible.

        return kb

    # ----------------------------------------
    # Worker dispatch
    # ----------------------------------------

    def _is_running(self) -> bool:
        return self.worker_task is not None and not self.worker_task.done()

    def _submit(self, prompt: str) -> None:
        self.cancel_event.clear()
        self.sink.on_user_message(prompt)

        self.active_sprite = sprites.pick()
        self.sprite_started_at = time.monotonic()

        self.worker_task = self.application.create_background_task(self._run_turn(prompt))

    async def _run_turn(self, prompt: str) -> None:
        try:
            await asyncio.to_thread(self.agent.run, prompt, self.sink, self.cancel_event)

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
        """Tick the sprite while a turn is in flight.

        Two responsibilities, both gated on `_is_running()`:
          1. Rotate `active_sprite` to a fresh random one once the current
             sprite has played one full cycle. This is what gives a long turn
             its slideshow of different little characters.
          2. Invalidate the app so the status bar repaints the next frame.

        Cheap when idle (one wakeup per tick, no work). The status bar derives
        the displayed frame from elapsed wall-clock time, so the tick cadence
        sets *smoothness*, not animation speed.
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
        """Re-render cells once the terminal width has been stable for a beat.

        rerender_all walks every cell through Rich, which is expensive. During
        an active resize drag the OS fires many width changes in quick
        succession; running rerender_all on each one starves the event loop.
        Waiting for the size to settle defers that work to the post-drag idle
        moment, where it's invisible to the user.

        The actual re-render is offloaded to a worker thread via
        `asyncio.to_thread` — on a long history a single rerender can take
        100+ ms, and running it on the event-loop thread blocks all input
        (clicks, scroll, keystrokes) for that entire window.
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
