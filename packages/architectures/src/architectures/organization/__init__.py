"""Organization architecture: agents registered under one org, talking via per-member inboxes.

Each registered agent gets an ExecuteCode whose kernel holds an `org` object
bound to its id (`org.members()`, `org.config()`, `org.send_message(...)`).
Sends are queued in the recipient's inbox; `run(kickoff)` starts one worker
per member that calls `agent.run(message)` on its inbox, so members work in
parallel while each handles its own messages one at a time.
"""

from architectures.organization.models import Member, Message, MessageLimitReached
from architectures.organization.org import Organization

__all__ = ['Member', 'Message', 'MessageLimitReached', 'Organization']
