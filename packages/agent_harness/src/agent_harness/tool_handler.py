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
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_harness.gates import GateContext
from agent_harness.messages import tool_msg
from agent_harness.sinks.base import Sink, ToolOutcome, ToolStatus
from agent_harness.base_tools.helpers.paths import resolve_path

if TYPE_CHECKING:
    from agent_harness.agent import Agent

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

    # This is the main execution algorithm for the tool handler which is called by the loop.py file
    # This takes a list of tool calls and executes them in a batch or sequentially based on a field from the tool dictionary
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

        t0 = time.perf_counter()

        # Run the actual tool function. This is the main line of the whole tool handler basically.
        # This returns the result of the tool call and the status of the tool call (so whether it threw and error or not)
        result, status = self._dispatch(tc, sink, cancel_event)

        duration = time.perf_counter() - t0

        # Emit tool result end event to the sink
        # Return the result in the form of the ToolOutcome class
        sink.on_tool_end(
            tool_call_id,
            ToolOutcome(
                payload=result, 
                status=status, 
                duration=duration
            ),
        )

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
    
    
    def _dispatch(
        self,
        tc: dict,
        sink: Sink,
        cancel_event: threading.Event,
    ) -> tuple[str, ToolStatus]:
        """Run the tool body and classify the outcome.

        Returns `(payload, status)` so the caller can stamp duration around
        the whole dispatch and build one `ToolOutcome` for the sink. Side
        effects (file-diff emit, plan-update emit) fire here because they
        depend on the same status determination.
        """

        # Unpack the tool call dictionary passed to the tool handler by the agent and assign them to variables
        tool_call_id = tc['id']
        name = tc['function']['name']
        args_json = tc['function']['arguments']

        if cancel_event.is_set():
            return '[interrupted]', 'interrupted'

        try:
            # Load the arguments from the tool call dictionary into a smaller dict called kwargs
            kwargs = json.loads(args_json) if args_json else {}
        except (ValueError, TypeError) as e:
            # Raise an error if the arguments are bad
            return f'error: bad arguments JSON: {e}', 'error'

        # Run the call past the gates: returns the (possibly rewritten) kwargs
        # and a deny reason if any gate blocked it (None means nothing is blocking the tool call)
        kwargs, deny_reason = self._apply_gates(name, tool_call_id, kwargs)

        if deny_reason is not None:
            return f'denied: {deny_reason}', 'denied'

        # TODO: This is an issue here. This tool handling snippet is specific to tools from coding agent
        # These need to be gotten rid of and handled at the tool level
        target: Path | None = None

        if name in ('EditFile', 'WriteFile'):
            raw = kwargs.get('file_path')

            if isinstance(raw, str) and raw:
                target = resolve_path(raw)

        before = _snapshot(target) if target else ''

        # Run the actual tool funtion and return the result 
        result = self._invoke(name, kwargs)

        # Check the success status of the tool and see if it threw an error or not 
        # If there was an error update the tool status else return ok 
        status: ToolStatus = 'error' if result.startswith('error:') else 'ok'

        if target is not None and status == 'ok':
            after = _snapshot(target)

            if after != before:
                sink.on_file_diff(tool_call_id, str(target), before, after)

        if name == 'Plan' and status == 'ok':
            sink.on_plan_update(self.agent.plan)

        return result, status

    def _is_parallel(self, tc: dict) -> bool:
        """A small helper function to check whether a tool call's underlying tool opted into safe parallelism."""

        # Get the tool function from the agent's tool functions dictionary
        fn = self.agent.tool_functions.get(tc['function']['name'])

        # Return the safe parallel flag from the tool function dictionary
        # This will determine if the tool can be run in the ThreadPoolExecutor or not 
        return getattr(fn, 'tool', {}).get('safe_parallel', False)

    def _apply_gates(
        self,
        name: str,
        tool_call_id: str,
        kwargs: dict,
    ) -> tuple[dict, str | None]:
        """
        Run this one tool call past every registered gate, in order.

        Called once per dispatch for a single tool call (`name`). Each gate
        decides whether it applies (via its tool filter) and, if so, returns
        a verdict. Returns `(resolved_kwargs, deny_reason)`: a non-None
        `deny_reason` means a gate blocked the call and dispatch must stop.
        A rewrite is visible to every later gate; the first deny wins.
        """

        # self.agent.gates holds one (tool_filter, gate_fn) tuple per registered
        # gate. Walk them all; `tool_filter` is the set of tool names a gate
        # guards (None = every tool), and `name` is the tool this call invokes.
        for tool_filter, gate in self.agent.gates:
            # Skip gates whose filter doesn't cover this tool (None = all tools).
            if tool_filter is not None and name not in tool_filter:
                continue

            # Present this call to the gate and consume its verdict. A gate
            # that raises fails closed: we deny the call rather than letting a
            # broken guard pass it through.
            ctx = GateContext(
                name,
                tool_call_id,
                kwargs,
                self.agent,
            )

            try:
                verdict = gate(ctx)
                
            except Exception as e:
                gate_name = getattr(gate, '__name__', repr(gate))
                print(f'[GATE ERROR] {gate_name} raised on {name}, denying: {e}')

                return kwargs, f'gate error in {gate_name}: {e}'

            if verdict.decision == 'deny':
                return kwargs, verdict.reason

            # A rewrite carries replacement args (guaranteed by the factory);
            # apply them so later gates and _invoke see the new values.
            if verdict.decision == 'rewrite' and verdict.args is not None:
                kwargs = verdict.args

        # If no gate applied, just pass the kwargs through to the invoke method
        return kwargs, None

    def _invoke(self, name: str, kwargs: dict) -> str:
        """Look up the registered function and run it with exception wrapping."""

        # Check to make sure the deferred tools get loaded before being called if they are deferred
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
