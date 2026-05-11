from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from agent.client import build_client
from agent.loop import execution_loop
from agent.tool_handler import ToolHandler
from tools.base import bash, edit, glob, grep, read, search, tree, write
from tui.sink import Sink

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

        # ---- Register base tools ---- #
        self.add_tool(**bash.tool)
        self.add_tool(**read.tool)
        self.add_tool(**write.tool)
        self.add_tool(**edit.tool)
        self.add_tool(**glob.tool)
        self.add_tool(**grep.tool)
        self.add_tool(**tree.tool)
        self.add_tool(**search.tool)

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
        self.system_prompt += f'\nCurrent date: {datetime.now().strftime("%A, %B %d, %Y")}'
        
        if self.memory:
            self.messages.append({'role': 'system', 'content': self.system_prompt + '\n' + self.memory})
        else:
            self.messages.append({'role': 'system', 'content': self.system_prompt})

    def run(
        self,
        prompt: str,
        sink: Sink | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:

    
        self.messages.append({'role': 'user', 'content': prompt})

        return execution_loop(
            self,
            model=self.model,
            stream=True,
            sink=sink,
            cancel_event=cancel_event,
        )


