"""Organization architecture: agents registered under one org, talking over a message bus.

Each registered agent gets an ExecuteCode whose kernel holds an `org` object
bound to its id (`org.members()`, `org.config()`, `org.send_message(...)`).
Sends are queued on the org's bus;
`run_until_idle` delivers them FIFO by calling `agent.run(message)` on the
recipient.
"""

from architectures.organization.models import Member, Message
from architectures.organization.org import Organization

__all__ = ['Member', 'Message', 'Organization']
