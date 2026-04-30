from __future__ import annotations

# When run directly (`python agent/agent.py`), Python only puts agent/ on
# sys.path, so `import agent.loop` fails. Add the project root so both
# `python agent/agent.py` and `python -m agent.agent` work.
if __package__ in (None, ''):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import Any

from openai import OpenAI

from agent.loop import execution_loop
from agent.tool_handler import ToolHandler
from agent.tools import calculator, file_architecture, read_file, weather


class Agent:
    def __init__(
        self,
        tools: list[dict[str, Any]],
        system_prompt: str = '',
    ) -> None:

        self.client = OpenAI(
            base_url='http://localhost:8000/v1',
            api_key='placeholder',
        )

        self.tools = tools
        self.handler = ToolHandler()
        self.messages: list[dict] = []
        self.system_prompt = system_prompt
        
        # Registert tools with the agent
        for tool in self.tools:
            self.handler.register(tool)

    def run(self, prompt: str) -> str:
        self.messages.append({'role': 'user', 'content': prompt})
        return execution_loop(self.client, self.handler, self.messages)


if __name__ == '__main__':
    agent = Agent(
        system_prompt='You are a helpful assistant that can answer questions and help with tasks. You are extremely funny and witty. You are also a bit of a nerd.',
        tools=[weather.tool, read_file.tool, calculator.tool, file_architecture.tool]
    )

    while True:
        prompt = input('> ')
        if prompt == 'exit':
            break
        agent.run(prompt)
        print()
