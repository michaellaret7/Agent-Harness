from __future__ import annotations

# When run directly (`python agent/agent.py`), Python only puts agent/ on
# sys.path, so `import agent.loop` fails. Add the project root so both
# `python agent/agent.py` and `python -m agent.agent` work.
if __package__ in (None, ''):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from agent.client import build_client
from agent.loop import execution_loop
from agent.tool_handler import ToolHandler
from agent.tools import calculator, file_architecture, read_file, weather

load_dotenv()


class Agent:
    def __init__(
        self,
        provider: str = 'vllm',
        model: str | None = None,
    ) -> None:

        self.client, self.model = build_client(provider, model)
        self.provider = provider
        self.messages: list[dict] = []

        # Tool registry
        self.tools: list[dict[str, Any]] = []
        self.tool_functions: dict[str, Callable] = {}

        self.tool_handler = ToolHandler(self)

        self.system_prompt = (Path(__file__).parent / 'context' / 'system_prompt.md').read_text(encoding='utf-8').strip()
        self.memory = (Path(__file__).parent / 'context' / 'memory.md').read_text(encoding='utf-8').strip()

        # ---- Register default tools ---- #
        self.add_tool(**weather.tool)
        self.add_tool(**read_file.tool)
        self.add_tool(**calculator.tool)
        self.add_tool(**file_architecture.tool)

        self.build_initial_context()

    def add_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        function: Callable,
    ) -> None:
        """Register a tool. No-op if already registered."""
        if name in self.tool_functions:
            return

        self.tools.append({
            'type': 'function',
            'function': {
                'name': name,
                'description': description,
                'parameters': parameters,
            },
        })

        self.tool_functions[name] = function

    def build_initial_context(self) -> None:
        if self.memory:
            self.messages.append({'role': 'system', 'content': self.system_prompt + '\n' + self.memory})
        else:
            self.messages.append({'role': 'system', 'content': self.system_prompt})

    def run(self, prompt: str) -> str:
        self.messages.append({'role': 'user', 'content': prompt})
        return execution_loop(self, model=self.model, stream=True)


if __name__ == '__main__':
    agent = Agent(provider='vllm')

    print(agent.messages)

    while True:
        prompt = input('[USER] -->  ')
        if prompt == 'exit':
            break
        agent.run(prompt)
        print()
