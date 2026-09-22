# agent-harness

A reusable Python harness for streaming, tool-calling agents. Applications import the engine and supply their own tools, instructions, and skills.

This repository is a **uv workspace** with two distributable libraries:

- **agent-harness** (`agent_harness`): execution loop, tool registration and dispatch, hooks, gates, skills, subagents, and output sinks.
- **tui**: an optional prompt_toolkit + Rich terminal frontend that consumes the engine.

Application-specific agents belong in separate projects. The engine can be installed and used without the TUI.

## Features

- Streaming text and reasoning output, with fragmented tool-call reassembly.
- Tool registration through dictionaries or the `@agent_tool` decorator.
- Deferred tools and Markdown-defined skills supplied by consuming applications.
- Lifecycle hooks for observation and gates for allowing, denying, or rewriting tool calls.
- Parallel execution of tools marked `safe_parallel=True`.
- Cooperative cancellation through a `threading.Event`.
- Subagent delegation and optional Pydantic structured output.
- Output sinks for stdout, logging, Langfuse tracing, and the optional TUI.
- Built-in web search/extraction, file reading, skill loading, deferred-tool loading, and planning.

## Use from another project

Python 3.12 and 3.13 are supported. The client currently supports OpenRouter and hosted vLLM endpoints using the OpenAI-compatible chat-completions API.

Install a built engine wheel into the consuming project:

```bash
uv add /path/to/agent_harness/dist/agent_harness-0.1.0-py3-none-any.whl
```

Alternatively, declare a Git dependency, replacing the example host with this repository's location:

```toml
[project]
dependencies = [
    "agent-harness @ git+https://your-host/agent_harness.git#subdirectory=packages/agent_harness",
]
```

Interactive applications can add the TUI wheel or Git dependency alongside the engine:

```toml
[project]
dependencies = [
    "agent-harness @ git+https://your-host/agent_harness.git#subdirectory=packages/agent_harness",
    "tui @ git+https://your-host/agent_harness.git#subdirectory=packages/tui",
]
```

## Programmatic use

The consuming application owns configuration bootstrap. The engine reads environment variables but does not load a `.env` file automatically.

For the examples below, create a `.env` file next to your script:

```dotenv
OPENROUTER_API_KEY=your-api-key
OPENROUTER_API_URL=https://openrouter.ai/api/v1
```

Replace `your-model-id` with the OpenRouter model you want to use.

### Run just the agent

Save this as `run_agent.py`:

```python
from dotenv import load_dotenv
from agent_harness.agent import Agent


if __name__ == "__main__":
    load_dotenv()
    agent = Agent(
        provider="openrouter",
        model="your-model-id",
        system="You are a helpful assistant.",
    )
    result = agent.run("Explain how solar panels work.")
```

To try it from this repository, save the script in the repository root and run:

```bash
uv run --package agent-harness python run_agent.py
```

The response streams to stdout and is also returned as `result`. Pass a custom `sink` to change where output goes. An optional `cancel_event: threading.Event` requests cancellation. A task can also be supplied at construction with `Agent(task=...)` and executed with `run()`.

### Configuration

- **OpenRouter:** pass `provider="openrouter"` and an explicit model. Set `OPENROUTER_API_KEY` and `OPENROUTER_API_URL=https://openrouter.ai/api/v1`.
- **Hosted vLLM:** pass `provider="vllm"`. Set `VLLM_API_URL` and either pass a model or set `VLLM_MODEL`.
- **Web tools:** set `PARALLEL_API_KEY` to use the built-in web search and extraction tools.
- **Tracing:** setting `LANGFUSE_PUBLIC_KEY` with the corresponding secret enables Langfuse instrumentation and sink composition. See `.env.example` for the available settings.

### Custom tools and skills

Define application-specific tools in the consuming project and pass them to the engine:

```python
from agent_harness.agent import Agent
from agent_harness.decorator import agent_tool

@agent_tool(name="Echo")
def echo(text: str) -> str:
    """Echo text back unchanged."""
    return text

agent = Agent(tools=[echo], system="You are a concise assistant.")
```

Tool dictionaries with `name`, `description`, `parameters`, and `function` are also supported. Registering a duplicate name keeps the existing tool and emits a warning. Tools passed to the constructor are added to the built-in tools.

For application-owned skills, pass `domain_root=Path(...)`; the harness discovers `<domain_root>/skills/`. The supplied `system` text is appended to the base prompt. This repository supplies the loading mechanism; consuming applications own their skill content.

### Optional terminal frontend

A consuming application can attach the TUI to its agent. Save this as `run_tui.py`, using the same `.env` settings above:

```python
import asyncio

from dotenv import load_dotenv
from agent_harness.agent import Agent
from tui.app import TUIApp


async def main():
    load_dotenv()

    agent = Agent(
        provider="openrouter",
        model="your-model-id",
        system="You are a helpful assistant.",
        # tools=[your_custom_tool],
    )

    await TUIApp(agent).run_async()


if __name__ == "__main__":
    asyncio.run(main())
```

To try it from this repository, save the script in the repository root and run:

```bash
uv run --package tui python run_tui.py
```

The frontend provides streaming display, tool history, usage status, scrolling, and cancellation controls. Both examples use the harness's built-in tools; add your own through `tools=[...]`.

In a separate project with the libraries installed, run `uv run python run_agent.py` or `uv run python run_tui.py` without the workspace-specific `--package` option.

## Repository layout

```text
agent_harness/
├── pyproject.toml                   # virtual workspace root
├── README.md
└── packages/
    ├── agent_harness/
    │   ├── pyproject.toml           # distributable engine
    │   └── src/agent_harness/
    │       ├── agent.py             # agent configuration and state
    │       ├── client.py            # provider client construction
    │       ├── loop.py              # streaming execution loop
    │       ├── tool_handler.py      # tool dispatch
    │       ├── decorator.py         # tool schema generation and binding
    │       ├── hooks.py
    │       ├── gates.py
    │       ├── sub_agent.py
    │       ├── messages.py
    │       ├── skills.py
    │       ├── usage.py
    │       ├── base_tools/
    │       ├── context/             # base system prompt
    │       ├── sinks/
    │       └── tests/               # live development demos
    └── tui/
        ├── pyproject.toml           # optional frontend library
        └── src/tui/
            ├── app.py
            ├── sink.py
            ├── history.py
            ├── keybindings.py
            ├── sprites.py
            ├── cells/
            └── panels/
```

The dependency direction is `tui → agent-harness`. External applications depend on the engine and optionally the frontend.

## Development and builds

The repository pins Python 3.12 for local development.

```bash
uv sync --all-packages
cp .env.example .env
# Edit .env with credentials for any live development runs.
```

Build the distributable engine, or both libraries:

```bash
uv build --package agent-harness
uv build --all-packages
```

Build output goes to `dist/`. The workspace root is not itself a distributable package.

A headless development entry point exercises the base engine:

```bash
uv run --package agent-harness python -m agent_harness
```

It uses the model configured in `agent_harness/__main__.py`. Live demos under `agent_harness/tests/` demonstrate hooks, gates, and subagent delegation; they require configured providers and can incur API usage.

## Execution flow

1. Stream a completion and send content/reasoning deltas to the sink.
2. Reassemble tool-call fragments by index.
3. Execute requested tools through `ToolHandler` and append their results to message history.
4. Repeat until the model returns an answer without tool calls, cancellation is requested, or `max_iters` is reached.

Reasoning is surfaced to sinks but is not appended to conversation history.
