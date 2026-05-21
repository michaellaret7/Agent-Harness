"""Executes parsed tool calls against the agent's registered tool functions.

The handler does one thing: take parsed tool_calls from the model, run the
matching Python callables, push start/end events to the Sink, and return
tool-result messages. Registration lives on the Agent.

For `EditFile` / `WriteFile` calls the handler snapshots the target file
before and after the call and emits a `on_file_diff` event so the UI can
render an inline highlighted diff. Tool functions themselves stay pure —
the LLM's tool-result message is unchanged.

Cancellation: between tool calls, checks `cancel_event`. Tool calls
in-flight are not killed (Python sync code can't be interrupted), but
remaining calls in the batch are skipped and synthesized as interrupted.

Parallel dispatch: tools opted into `safe_parallel=True` on `@agent_tool`
are grouped into consecutive chunks and run concurrently via a
`ThreadPoolExecutor`. Non-parallel tools (Bash, EditFile, WriteFile, …)
act as barriers — the chunk before them completes before they start, and
they complete before the next chunk begins. Tool-result message order
always matches the model's emitted tool_call order.
"""
from __future__ import annotations

import contextvars
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.messages import tool_msg
from agent.sinks import Sink
from tools.helpers.paths import resolve_path

if TYPE_CHECKING:
    from agent.agent import Agent

#     ================================
# --> Helper funcs
#     ================================


def _snapshot(path: Path) -> str:
    """Read file contents, returning '' if the file doesn't exist yet."""
    if not path.is_file():
        return ''

    try:
        return path.read_text(encoding='utf-8')

    except OSError:
        return ''

#     ================================
# --> Handler
#     ================================


