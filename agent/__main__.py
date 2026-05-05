"""REPL with tools enabled. Run with: uv run python -m agent"""
from __future__ import annotations

from datetime import datetime

from agent.agent import Agent


time_tool = {
    'name': 'get_current_time',
    'description': 'Return the current local date and time as an ISO-8601 string.',
    'parameters': {'type': 'object', 'properties': {}},
    'function': lambda: datetime.now().isoformat(timespec='seconds'),
}


def main() -> None:
    agent = Agent()
    agent.add_tool(**time_tool)

    while True:
        prompt = input('> ')
        if prompt == 'exit':
            break
        agent.run(prompt)
        print()


if __name__ == '__main__':
    main()
