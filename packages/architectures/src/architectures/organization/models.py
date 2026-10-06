"""Data records shared across the organization architecture."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
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
