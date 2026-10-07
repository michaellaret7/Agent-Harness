"""Data records shared across the organization architecture."""
from __future__ import annotations

import queue
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent_harness.sinks import Sink


@dataclass
class Message:
    sender_id: uuid.UUID
    recipient_id: uuid.UUID | None  # None = broadcast
    content: Any
    expects_reply: bool = True  # automatic replies use False; explicit sends remain possible


@dataclass
class Member:
    agent: Any
    role: str
    sink: Sink  # presentation sink for every delivery; Langfuse is composed on top by the engine
    inbox: queue.Queue[Message | None] = field(default_factory=queue.Queue)  # None = stop sentinel; drained only by this member's worker this is the agent's inbox


class MessageLimitReached(RuntimeError):
    """Raised by `Organization.post` once a run has used its message budget."""