class ToolHandler:
    def __init__(self, agent: 'Agent') -> None:
        self.agent = agent

    def execute(
        self,
        tool_calls: list[dict],
        sink: Sink,
        cancel_event: threading.Event,
    ) -> list[dict[str, Any]]:
        """Run a batch of tool calls and return tool-result messages in order.

        Walks the batch once, grouping consecutive `safe_parallel` calls
        into chunks dispatched through a thread pool. Non-parallel calls
        run serially and act as barriers between chunks. Result-message
        order always matches the model's emitted tool_call order.

        Cancellation is checked at chunk boundaries. Once `cancel_event`
        is set, the in-flight chunk completes (Python sync code cannot be
        interrupted) and every remaining tool_call short-circuits to
        `[interrupted]` via `_run_tool`, preserving sink events and the
        tool_call/tool_result pairing in message history.
        """
        results: list[str] = []
        n = len(tool_calls)
        i = 0

        # Start a while loop while the length of tool calls is greater than the index
        # This is the main tool loop, looping through each of the submitted tool calls
        # The index i is used to track the current tool call being processed
        while i < n:
            # Check if the cancellation event has been set
            # If it has, we need to run the remaining tool calls and break out of the loop
            if cancel_event.is_set():
                results.extend(self._run_tool(tc, sink, cancel_event) for tc in tool_calls[i:])
                break
            
            # Check if the current tool at item i is parallel 
            # The is parallel check comes from the parallel arg being passed to the agent_tool decorator
            # If the tool is able to run in parallel, continue, otherwise run the tool sequentially
            # From line 100 to 107 we are basically just creating a mini list of tool calls that can run in parallel 
            # Once the list it built we submit them to the threadpool on line 111
            if self._is_parallel(tool_calls[i]):
                # Create new mini index of j within the current chunk
                j = i

                # Begin a while loop saying while mini index is less than number of tool calls and the current tool at item j is parallel
                # add 1 to j to move to the next tool call
                while j < n and self._is_parallel(tool_calls[j]):
                    j += 1

                # Run the parallel tools returned from the while loop and add the results to the results list
                results.extend(self._run_tools_parallel(tool_calls[i:j], sink, cancel_event))

                # Set the index i to the new mini index j
                i = j

            else:
                # Run the tool sequentially and add the result to the results list
                results.append(self._run_tool(tool_calls[i], sink, cancel_event))
                i += 1
        
        # Return the results list as tool messages
        tc_results = [tool_msg(tc['id'], r) for tc, r in zip(tool_calls, results)]

        return tc_results

    def _run_tool(
        self,
        tc: dict,
        sink: Sink,
        cancel_event: threading.Event,
    ) -> str:
        """Run one tool call end-to-end: emit events, parse args, snapshot file, dispatch."""

        # Unpack the tool call dict into id, name, and raw JSON args
        # To clarify, this is what the agent passes to the tool handler 
        tool_call_id = tc['id']
        name = tc['function']['name']
        args_json = tc['function']['arguments']

        # Emit tool start event to the sink
        sink.on_tool_start(tool_call_id, name, args_json)

        # Short-circuit to '[interrupted]' if cancellation was requested before this tool runs
        if cancel_event.is_set():
            result = '[interrupted]'

        else:
            try:
                # Parse the JSON arguments string into a kwargs dict
                kwargs = json.loads(args_json) if args_json else {}

            except (ValueError, TypeError) as e:
                result = f'error: bad arguments JSON: {e}'
                sink.on_tool_end(tool_call_id, result)
                return result

            # For EditFile/WriteFile, resolve the target so we can snapshot it before/after the call
            target: Path | None = None

            if name in ('EditFile', 'WriteFile'):
                raw = kwargs.get('file_path')

                if isinstance(raw, str) and raw:
                    target = resolve_path(raw)

            before = _snapshot(target) if target else ''

            # Dispatch to the registered tool function
            # This is where the actual tool function is called and the result is returned
            result = self._invoke(name, kwargs)

            # If the file was touched and the call succeeded, emit a diff event to the sinkwhen it changed
            if target is not None and not result.startswith('error:'):
                after = _snapshot(target)

                if after != before:
                    sink.on_file_diff(tool_call_id, str(target), before, after)

            # If the plan was updated, push the new state to the sink so UI
            # surfaces can re-render. Mirrors the file-diff pattern above.
            if name == 'Plan' and not result.startswith('error:'):
                sink.on_plan_update(self.agent.plan)

        # Emit tool end event to the sink
        sink.on_tool_end(tool_call_id, result)

        return result

    def _run_tools_parallel(
        self,
        batch: list[dict],
        sink: Sink,
        cancel_event: threading.Event,
    ) -> list[str]:
        """Run a batch of safe-parallel tool calls concurrently, preserving order."""
        
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            # Per-future contextvars copy so sink callbacks on pool threads see the iteration's OTel context.
            futures = [
                pool.submit(contextvars.copy_context().run, self._run_tool, tc, sink, cancel_event)
                for tc in batch
            ]

            return [fut.result() for fut in futures]

    def _is_parallel(self, tc: dict) -> bool:
        """Check whether a tool call's underlying tool opted into safe parallelism."""

        # Get the tool function from the agent's tool functions dictionary
        fn = self.agent.tool_functions.get(tc['function']['name'])

        # Return the safe parallel flag from the tool function dictionary
        # This will determine if the tool can be run in the ThreadPoolExecutor or not 
        return getattr(fn, 'tool', {}).get('safe_parallel', False)

    def _invoke(self, name: str, kwargs: dict) -> str:
        """Look up the registered function and run it with exception wrapping."""

        if name in self.agent.deferred_tools and name not in self.agent.loaded_deferred:
            return (
                f'error: {name!r} is deferred. Call LoadTool(names=[{name!r}]) '
                f'first to retrieve the full schema, then call {name} with the '
                f'correct arguments.'
            )

        # Get the tool function from the agent's tool functions dictionary
        fn = self.agent.tool_functions.get(name)

        # If the tool name is not found in the tool function dictionary, return an error
        if fn is None:
            return f'error: unknown tool {name!r}'

        try:
            # Run the actual tool function with the keyword arguments 
            # Return the result as a string
            return str(fn(**kwargs))

        except Exception as e:
            return f'error: {type(e).__name__}: {e}'
