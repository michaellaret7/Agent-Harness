"""Organization: an agent registry where each member works its own inbox in parallel via `agent.run`."""
from __future__ import annotations

import os
import threading
import uuid
from typing import Any

from agent_harness.sinks import LogSink
from agent_harness.tooling.decorator import bind_tool

from architectures.organization.models import Member, Message, MessageLimitReached
from architectures.organization.tools import get_org_info, send_message


#     ================================
# --> Organization
#     ================================


class Organization:
    """An agent registry plus one inbox per member; members talk via SendMessage."""

    def __init__(self, name: str, goal: str, **config: Any):
        self.id = uuid.uuid4()
        self.name = name
        self.goal = goal  # appended to every member's system prompt at registration
        self.config = config # org-specific properties/variables
        self.agents: dict[uuid.UUID, Member] = {}  # UUID -> agent instance + role

        # One condition guards delivery bookkeeping shared across member worker threads
        self._cond = threading.Condition()
        self._outstanding = 0  # queued + running messages; 0 means the org is idle
        self._sent = 0
        self._max_messages = 0
        self._errors: list[Exception] = []
        self._cancel = threading.Event()  # set on Ctrl+C: running agents stop, queued messages are skipped

    # --- internal helpers ---
    def _sender_label(self, sender_id: uuid.UUID) -> str:
        if sender_id == self.id:
            return f'org {self.name}'

        return f'{self.agents[sender_id].role} ({sender_id})'

    def _stop_workers(self, workers: list[threading.Thread]) -> None:
        """Queue a stop sentinel behind each inbox's remaining messages and wait for every worker to exit."""
        for member in self.agents.values():
            member.inbox.put(None)

        for worker in workers:
            worker.join()

    def _worker(self, member: Member) -> None:
        """Process this member's inbox one message at a time until the stop sentinel."""
        while (message := member.inbox.get()) is not None:
            # After a cancel, drain without running so the worker reaches its sentinel quickly
            if self._cancel.is_set():
                with self._cond:
                    self._outstanding -= 1

                continue

            prompt = f'[Message from {self._sender_label(message.sender_id)}]\n{message.content}'

            print(f'\n[bus] -> {member.role}')

            try:
                # run the agent cls's run method with the message from the bus to the agent
                member.agent.run(prompt, sink=member.sink, cancel_event=self._cancel)
            except Exception as exc:
                # Keep the worker alive, or this inbox would never drain and `run` would hang
                with self._cond:
                    self._errors.append(exc)
            finally:
                with self._cond:
                    self._outstanding -= 1
                    self._cond.notify_all()

    # get the agent object from the organization
    def get_agent(self, agent_id: uuid.UUID) -> Any:
        return self.agents[agent_id].agent

    # check if the agent is in the organization
    def has_agent(self, agent_id: uuid.UUID) -> bool:
        return agent_id in self.agents

    # --- register an agent with the organization ---
    def register_agent(self, role: str, agent: Any) -> uuid.UUID:
        """Assign an ID, record the role, attach this member's org tools, and add the org prompt.

        The agent must not have run yet: the org block is written into its
        system prompt, which is fixed once a conversation starts.
        """

        if any(member.agent is agent for member in self.agents.values()):
            raise ValueError("Agent already registered in this org")

        # Langfuse is the engine's ambient sink, composed into every run when this key is set.
        # Fail fast so no member ever runs untraced.
        if not os.environ.get('LANGFUSE_PUBLIC_KEY'):
            raise RuntimeError('register_agent: LANGFUSE_PUBLIC_KEY is not set; org members must be traced')

        agent_id = uuid.uuid4()

        # Prompt first: it fails fast on an agent that already ran, before anything is registered
        agent.extend_system_prompt(
            '<organization>\n'
            f'You are a member of the organization "{self.name}" with the role "{role}" (id {agent_id}).\n'
            f'Organization goal: {self.goal}\n'
            '</organization>'
        )

        self.agents[agent_id] = Member(
            agent=agent, 
            role=role, 
            sink=LogSink(f'{self.name}_{role}')
        )

        # Org tools are bound to this member, so the model never passes its own id
        agent.add_tool(bind_tool(get_org_info, _org=self, _self_id=agent_id))
        agent.add_tool(bind_tool(send_message, _org=self, _self_id=agent_id))

        return agent_id

    # --- messaging ---
    def post(self, message: Message) -> None:
        """Queue a message in the recipient's inbox; raises MessageLimitReached once the run's budget is spent."""
        
        if message.recipient_id is None:
            raise NotImplementedError('Broadcast delivery is not supported yet')

        # Count and enqueue under the lock, so `run` never sees 0 while a message is in flight
        # and no message lands behind a stop sentinel after a cancel
        with self._cond:
            if self._cancel.is_set():
                raise MessageLimitReached('Org run was cancelled')

            if self._sent >= self._max_messages:
                raise MessageLimitReached(f'Org message limit ({self._max_messages}) reached')

            self._sent += 1
            self._outstanding += 1
            # put the message in the agent recipients inbox using the 
            # message's recipient_id value
            self.agents[message.recipient_id].inbox.put(message)

    def run(self, kickoff: list[tuple[uuid.UUID, str]], max_messages: int = 20) -> None:
        """Send the kickoff messages and let members work in parallel until the org is idle.

        Each member processes its own inbox one message at a time, so a busy
        agent's new messages wait their turn while idle agents start at once.
        `max_messages` caps every message in this run, kickoff included; past
        it, SendMessage returns an error so agents wrap up and the org drains.
        """

        # flag any recipients that are not members of the organization
        unknown = [recipient_id for recipient_id, _ in kickoff if not self.has_agent(recipient_id)]

        if unknown:
            raise ValueError(f'Kickoff recipients are not members: {unknown}')

        if len(kickoff) > max_messages:
            raise ValueError(f'{len(kickoff)} kickoff messages exceed max_messages={max_messages}')

        self._sent = 0
        self._max_messages = max_messages
        self._cancel.clear()

        # send the kickoff messages to the recipients using the org's post method
        for recipient_id, content in kickoff:
            self.post(Message(sender_id=self.id, recipient_id=recipient_id, content=content))

        # start the worker threads for each member
        workers = [
            threading.Thread(target=self._worker, args=(member,), name=f'{self.name}:{member.role}')
            for member in self.agents.values()
        ]

        for worker in workers:
            worker.start()

        try:
            with self._cond:
                # Timed wait: an untimed one blocks Ctrl+C on Windows until the org goes idle
                while not self._cond.wait_for(lambda: self._outstanding == 0, timeout=0.1):
                    pass

        except KeyboardInterrupt:
            # Under the lock, so no post can slip a message in behind the stop sentinels
            with self._cond:
                self._cancel.set()

            raise

        finally:
            # Always stop the workers; otherwise they block on their inboxes and the process never exits
            self._stop_workers(workers)

        if self._errors:
            errors, self._errors = self._errors, []
            raise errors[0]
