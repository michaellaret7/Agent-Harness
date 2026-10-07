"""Org tools attached to each member at registration, bound to that member's id."""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from agent_harness.tooling.decorator import Param, agent_tool
from agent_harness.tooling.result import ToolResult

from architectures.organization.models import Message, MessageLimitReached


#     ================================
# --> Org tools (bound per member at registration)
#     ================================


@agent_tool(name='GetOrgInfo')
def get_org_info(_org: Any, _self_id: uuid.UUID) -> ToolResult:
    """Return your organization's name, config variables (e.g. aum, sector), and every member as `role: uuid`, marking yourself."""
    config = [f'{key}: {value}' for key, value in _org.config.items()]

    members = [
        f"- {member.role}: {agent_id}{' (you)' if agent_id == _self_id else ''}"
        for agent_id, member in _org.agents.items()
    ]

    lines = [f'name: {_org.name}', *config, 'members:', *members]

    return ToolResult('\n'.join(lines), 'ok')


@agent_tool(name='SendMessage')
def send_message(
    recipient_id: Annotated[str, Param(description='UUID of the recipient agent (see GetOrgInfo).')],
    content: Annotated[str, Param(description='The message to deliver.')],
    _org: Any,
    _self_id: uuid.UUID,
) -> ToolResult:
    """Send a message to another agent in your organization.

    Delivery is asynchronous: the message is queued in the recipient's inbox
    and the recipient handles it once it is free. Any reply arrives later as a new
    message to you — do not wait for it in this turn.
    """
    try:
        target = uuid.UUID(recipient_id)
    except ValueError:
        return ToolResult(f'Invalid UUID: {recipient_id!r}', 'error')

    if not _org.has_agent(target):
        return ToolResult(f'No member with id {target}. Call GetOrgInfo.', 'error')

    try:
        _org.post(Message(sender_id=_self_id, recipient_id=target, content=content))
    except MessageLimitReached as exc:
        return ToolResult(f'{exc}. Finish your current task without sending more messages.', 'error')

    return ToolResult(f'Queued for {_org.agents[target].role} ({target}).', 'ok')
