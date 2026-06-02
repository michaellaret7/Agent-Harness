"""Plan: create, update, or replace the agent's working plan.

The agent owns `self.plan: list[dict]` (one canonical list, mutated in
place). This tool is the only interface the model has to that list —
each call is a *full replacement*: the model passes the complete desired
plan, the tool clears and refills the list, then returns the rendered
state as the tool-result string.

Validation is hard-fail: invalid `status` values or more than one
`in_progress` item return `error: ...` and leave the plan unchanged.

`self.plan` is injected into the hidden `_plan` parameter via `bind_tool`
at registration time (see `agent.py`), so that underscore-prefixed parameter
is hidden from the generated JSON Schema and never seen by the LLM.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal, TypedDict

from agent_harness.decorator import Param, agent_tool

ALLOWED_STATUSES = ('pending', 'in_progress', 'completed')

GLYPHS = {
    'pending': '[ ]',
    'in_progress': '[-]',
    'completed': '[x]',
}


class PlanItem(TypedDict):
    """One plan step. Drives the nested item schema the LLM sees."""

    text: Annotated[str, Param(description='Short description of the step.')]
    status: Literal['pending', 'in_progress', 'completed']


#     ================================
# --> Helper funcs
#     ================================


def _render(plan: list[dict]) -> str:
    """Render the plan as a markdown-ish checklist for the model to read back."""
    if not plan:
        return 'Plan is empty.'

    lines = [f'  {GLYPHS[item["status"]]} {item["text"]}' for item in plan]

    return f'Plan ({len(plan)} items):\n' + '\n'.join(lines)


def _coerce(items: Any) -> tuple[list, str | None]:
    """Undo model double-encoding before validation.

    Some models stringify the whole `items` array, or each element, as JSON
    inside the tool-call arguments. The handler's single `json.loads` of the
    outer envelope leaves those inner strings intact. Parse them here so a
    correctly-intended plan isn't rejected as `item N is not an object`.
    Returns (coerced_list, None) on success, or ([], error_message) on failure.
    """
    if isinstance(items, str):
        try:
            items = json.loads(items)

        except (ValueError, TypeError) as e:
            return [], f'items was a string but not valid JSON: {e}'

    if not isinstance(items, list):
        return [], f'items must be a list (got {type(items).__name__})'

    coerced: list = []

    for idx, item in enumerate(items):
        if isinstance(item, str):
            try:
                item = json.loads(item)

            except (ValueError, TypeError) as e:
                return [], f'item {idx} was a string but not valid JSON: {e}'

        coerced.append(item)

    return coerced, None


def _validate(items: list) -> str | None:
    """Check item shape + at-most-one-in_progress. Return an error string or None."""
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            return f'item {idx} is not an object (got {type(item).__name__})'

        if 'text' not in item:
            return f'item {idx} missing required field "text"'

        if 'status' not in item:
            return f'item {idx} missing required field "status"'

        if not isinstance(item['text'], str):
            return f'item {idx} field "text" must be a string'

        if item['status'] not in ALLOWED_STATUSES:
            return (
                f'item {idx} has invalid status {item["status"]!r}; '
                f'must be one of {list(ALLOWED_STATUSES)}'
            )

    in_progress_count = sum(1 for item in items if item['status'] == 'in_progress')

    if in_progress_count > 1:
        return f'at most one item may be in_progress (got {in_progress_count})'

    return None


#     ================================
# --> Tool
#     ================================


@agent_tool(name='Plan', deferred=True)
def plan(
    items: Annotated[
        list[PlanItem],
        Param(description=(
            'Full plan as a list of items. Each item is an object with '
            '"text" (string) and "status" ("pending" | "in_progress" | '
            '"completed"). The list replaces any existing plan.'
        )),
    ],
    _plan: list[dict] | None = None,
) -> str:
    """Replace the entire plan with the given items.

    Call this to create, update, reorder, or clear the plan. Each call is
    a full replacement — pass the complete desired plan every time. To
    clear the plan, pass an empty list.

    Each item is an object with two fields:
      - `text`: short description of the step
      - `status`: one of "pending", "in_progress", "completed"

    At most one item may have status "in_progress" at a time. Returns the
    rendered plan after the update; returns an error string (and leaves
    the plan unchanged) on invalid input.
    """
    if _plan is None:
        return 'error: Plan tool not bound to an agent state'

    coerced, err = _coerce(items)

    if err is not None:
        return f'error: {err}'

    err = _validate(coerced)

    if err is not None:
        return f'error: {err}'

    _plan.clear()
    _plan.extend({'text': item['text'], 'status': item['status']} for item in coerced)

    return _render(_plan)
