"""Plan: create, update, or replace the agent's working plan.

The agent owns `self.plan: list[dict]` (one canonical list, mutated in
place). This tool is the only interface the model has to that list —
each call is a *full replacement*: the model passes the complete desired
plan, the tool clears and refills the list, then returns the rendered
state as the tool-result string.

Validation is hard-fail: invalid `status` values or more than one
`in_progress` item return `error: ...` and leave the plan unchanged.

`self.plan` is bound via `functools.partial` at registration time
(see `agent.py`), so the underscore-prefixed `_plan` parameter is hidden
from the generated JSON Schema and never seen by the LLM.
"""
from __future__ import annotations

from functools import partial
from typing import Annotated, Any

from agent.decorator import Param, agent_tool

ALLOWED_STATUSES = ('pending', 'in_progress', 'completed')

GLYPHS = {
    'pending': '[ ]',
    'in_progress': '[-]',
    'completed': '[x]',
}


#     ================================
# --> Helper funcs
#     ================================


def _render(plan: list[dict]) -> str:
    """Render the plan as a markdown-ish checklist for the model to read back."""
    if not plan:
        return 'Plan is empty.'

    lines = [f'  {GLYPHS[item["status"]]} {item["text"]}' for item in plan]

    return f'Plan ({len(plan)} items):\n' + '\n'.join(lines)


def _validate(items: list[dict]) -> str | None:
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
        list[dict],
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

    err = _validate(items)

    if err is not None:
        return f'error: {err}'

    _plan.clear()
    _plan.extend({'text': item['text'], 'status': item['status']} for item in items)

    return _render(_plan)


def bind_plan(plan_state: list[dict]) -> dict[str, Any]:
    """Build a Plan tool dict bound to the given plan list.

    The schema and arg-validation wrapper come from the `@agent_tool`
    decorator on `plan`; this swaps in a runtime `function` with `_plan`
    pre-injected via `partial`. The list is passed by reference, so the
    tool mutates the live `Agent`-owned container.

    Named `bind_plan` rather than `plan_loader` because, unlike
    `tool_loader` and `skill_loader` (which build tools whose underlying
    function is `load_tool` / `load_skill`), the Plan tool's function
    doesn't *load* anything — it mutates the bound state. The suffix
    `_loader` in those other factories tracks the function name, not the
    factory pattern.
    """
    tool_dict = dict(plan.tool)
    tool_dict['function'] = partial(plan.tool['function'], _plan=plan_state)

    return tool_dict
