"""Organization: an agent registry plus a FIFO message bus that delivers by calling `agent.run`."""
from __future__ import annotations

import uuid
from abc import ABC
from collections import deque
from typing import Any

from agent_harness.tooling.decorator import bind_tool

from architectures.organization.models import Member, Message
from architectures.organization.tools import get_org_info, list_org_members, send_message


#     ================================
# --> Organization
#     ================================


class Organization(ABC):
    """Abstract base: every org gets a bus, an agent registry, and a shared repo."""

    def __init__(self, name: str, goal: str, **config: Any):
        self.id = uuid.uuid4()
        self.name = name
        self.goal = goal  # appended to every member's system prompt at registration
        self.config = config # org-specific properties/variables
        self.message_bus: deque[Message] = deque()
        self.agents: dict[uuid.UUID, Member] = {}  # UUID -> agent instance + role

    # --- registration ---
    def register_agent(self, role: str, agent: Any) -> uuid.UUID:
        """Assign an ID, record the role, attach this member's org tools, and add the org prompt.

        The agent must not have run yet: the org block is written into its
        system prompt, which is fixed once a conversation starts.
        """

        if any(member.agent is agent for member in self.agents.values()):
            raise ValueError("Agent already registered in this org")

        agent_id = uuid.uuid4()

        # Prompt first: it fails fast on an agent that already ran, before anything is registered
        agent.extend_system_prompt(
            '<organization>\n'
            f'You are a member of the organization "{self.name}" with the role "{role}" (id {agent_id}).\n'
            f'Organization goal: {self.goal}\n'
            '</organization>'
        )

        self.agents[agent_id] = Member(agent=agent, role=role)

        # Org tools are bound to this member, so the model never passes its own id
        agent.add_tool(bind_tool(list_org_members, _org=self, _self_id=agent_id))
        agent.add_tool(bind_tool(get_org_info, _org=self))
        agent.add_tool(bind_tool(send_message, _org=self, _self_id=agent_id))

        return agent_id

    def get_agent(self, agent_id: uuid.UUID) -> Any:
        return self.agents[agent_id].agent

    def has_agent(self, agent_id: uuid.UUID) -> bool:
        return agent_id in self.agents

    # --- messaging ---
    def post(self, message: Message) -> None:
        """Queue a message on the bus. Delivery happens in `run_until_idle`."""
        if message.recipient_id is None:
            raise NotImplementedError('Broadcast delivery is not supported yet')

        self.message_bus.append(message)

    def _sender_label(self, sender_id: uuid.UUID) -> str:
        if sender_id == self.id:
            return f'org {self.name}'

        return f'{self.agents[sender_id].role} ({sender_id})'

    def run_until_idle(self, max_deliveries: int = 20) -> None:
        """Drain the bus FIFO, running each recipient on its message.

        Deliveries are serial, so a recipient is always free when its message
        is popped; anything it sends mid-run lands at the back of the queue.
        `max_deliveries` caps runaway ping-pong between agents.
        """
        deliveries = 0

        while self.message_bus:
            if deliveries >= max_deliveries:
                print(f'\n[bus] stopped after {max_deliveries} deliveries; {len(self.message_bus)} left queued')
                return

            message = self.message_bus.popleft()
            assert message.recipient_id is not None  # post() rejects broadcasts
            recipient = self.agents[message.recipient_id]

            prompt = f'[Message from {self._sender_label(message.sender_id)}]\n{message.content}'

            print(f'\n[bus] -> {recipient.role}')
            recipient.agent.run(prompt)

            deliveries += 1
