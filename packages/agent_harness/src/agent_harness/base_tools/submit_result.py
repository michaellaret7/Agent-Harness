"""SubmitResult: the only way an agent with `output_model` can finish.

Validated termination. Without it, the loop treats any reply without tool
calls as "done", so a model can stop early and claim success. With an
`output_model`, the model must hand its answer in through this tool:

  1. the arguments are validated against `output_model` (shape check);
  2. the optional `verifier` runs on the validated object (your own check).

A failed check is returned as an error tool result, so the model reads why
and keeps working with its full context. An accepted result is stored on
`agent.submission`; the loop sees it and ends the run.

The tool reads `agent.output_model` and `agent.verifier` at call time, so
callers that assign them after construction (e.g. the eval judge) work too.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from pydantic import ValidationError

from agent_harness.tooling.result import ToolResult

if TYPE_CHECKING:
    from agent_harness.agent import Agent

SUBMIT_RESULT = 'SubmitResult'

# None accepts the result; a string rejects it and becomes the model-facing reason.
# Takes Any so a verifier typed on the concrete model (e.g. `(r: StockPick) -> ...`) type-checks.
Verifier = Callable[[Any], str | None]


#     ================================
# --> Tool
#     ================================


def make_submit_result_tool(agent: Agent) -> dict[str, Any]:
    """Build the SubmitResult tool dict bound to `agent`.

    The parameters schema is `agent.output_model`'s JSON schema, so the
    model's fields become the tool's arguments.
    """
    output_model = agent.output_model

    if output_model is None:
        raise ValueError('make_submit_result_tool requires agent.output_model to be set')

    def submit_result(**fields: Any) -> ToolResult:
        model = agent.output_model
        assert model is not None  # registered only when output_model is set

        # Check 1: shape
        try:
            result = model.model_validate(fields)

        except ValidationError as e:
            return ToolResult(f'Invalid result, fix it and call {SUBMIT_RESULT} again:\n{e}', status='error')

        # Check 2: the caller's own rule (optional)
        if agent.verifier is not None:
            reason = agent.verifier(result)

            if reason is not None:
                return ToolResult(f'Rejected: {reason}\nKeep working, then call {SUBMIT_RESULT} again.', status='error')

        agent.submission = result

        return ToolResult('Accepted.', status='ok')

    return {
        'name': SUBMIT_RESULT,
        'description': (
            'Hand in your final answer. The task only ends when this call is accepted; '
            'replying with plain text does not finish it. If the result is rejected, '
            'read the reason, fix the problem, and call this again.'
        ),
        'parameters': output_model.model_json_schema(),
        'function': submit_result,
    }
