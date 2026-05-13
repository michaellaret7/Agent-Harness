"""LangfuseSink — mirrors Sink events to Langfuse spans.

One trace per call to `Agent.run`. Structure:

  root span "agent-turn"
   ├─ generation         (auto-captured by langfuse.openai instrumentation)
   ├─ tool <tool-name>   (started here on on_tool_start, ended on on_tool_end)
   └─ generation         (next LLM call in the tool loop)

The turn span is held open across multiple sink callbacks by manually
driving the langfuse context-manager protocol — `__enter__` on
on_turn_start, `__exit__` on on_turn_end. While the turn CM is active,
the OpenAI wrapper's generations and our manually-started tool spans
both nest underneath it via OpenTelemetry context propagation.

The Sink calls happen on the agent worker thread. Each turn owns its own
`_tool_spans` dict, which is not shared across threads.
"""
from __future__ import annotations

import logging
from typing import Any

from langfuse import Langfuse, propagate_attributes

log = logging.getLogger(__name__)


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
        self._tool_spans: dict[str, Any] = {}
        self._errors: list[str] = []
        self._interrupted = False

    #     ================================
    # --> Turn boundaries
    #     ================================

    def on_turn_start(self, prompt: str) -> None:
        self._tool_spans.clear()
        self._errors.clear()
        self._interrupted = False
        self._session_cm = None
        self._turn_cm = None
        self._turn_span = None

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
                as_type='span',
                name='agent-turn',
                input=prompt,
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
