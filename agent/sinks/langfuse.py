"""LangfuseSink — mirrors Sink events to Langfuse spans.

One trace per call to `Agent.run`. Structure:

  root span "agent-turn"
   └─ chain "execution_loop"
        ├─ span "iteration_1"
        │    ├─ generation          (auto-captured by langfuse.openai)
        │    └─ tool <tool-name>    (started on on_tool_start, ended on on_tool_end)
        ├─ span "iteration_2"
        │    └─ ...
        └─ ...

Every level is held open across multiple sink callbacks by manually
driving the langfuse context-manager protocol — `__enter__` on the
*_start event, `__exit__` on the *_end event. OTel context is
thread-local; all callbacks run on the agent worker thread, so the
push/pop chain stays consistent.

`start_as_current_observation` makes the new observation OTel-current,
so anything entered next (tool spans, langfuse.openai generations,
nested spans) attaches to it as a child. Tool spans use
`start_observation` (non-current) — they attach to the current
observation at creation time (the iteration span) but don't themselves
become current, so their lifetime is independent of any other push/pop.

The Sink calls happen on the agent worker thread. Each turn owns its own
`_tool_spans` dict, which is not shared across threads.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langfuse import Langfuse, propagate_attributes

if TYPE_CHECKING:
    from agent.usage import Usage

log = logging.getLogger(__name__)


#     ================================
# --> Helper funcs
#     ================================


def _iteration_output(action: str, content: str, tools_called: list[str]) -> dict[str, Any]:
    """Translate the loop's iteration `action` into a span output payload.

    The action set comes from `agent.loop._classify` — keeping the
    translation here keeps Langfuse-specific formatting out of the loop.
    """
    if action == 'answer_ready':
        return {'action': 'answer_ready', 'assistant_text': content}

    if action == 'cancelled':
        return {'action': 'cancelled', 'assistant_text': content}

    if action == 'partial_cancelled':
        return {'action': 'cancelled', 'partial_tool_calls': True}

    return {
        'action': 'tool_calls',
        'tools_called': tools_called,
        'assistant_text': content or None,
    }


#     ================================
# --> Sink
#     ================================


class LangfuseSink:
    def __init__(
        self,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._client = Langfuse()
        self._session_id = session_id
        self._base_metadata = metadata or {}

        self._session_cm: Any | None = None
        self._turn_cm: Any | None = None
        self._turn_span: Any | None = None
        self._loop_cm: Any | None = None
        self._loop_span: Any | None = None
        self._iter_cm: Any | None = None
        self._iter_span: Any | None = None
        self._tool_spans: dict[str, Any] = {}
        self._errors: list[str] = []
        self._interrupted = False

    #     ================================
    # --> Turn boundaries
    #     ================================

    def on_turn_start(self, task: str) -> None:
        self._tool_spans.clear()
        self._errors.clear()
        self._interrupted = False
        self._session_cm = None
        self._turn_cm = None
        self._turn_span = None
        self._loop_cm = None
        self._loop_span = None
        self._iter_cm = None
        self._iter_span = None

        try:
            # propagate_attributes must be entered *before* the turn span so
            # session_id is applied to that span and any children. OTel context
            # is thread-local — entering on the worker thread that also
            # creates the turn span keeps the propagation working.
            if self._session_id:
                session_cm = propagate_attributes(session_id=self._session_id)
                session_cm.__enter__()
                self._session_cm = session_cm

            cm = self._client.start_as_current_observation(
                as_type='agent',
                name='agent-turn',
                input=task,
                metadata=self._base_metadata or None,
            )
            self._turn_span = cm.__enter__()
            self._turn_cm = cm

        except Exception as e:
            log.warning('langfuse: failed to start turn span: %s', e)

    def on_turn_end(self, result: str) -> None:
        # Defensive: orphan any tool spans that never received on_tool_end.
        for span in self._tool_spans.values():
            try:
                span.update(output='[no result emitted]', level='WARNING')
                span.end()

            except Exception as e:
                log.warning('langfuse: failed to close orphan tool span: %s', e)

        self._tool_spans.clear()

        # Defensive: an iter/loop span may still be open if the loop bailed
        # before its terminal callback fired (e.g., exception, partial cancel).
        # Close inner-out to keep OTel context unwinding correctly.
        self._close_iteration_span(level='WARNING', status='turn ended mid-iteration')
        self._close_loop_span(stop_reason='aborted', iterations=None, level='WARNING')

        if self._turn_span is not None:
            try:
                update: dict[str, Any] = {'output': result}

                if self._interrupted:
                    update['level'] = 'WARNING'
                    update['status_message'] = 'interrupted'

                elif self._errors:
                    update['level'] = 'ERROR'
                    update['status_message'] = '; '.join(self._errors)

                self._turn_span.update(**update)

            except Exception as e:
                log.warning('langfuse: failed to update turn span: %s', e)

        if self._turn_cm is not None:
            try:
                self._turn_cm.__exit__(None, None, None)

            except Exception as e:
                log.warning('langfuse: failed to end turn span: %s', e)

        if self._session_cm is not None:
            try:
                self._session_cm.__exit__(None, None, None)

            except Exception as e:
                log.warning('langfuse: failed to exit session context: %s', e)

        self._turn_cm = None
        self._turn_span = None
        self._session_cm = None

        try:
            self._client.flush()

        except Exception as e:
            log.warning('langfuse: flush failed: %s', e)

    #     ================================
    # --> Loop + iteration spans
    #     ================================

    def on_loop_start(self, model: str, max_iters: int, tool_names: list[str]) -> None:
        if self._turn_span is None:
            return  # turn never started — nothing to attach under

        try:
            cm = self._client.start_as_current_observation(
                as_type='chain',
                name='execution_loop',
                input={'model': model, 'max_iters': max_iters, 'tools': tool_names},
            )
            self._loop_span = cm.__enter__()
            self._loop_cm = cm

        except Exception as e:
            log.warning('langfuse: failed to start loop span: %s', e)

    def on_loop_end(self, stop_reason: str, iterations: int) -> None:
        self._close_loop_span(stop_reason=stop_reason, iterations=iterations)

    def on_iteration_start(self, number: int, message_count: int) -> None:
        if self._loop_span is None:
            return

        try:
            cm = self._client.start_as_current_observation(
                as_type='span',
                name=f'iteration_{number}',
                input={'iteration': number, 'message_count': message_count},
                metadata={'iteration': str(number)},
            )
            self._iter_span = cm.__enter__()
            self._iter_cm = cm

        except Exception as e:
            log.warning('langfuse: failed to start iteration span %d: %s', number, e)

    def on_iteration_end(self, number: int, action: str, content: str, tools_called: list[str]) -> None:
        if self._iter_span is not None:
            try:
                self._iter_span.update(output=_iteration_output(action, content, tools_called))

            except Exception as e:
                log.warning('langfuse: failed to update iteration span %d: %s', number, e)

        level = 'WARNING' if action in ('cancelled', 'partial_cancelled') else None
        self._close_iteration_span(level=level)

    def _close_iteration_span(self, *, level: str | None = None, status: str | None = None) -> None:
        if self._iter_cm is None:
            return

        try:
            if level is not None and self._iter_span is not None:
                update: dict[str, Any] = {'level': level}

                if status:
                    update['status_message'] = status

                self._iter_span.update(**update)

            self._iter_cm.__exit__(None, None, None)

        except Exception as e:
            log.warning('langfuse: failed to end iteration span: %s', e)

        self._iter_cm = None
        self._iter_span = None

    def _close_loop_span(
        self,
        *,
        stop_reason: str,
        iterations: int | None,
        level: str | None = None,
    ) -> None:
        if self._loop_cm is None:
            return

        if self._loop_span is not None:
            try:
                update: dict[str, Any] = {
                    'output': {'stop_reason': stop_reason, 'iterations': iterations},
                }

                if level is not None:
                    update['level'] = level

                self._loop_span.update(**update)

            except Exception as e:
                log.warning('langfuse: failed to update loop span: %s', e)

        try:
            self._loop_cm.__exit__(None, None, None)

        except Exception as e:
            log.warning('langfuse: failed to end loop span: %s', e)

        self._loop_cm = None
        self._loop_span = None

    #     ================================
    # --> Tool spans
    #     ================================

    def on_tool_start(self, tool_call_id: str, name: str, args_json: str) -> None:
        if self._turn_span is None:
            return  # turn never started — nothing to attach under

        try:
            span = self._client.start_observation(
                as_type='tool',
                name=name,
                input=args_json,
            )
            self._tool_spans[tool_call_id] = span

        except Exception as e:
            log.warning('langfuse: failed to start tool span %s: %s', name, e)

    def on_tool_end(self, tool_call_id: str, result: str) -> None:
        span = self._tool_spans.pop(tool_call_id, None)

        if span is None:
            return

        try:
            update: dict[str, Any] = {'output': result}

            if result.startswith('error:') or result == '[interrupted]':
                update['level'] = 'ERROR' if result.startswith('error:') else 'WARNING'
                update['status_message'] = result

            span.update(**update)
            span.end()

        except Exception as e:
            log.warning('langfuse: failed to end tool span %s: %s', tool_call_id, e)

    def on_file_diff(self, tool_call_id: str, path: str, before: str, after: str) -> None:
        span = self._tool_spans.get(tool_call_id)

        if span is None:
            return

        try:
            span.update(metadata={
                'diff_path': path,
                'diff_before_chars': len(before),
                'diff_after_chars': len(after),
            })

        except Exception as e:
            log.warning('langfuse: failed to attach diff metadata: %s', e)

    def on_plan_update(self, plan: list[dict]) -> None:
        # No-op: the Plan tool's input/output are already captured on the
        # tool span via on_tool_start / on_tool_end.
        pass

    #     ================================
    # --> Error / interruption flags
    #     ================================

    def on_error(self, message: str) -> None:
        self._errors.append(message)

    def on_interrupted(self) -> None:
        self._interrupted = True

    #     ================================
    # --> No-ops (LLM deltas are already captured by langfuse.openai)
    #     ================================

    def on_user_message(self, text: str) -> None:
        pass

    def on_reasoning_delta(self, text: str) -> None:
        pass

    def on_content_delta(self, text: str) -> None:
        pass

    def on_assistant_end(self) -> None:
        pass

    def on_usage(self, usage: Usage) -> None:
        # Already captured by the langfuse.openai monkey-patch on the
        # auto-generation span — do not double-send.
        pass
