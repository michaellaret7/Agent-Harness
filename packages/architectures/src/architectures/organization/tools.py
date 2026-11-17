"""Org tools attached to each member at registration, bound to that member's id."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from agent_harness.tooling.definition import ToolInput, agent_tool
from agent_harness.tooling.result import ToolResult

from architectures.organization.models import Message, MessageLimitReached


class SendMessageInput(ToolInput):
    """Arguments of the SendMessage tool."""

    recipient_id: int = Field(description="The recipient's `id` from GetOrgInfo, not a number that appears in a role name.")
    content: str = Field(description='The message to deliver.')


class SendMessageBlastInput(ToolInput):
    """Arguments of the SendMessageBlast tool."""

    recipient_ids: list[int] = Field(description='The `id` of every recipient, from GetOrgInfo.')
    content: str = Field(description='The message to deliver to every recipient.')


#     ================================
# --> Org tools (bound per member at registration)
#     ================================


@agent_tool(name='GetOrgInfo')
def get_org_info(_org: Any, _self_id: int) -> ToolResult:
    """Return your organization's name, config variables (e.g. aum, sector), and every member as `id N · role`, marking yourself."""
    config = [f'{key}: {value}' for key, value in _org.config.items()]

    members = [
        f"- id {agent_id} · {member.role}{' (you)' if agent_id == _self_id else ''}"
        for agent_id, member in _org.agents.items()
    ]

    lines = [f'name: {_org.name}', *config, 'members:', *members]

    return ToolResult('\n'.join(lines), 'ok')


@agent_tool(name='SendMessage')
def send_message(args: SendMessageInput, _org: Any, _self_id: int) -> ToolResult:
    """Send a message to another agent in your organization.

    Delivery is asynchronous: the message is queued in the recipient's inbox
    and the recipient handles it once it is free. Any reply arrives later as a new
    message to you — do not wait for it in this turn.
    """
    recipient_id = args.recipient_id

    if not _org.has_agent(recipient_id):
        return ToolResult(f'No member with id {recipient_id}. Call GetOrgInfo.', 'error')

    try:
        _org.post([Message(sender_id=_self_id, recipient_id=recipient_id, content=args.content)])
    except MessageLimitReached as exc:
        return ToolResult(f'{exc}. Finish your current task without sending more messages.', 'error')

    return ToolResult(f'Queued for {_org.agents[recipient_id].role} (id {recipient_id}).', 'ok')


@agent_tool(name='SendMessageBlast')
def send_message_blast(args: SendMessageBlastInput, _org: Any, _self_id: int) -> ToolResult:
    """Send the same message to several agents in your organization at once.

    All recipients get it or none do. Each recipient counts as one message
    against the org's budget. Replies arrive later as separate messages to you.
    """
    unique_ids = list(dict.fromkeys(args.recipient_ids))  # dedupe, keep order
    unknown = [recipient_id for recipient_id in unique_ids if not _org.has_agent(recipient_id)]

    if not unique_ids:
        return ToolResult('recipient_ids is empty.', 'error')

    if unknown:
        return ToolResult(f'No members with ids {unknown}. Call GetOrgInfo. Nothing was sent.', 'error')

    if _self_id in unique_ids:
        return ToolResult('You cannot message yourself; remove your own id. Nothing was sent.', 'error')

    messages = [Message(sender_id=_self_id, recipient_id=recipient_id, content=args.content) for recipient_id in unique_ids]

    try:
        _org.post(messages)
    except MessageLimitReached as exc:
        return ToolResult(f'{exc}. Nothing was sent. Finish your current task without sending more messages.', 'error')

    recipients = ', '.join(f'{_org.agents[recipient_id].role} (id {recipient_id})' for recipient_id in unique_ids)

    return ToolResult(f'Queued for {recipients}.', 'ok')
