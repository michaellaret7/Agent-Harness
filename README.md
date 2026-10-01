# agent-harness

A reusable Python harness for streaming, tool-calling agents. Applications import the engine and supply their own tools and instructions.

This repository is a **uv workspace** with two distributable libraries:

- **agent-harness** (`agent_harness`): execution loop, tool registration and dispatch, hooks, gates, subagents, and output sinks.
- **tui**: an optional prompt_toolkit + Rich terminal frontend that consumes the engine.

Application-specific agents belong in separate projects. The engine can be installed and used without the TUI.

## Features

- Streaming text and reasoning output, with fragmented tool-call reassembly.
- Tool registration through dictionaries or the `@agent_tool` decorator.
- Deferred tools supplied by consuming applications.
- Lifecycle hooks for observation and gates for allowing, denying, or rewriting tool calls.
- Parallel execution of tools marked `safe_parallel=True`.
- Cooperative cancellation through a `threading.Event`.
- Subagent delegation and optional Pydantic structured output.
- Output sinks for stdout, logging, Langfuse tracing, and the optional TUI.
- Built-in web search/extraction, deferred-tool loading, and planning.

## Use from another project

Python 3.12 and 3.13 are supported. The client currently supports OpenRouter and hosted vLLM endpoints using the OpenAI-compatible chat-completions API.

Install a built engine wheel into the consuming project:

```bash
uv add /path/to/agent_harness/dist/agent_harness-1.0.0-py3-none-any.whl
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
from agent_harness import Agent


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

### Custom tools

Define application-specific tools in the consuming project and pass them to the engine:

```python
from agent_harness import Agent
from agent_harness.tooling.decorator import agent_tool
from agent_harness.tooling.result import ToolResult

@agent_tool(name="Echo")
def echo(text: str) -> ToolResult:
    """Echo text back unchanged."""
    return ToolResult(text, status='ok')

agent = Agent(tools=[echo], system="You are a concise assistant.")
```

Tool dictionaries with `name`, `description`, `parameters`, and `function` are also supported. Registering a duplicate name keeps the existing tool and emits a warning. Tools passed to the constructor are added to the built-in tools.

Every tool callable must return `ToolResult(payload: str, status='ok' | 'error')`.
Status is independent of payload text; exceptions are converted to error results
by the handler. Version 1.0 removes plain-value tool returns and the `error:`
prefix convention for detecting failures. See [the tool result contract](docs/tools/results.md)
for migration and batch-result semantics.

The supplied `system` text is appended to the base prompt.

### Optional terminal frontend

A consuming application can attach the TUI to its agent. Save this as `run_tui.py`, using the same `.env` settings above:

```python
import asyncio

from dotenv import load_dotenv
from agent_harness import Agent
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
├── tests/                          # executable integration checks
├── examples/
│   └── stock_data_analysis.py      # live stock-data and screening demo
├── docs/
│   └── tools/code_execution.md
└── packages/
    ├── agent_harness/
    │   ├── pyproject.toml           # distributable engine
    │   └── src/agent_harness/
    │       ├── agent.py             # agent configuration and state
    │       ├── client.py            # provider client construction
    │       ├── loop.py              # streaming execution loop
    │       ├── hooks.py
    │       ├── gates.py
    │       ├── sub_agent.py
    │       ├── subagent_config.py   # shared subagent configuration
    │       ├── messages.py
    │       ├── usage.py
    │       ├── tooling/            # infrastructure shared by tools
    │       │   ├── decorator.py    # tool schema generation and binding
    │       │   ├── handler.py      # tool dispatch
    │       │   └── result.py       # tool result contract
    │       ├── base_tools/
    │       │   ├── code_execution/
    │       │   │   ├── tool.py      # ExecuteCode tool and factory
    │       │   │   ├── sandbox.py   # parent-side process management
    │       │   │   ├── kernel.py    # child-side Python execution
    │       │   │   └── screening.py # optional code screening
    │       │   ├── deploy_subagent.py
    │       │   ├── extract.py
    │       │   ├── load_tool.py
    │       │   ├── plan.py
    │       │   └── search.py
    │       ├── context/             # base system prompt
    │       └── sinks/
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

Code execution and subagent configuration use these imports:

```python
from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool
from agent_harness.subagent_config import SubAgentConfig
```

These replace the former `base_tools.execute_code` and `base_tools.deploy_subagent.SubAgentConfig` import paths. Consumers must update their imports; the removed code-execution modules have no compatibility shims. Treat these path changes as a breaking change when publishing the next engine release.

Tool infrastructure now lives in `agent_harness.tooling`: import `agent_tool`, `Param`, and `bind_tool` from `tooling.decorator`, `ToolHandler` from `tooling.handler`, and `ToolResult` from `tooling.result`. These replace the former top-level `decorator`, `tool_handler`, and `tool_result` modules and are also breaking import-path changes. `base_tools/` holds the actual tools offered to the model.

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

It uses the model configured in `agent_harness/__main__.py`. The live example in `examples/stock_data_analysis.py` requires OpenRouter and FMP credentials and can incur API usage:

```bash
uv run --package agent-harness python examples/stock_data_analysis.py
```

Executable integration checks live in the repository's `tests/` directory:

Run the deterministic harness contracts without API credentials or network access:

```bash
uv run --all-packages pytest
```

These cover the execution loop, hooks, and tool dispatch using scripted HTTP responses and real local tools. Pytest is a workspace development dependency. See [the behavior contracts](docs/testing/contracts.md) for the guarantees. Expected hook/gate errors are captured by pytest in the failure-isolation cases.

The existing sandbox, subagent, and live screening checks run separately:

```bash
uv run --package agent-harness python tests/test_code_execution.py
uv run --package agent-harness python tests/test_subagent_tools.py
uv run --package agent-harness python tests/test_code_screen.py
```

The code-execution checks may install packages into isolated test environments. The code-screen checks call the live screening service and require OpenRouter credentials.

## Execution flow

1. Stream a completion and send content/reasoning deltas to the sink.
2. Reassemble tool-call fragments by index.
3. Execute requested tools through `ToolHandler` and append their results to message history.
4. Repeat until the model returns an answer without tool calls, cancellation is requested, or `max_iters` is reached.

Reasoning is surfaced to sinks but is not appended to conversation history.
