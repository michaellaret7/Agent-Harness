"""Dispatch registered tools, emit Sink events, and return tool-result messages.

Registration belongs to Agent. Consecutive safe_parallel calls run concurrently;
serial calls form barriers. Results retain the model's call order. Cancellation
lets in-flight calls finish and marks remaining calls interrupted.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from agent_harness.gates import GateContext
from agent_harness.messages import tool_msg
from agent_harness.tooling.context import CONTEXT_PARAM, ToolContext
from agent_harness.tooling.failure_streak import MAX_TOOL_FAILURES, FailureStreaks
from agent_harness.tooling.result import ToolResult
from agent_harness.sinks.base import Sink, ToolOutcome, ToolStatus

if TYPE_CHECKING:
    from agent_harness.agent import Agent

#     ================================
# --> Handler
#     ================================


class ToolHandler:
    def __init__(self, agent: 'Agent') -> None:
        self.agent = agent

        # Blocks a tool that keeps failing; Agent.run resets it at every run start
        self.failure_streaks = FailureStreaks()

    # This is the main execution algorithm for the tool handler which is called by the loop.py file
    # This takes a list of tool calls and executes them in a batch or sequentially based on a field from the tool dictionary
    def execute(
        self,
        tool_calls: list[dict],
        sink: Sink,
        cancel_event: threading.Event,
    ) -> list[dict[str, Any]]:
        """Execute tool calls and return result messages in the original order.

        Consecutive safe_parallel calls share a thread pool; serial calls are barriers.
        Cancellation is checked between chunks. In-flight calls finish; remaining calls
        emit interrupted results and sink events, preserving call/result pairs.
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
        result, status, data = self._dispatch(tc, sink, cancel_event)

        duration = time.perf_counter() - t0

        # Count consecutive failures per tool; surface the moment a tool becomes blocked
        if self.failure_streaks.record(name, status, result):
            sink.on_error(f'tool.blocked {name} after {MAX_TOOL_FAILURES} consecutive failures: {result}')

        # Emit tool result end event to the sink
        # Return the result in the form of the ToolOutcome class
        sink.on_tool_end(
            tool_call_id,
            ToolOutcome(
                payload=result, 
                status=status, 
                duration=duration,
                data=data,
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
    ) -> tuple[str, ToolStatus, Any]:
        """Run the tool body and classify the outcome.

        Returns `(payload, status, data)` so the caller can stamp duration around
        the whole dispatch and build one `ToolOutcome` for the sink. Side
        effects a tool has on the run (e.g. Plan's plan-update emit) happen
        inside the tool, through the ToolContext passed by `_invoke`.
        """

        # Unpack the tool call dictionary passed to the tool handler by the agent and assign them to variables
        tool_call_id = tc['id']
        name = tc['function']['name']
        args_json = tc['function']['arguments']

        if cancel_event.is_set():
            return '[interrupted]', 'interrupted', None

        # A tool that failed too many times in a row is blocked: tell the model to change course
        block_reason = self.failure_streaks.block_reason(name)

        if block_reason is not None:
            return f'denied: {block_reason}', 'denied', None

        try:
            # Load the arguments from the tool call dictionary into a smaller dict called kwargs
            kwargs = json.loads(args_json) if args_json else {}
        except (ValueError, TypeError) as e:
            # Raise an error if the arguments are bad
            return f'error: bad arguments JSON: {e}', 'error', None

        # Underscore params are bound by the application (bind_tool) and hidden from the schema;
        # a model-supplied value would override the bound one, so the model may never set them
        hidden = sorted(key for key in kwargs if str(key).startswith('_'))

        if hidden:
            return f'error: unknown arguments {hidden}', 'error', None

        # Run the call past the gates: returns the (possibly rewritten) kwargs
        # and a deny reason if any gate blocked it (None means nothing is blocking the tool call)
        kwargs, deny_reason = self._apply_gates(name, tool_call_id, kwargs)

        if deny_reason is not None:
            return f'denied: {deny_reason}', 'denied', None

        # Run the actual tool funtion and return the result
        result = self._invoke(name, kwargs, ToolContext(self.agent, sink, cancel_event))

        # Read the explicit tool status independently of its model-facing payload.
        status: ToolStatus = result.status

        return result.payload, status, result.data

    def _is_parallel(self, tc: dict) -> bool:
        """A small helper function to check whether a tool call's underlying tool opted into safe parallelism."""

        # Check the agent's registry of safe-parallel tool names, recorded at add_tool time.
        # This will determine if the tool can be run in the ThreadPoolExecutor or not
        return tc['function']['name'] in self.agent.parallel_tools

    def _apply_gates(
        self,
        name: str,
        tool_call_id: str,
        kwargs: dict,
    ) -> tuple[dict, str | None]:
        """Apply matching gates in order and return `(resolved_kwargs, deny_reason)`.

        Rewrites reach later gates. The first denial stops processing; a non-None
        reason tells dispatch to block the call.
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

    def _invoke(self, name: str, kwargs: dict, ctx: ToolContext) -> ToolResult:
        """Look up the registered function and run it with exception wrapping.

        Tools that declare `_ctx` receive `ctx`. It is added after the gates
        ran, so gates only ever see the model's arguments.
        """

        # Check to make sure the deferred tools get loaded before being called if they are deferred
        # (LoadTool pops loaded tools out of the registry, so membership alone means "not yet loaded")
        if name in self.agent.deferred_tools:
            return ToolResult(
                f'error: {name!r} is deferred. Call LoadTool(names=[{name!r}]) '
                f'first to retrieve the full schema, then call {name} with the '
                f'correct arguments.',
                status='error',
            )

        # Get the tool function from the agent's tool functions dictionary
        fn = self.agent.tool_functions.get(name)

        # If the tool name is not found in the tool function dictionary, return an error
        if fn is None:
            return ToolResult(f'error: unknown tool {name!r}', status='error')

        # Hand the run's context to tools that asked for it (recorded at add_tool time)
        if name in self.agent.context_tools:
            kwargs = {**kwargs, CONTEXT_PARAM: ctx}

        try:
            # Run the actual tool function with the keyword arguments 
            # Require an explicit result; arbitrary output cannot imply success.
            result = fn(**kwargs)

            if not isinstance(result, ToolResult):
                raise TypeError(f'tool {name!r} must return ToolResult, got {type(result).__name__}')

            return result

        except Exception as e:
            return ToolResult(f'error: {type(e).__name__}: {e}', status='error')
