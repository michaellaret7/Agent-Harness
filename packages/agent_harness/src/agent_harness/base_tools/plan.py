"""Replace the agent's working plan and publish successful updates to the sink.

Validate statuses and allow at most one in_progress item. Invalid input leaves
the plan unchanged. Valid input mutates the existing list and returns rendered
text. The hidden `_ctx` supplies the agent and sink.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from agent_harness.tooling.context import ToolContext
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.input_model import ToolInput
from agent_harness.tooling.result import ToolResult

GLYPHS = {
    'pending': '[ ]',
    'in_progress': '[-]',
    'completed': '[x]',
}


class PlanItem(BaseModel):
    """One plan step. Drives the nested item schema the LLM sees."""

    text: str = Field(description='Short description of the step.')
    status: Literal['pending', 'in_progress', 'completed']


class PlanInput(ToolInput):
    """Arguments of the Plan tool."""

    items: list[PlanItem] = Field(description=(
        'Full plan as a list of items. Each item is an object with '
        '"text" (string) and "status" ("pending" | "in_progress" | '
        '"completed"). The list replaces any existing plan.'
    ))

    @field_validator('items', mode='before')
    @classmethod
    def decode_items(cls, items: Any) -> Any:
        """Undo model double-encoding before validation.

        Some models stringify the whole `items` array, or each element, as JSON
        inside the tool-call arguments. The handler's single `json.loads` of the
        outer envelope leaves those inner strings intact. Parse them here so a
        correctly-intended plan isn't rejected as `Input should be a valid list`.
        A string that is not valid JSON raises, which is reported as a validation error.
        """
        if isinstance(items, str):
            items = json.loads(items)

        if isinstance(items, list):
            items = [json.loads(item) if isinstance(item, str) else item for item in items]

        return items

    @field_validator('items')
    @classmethod
    def allow_one_in_progress(cls, items: list[PlanItem]) -> list[PlanItem]:
        """Reject a plan with more than one in_progress item."""
        in_progress_count = sum(1 for item in items if item.status == 'in_progress')

        if in_progress_count > 1:
            raise ValueError(f'at most one item may be in_progress (got {in_progress_count})')

        return items


#     ================================
# --> Helper funcs
#     ================================


def render_plan(plan: list[dict]) -> str:
    """Render the plan as a markdown-ish checklist for the model to read back."""
    if not plan:
        return 'Plan is empty.'

    lines = [f'  {GLYPHS[item["status"]]} {item["text"]}' for item in plan]

    return f'Plan ({len(plan)} items):\n' + '\n'.join(lines)


#     ================================
# --> Tool
#     ================================


@agent_tool(name='Plan')
def plan(args: PlanInput, _ctx: ToolContext | None = None) -> ToolResult:
    """Replace the entire plan with the given items.

    Call this to create, update, reorder, or clear the plan. Each call is
    a full replacement — pass the complete desired plan every time. To
    clear the plan, pass an empty list.

    Each item is an object with two fields:
      - `text`: short description of the step
      - `status`: one of "pending", "in_progress", "completed"

    At most one item may have status "in_progress" at a time. Returns the
    rendered plan in a ToolResult after the update; returns error status (and leaves
    the plan unchanged) on invalid input.
    """
    if _ctx is None:
        return ToolResult('error: Plan tool called without a run context', status='error')

    current = _ctx.agent.plan

    current.clear()
    current.extend({'text': item.text, 'status': item.status} for item in args.items)

    _ctx.sink.on_plan_update(current)

    return ToolResult(render_plan(current), status='ok')
