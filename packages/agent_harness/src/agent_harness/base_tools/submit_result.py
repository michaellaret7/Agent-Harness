"""Validate structured output before allowing an agent to finish.

SubmitResult checks output_model, then the optional verifier. Failures return
an error so the model can retry; acceptance stores agent.submission and ends
the loop. Both settings are read at call time.
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
