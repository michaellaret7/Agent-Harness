<div align="center">
# local-agent

**A streaming, tool-calling agent with a Rich + prompt_toolkit TUI, talking to any OpenAI-compatible endpoint.**



[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-managed-DE5FE9)](https://github.com/astral-sh/uv)

</div>

---

## What this is

A minimal agent loop wrapped in a full-screen terminal UI. It streams tokens from a chat-completions endpoint, parses tool calls, executes them, and feeds the results back — until the model stops asking for tools and returns a final answer.

The client supports two backends:

- A hosted vLLM endpoint (e.g. RunPod proxy) — original target was NVIDIA Nemotron 3 Nano on vLLM.
- OpenRouter — gives you access to OpenAI, Anthropic, Google, Meta, and other models through a single OpenAI-compatible endpoint.

## Features

- **Full-screen TUI** built on prompt_toolkit + Rich. Transparent background, native terminal theme shows through.
- **Streaming token-by-token output** with live reasoning passthrough (the `<think>...</think>` blocks thinking models emit).
- **Tool calling** in the standard OpenAI tool-call format, with fragment reassembly across stream chunks.
- **Cancellable turns** — `Esc` aborts an in-flight stream; `Ctrl+C` double-tap exits.
- **Click-to-copy mode** — `Ctrl+T` releases mouse capture so the terminal's native click-drag selection works.
- **Built-in tools** — bash, read/write/edit (updated), glob, grep, tree, and search.

## Requirements

- Python 3.12 (pinned; uv refuses to sync on 3.13)
- [`uv`](https://github.com/astral-sh/uv) for dependency management
- An endpoint to talk to — OpenRouter or a hosted vLLM endpoint

## Setup

```bash
# 1. Install dependencies (note the --group flag — a plain `uv sync` leaves agent deps missing)
uv sync --group agent

# 2. Configure provider credentials
cp .env.example .env
# then edit .env — see "Configuring the endpoint" below
```

## Running

```bash
uv run python -m agent
```

This launches the TUI. Type a prompt and press `Enter` to submit. Try:

```
what's 2 ** 16 plus the number of files in tools/base?
read agent/loop.py and explain how tool calls are reassembled across stream chunks
```

### Key bindings

| Key                | Action                                                |
| ------------------ | ----------------------------------------------------- |
| `Enter`            | Submit the prompt                                     |
| `Shift+Enter`      | Insert a newline (multi-line input)                   |
| `Esc`              | Cancel the in-flight turn / clear the input           |
| `Ctrl+C`           | Cancel the turn; double-tap within 2s to exit         |
| `Ctrl+D`           | Exit (when input is empty)                            |
| `PgUp` / `PgDn`    | Scroll the output panel                               |
| `Ctrl+↑` / `Ctrl+↓`| Scroll the output panel                               |
| `End`              | Jump to bottom and re-lock to tail                    |
| `Ctrl+T`           | Toggle copy mode (releases mouse for text selection)  |
| `Tab`              | Cycle focus                                           |

## Configuring the endpoint

`agent/client.py` builds the OpenAI-compatible client. Two modes:

**OpenRouter (default for `python -m agent`)** — `agent/__main__.py` constructs `Agent(provider='openrouter', model='nvidia/nemotron-3-super-120b-a12b')`. The client reads `OPENROUTER_API_KEY` and optionally `OPENROUTER_API_URL`. OpenRouter exposes every supported model (OpenAI, Anthropic, Google, Meta, etc.) through a single OpenAI-compatible endpoint — pick whatever `model` string you want from openrouter.ai/models.

**Hosted vLLM** — `Agent(provider='vllm')`. The client reads `VLLM_API_URL` and `VLLM_MODEL` from `.env`. Point `VLLM_API_URL` at your RunPod (or other) endpoint. The hosted endpoint is treated as unauthenticated.

The `Agent` class default is `provider='vllm'`, but the `python -m agent` entry point overrides it to OpenRouter. To change what `python -m agent` launches, edit `agent/__main__.py`.

## Project layout

```
local-agent/
├── agent/
│   ├── __main__.py         # TUI entry point (uv run python -m agent)
│   ├── agent.py            # Agent class — message history, tool registry, context wiring
│   ├── client.py           # OpenAI-compatible client builder (vllm / openrouter)
│   ├── loop.py             # streaming execution loop, tool-call reassembly
│   ├── tool_handler.py     # tool dispatch (execution only)
│   └── context/
│       ├── system_prompt.md
│       └── memory.md
├── tools/                  # top-level — imported as `from tools.base import ...`
│   └── base/               # bash, read, write, edit, glob, grep, tree, search
├── tui/                    # top-level — prompt_toolkit + Rich frontend
│   ├── app.py              # TUIApp — layout, key bindings, worker dispatch
│   ├── cells.py            # cell taxonomy (User/Assistant/Tool/Error) + Rich render
│   ├── history.py          # lock-protected list of cells
│   ├── panels.py           # OutputPanel, InputPanel, StatusBar
│   └── sink.py             # Sink Protocol + TUISink / StdoutSink implementations
└── pyproject.toml
```

`tools/` and `tui/` live at the **project root**, not under `agent/`. They're top-level packages and importable from anywhere in the repo.

## How the loop works

`agent/loop.py` is the load-bearing file. The model's response can interleave plain text, reasoning, and tool-call fragments — the latter arrive in pieces keyed by `index`, with the function name on the first fragment and arguments dribbling in across many subsequent chunks. The loop:

1. Streams a completion, surfacing content and reasoning to the Sink as deltas.
2. Reassembles fragmented `tool_calls` into complete dicts (index-keyed merge — concatenating in arrival order would corrupt parallel tool calls).
3. If there are tool calls, executes each one through `ToolHandler.execute()` and appends the results as `role: "tool"` messages.
4. Repeats until the model returns a turn with no tool calls — that's the final answer.
5. Bails out at `max_iters=10` to avoid runaway loops.

Reasoning content is surfaced live but **deliberately not appended to history**, matching the convention for thinking-model APIs and keeping `<think>` blocks out of subsequent prompts.

## Programmatic use

To drive the agent from a script instead of the TUI, import `Agent` directly and pass a `Sink`:

```python
from agent.agent import Agent
from tui.sink import StdoutSink

agent = Agent(provider='openrouter', model='nvidia/nemotron-3-super-120b-a12b')
agent.run('summarize agent/loop.py', sink=StdoutSink())
```

If `sink` is `None`, output goes to stdout via `StdoutSink`. An optional `cancel_event: threading.Event` lets you abort an in-flight turn.

## Adding a tool

A tool is just a dict with four keys: `name`, `description`, `parameters` (JSON Schema), `function` (callable). Drop a file in `tools/base/` (or anywhere else — the path is up to you):

```python
# tools/base/echo.py
def echo(text: str) -> str:
    return text

tool = {
    'name': 'echo',
    'description': 'Echo a string back unchanged.',
    'parameters': {
        'type': 'object',
        'properties': {
            'text': {'type': 'string', 'description': 'String to echo.'},
        },
        'required': ['text'],
    },
    'function': echo,
}
```

Then register it on the agent:

```python
from tools.base import echo
agent = Agent()
agent.add_tool(**echo.tool)
```

`add_tool` is idempotent by name — re-registering is a silent no-op, not an error. The schema goes to the model on the next request, and `ToolHandler` dispatches to your `function` when the model calls it.

## License

MIT — add a `LICENSE` file if you fork this.

## Acknowledgements

- [wttr.in](https://wttr.in) for the weather endpoint
- EditFile tool test edit — round 3 ✏️
