<div align="center">

<img src="rm_header.svg" alt="local-agent" width="800"/>

# local-agent

**A streaming, tool-calling agent running on a self-hosted Nemotron 3 Nano via vLLM.**

[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![vLLM](https://img.shields.io/badge/vLLM-0.15+-FF6F00)](https://github.com/vllm-project/vllm)
[![Nemotron](https://img.shields.io/badge/Nemotron_3_Nano-4B_FP8-76B900?logo=nvidia&logoColor=white)](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8)
[![uv](https://img.shields.io/badge/uv-managed-DE5FE9)](https://github.com/astral-sh/uv)

</div>

---

## What this is

A minimal agent loop that runs entirely on your own machine. It serves NVIDIA's Nemotron 3 Nano 4B (FP8) through vLLM's OpenAI-compatible API, then talks to it from a small Python client that streams tokens, parses tool calls, executes them, and feeds the results back — until the model stops asking for tools and returns a final answer.

No cloud, no API keys, no telemetry. The whole thing fits on a 16 GB consumer GPU.

## Architecture

Two processes, talking over `localhost:8000`:

```
┌─────────────────────────┐         ┌──────────────────────────┐
│   server/serve.py       │         │   agent/__main__.py      │
│                         │         │                          │
│   vLLM + Nemotron 3     │ ◄─────► │   Streaming REPL         │
│   OpenAI-compatible     │  HTTP   │   Tool dispatch          │
│   :8000/v1              │         │   Reasoning passthrough  │
└─────────────────────────┘         └──────────────────────────┘
```

The split is deliberate: the server is heavy and slow to start, so you launch it once and leave it running. The agent client is cheap to restart while you iterate on tools and prompts.

## Features

- **Streaming token-by-token output** with live reasoning passthrough (the `<think>...</think>` blocks Nemotron 3 emits, parsed by the model's bundled `nano_v3` plugin).
- **Tool calling** in Qwen3-Coder format, parsed server-side by vLLM.
- **Built-in tools** — calculator (AST-based, no `eval`), weather (wttr.in), file reader, file-tree viewer, current time. All sandboxed to the project directory where relevant.
- **Safe by construction** — file tools reject paths outside the project root; the calculator walks the AST instead of evaluating it.
- **FP8 weights + FP8 KV cache** to fit a 16 GB card with a 16k context window.

## Requirements

- Python 3.12
- NVIDIA GPU with at least 16 GB VRAM (defaults are tuned for this; raise `max_model_len` if you have more)
- CUDA driver (the toolkit is not required — config uses `TRITON_ATTN` to avoid needing `nvcc`)
- [`uv`](https://github.com/astral-sh/uv) for dependency management

## Setup

```bash
# 1. Install dependency groups (server is heavy — pulls torch + CUDA wheels)
uv sync --group server --group agent

# 2. Download the model into ./models (~4 GB for the FP8 build)
uv run python scripts/download_model.py

# 3. (Optional) Add an HF token to .env if you ever switch to a gated model
cp .env.example .env
```

Available model presets:

```bash
uv run python scripts/download_model.py --list-presets
```

## Running

**Terminal 1 — start the server:**

```bash
uv run python -m server.serve
```

First start takes a minute or two while vLLM compiles kernels and loads weights. Wait for the line `Uvicorn running on http://0.0.0.0:8000`.

**Terminal 2 — start the agent:**

```bash
uv run python -m agent
```

You'll get a `>` prompt. Try:

```
> what's 2 ** 16 plus the number of files in agent/tools?
> what's the weather in new york right now?
> read agent/loop.py and explain how tool calls are reassembled across stream chunks
```

Type `exit` to quit.

## Project layout

```
local-agent/
├── agent/                  # client side
│   ├── __main__.py         # REPL entry point (uv run python -m agent)
│   ├── agent.py            # Agent class, OpenAI client wiring
│   ├── loop.py             # streaming execution loop, tool-call reassembly
│   ├── tool_handler.py     # tool registry + dispatch
│   └── tools/              # individual tools (one file each)
│       ├── calculator.py
│       ├── file_architecture.py
│       ├── read_file.py
│       └── weather.py
├── server/                 # vLLM server side
│   ├── config.py           # ServerConfig dataclass → vllm CLI args
│   └── serve.py            # locates reasoning-parser plugin, execvp's vllm
├── scripts/
│   └── download_model.py   # snapshot_download wrapper with presets
├── models/                 # HF cache (gitignored)
└── pyproject.toml          # uv workspace, two optional dep groups
```

## How the loop works

`agent/loop.py` is the interesting part. The model's response can interleave plain text, reasoning, and tool-call fragments — the latter arrive in pieces keyed by index, with the function name on the first fragment and arguments dribbling in across many subsequent chunks. The loop:

1. Streams a completion, printing content live.
2. Reassembles fragmented `tool_calls` into complete dicts.
3. If there are tool calls, executes each one through `ToolHandler.call()` and appends the results as `role: "tool"` messages.
4. Repeats until the model returns a turn with no tool calls — that's the final answer.
5. Bails out at `max_iters=10` to avoid runaway loops.

Reasoning content is printed live but **not** appended to history, matching the convention for thinking models.

## Adding a tool

A tool is just a dict with four keys. Drop a file in `agent/tools/` like:

```python
# agent/tools/echo.py
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
    'fn': echo,
}
```

Then register it in `agent/__main__.py`:

```python
from agent.tools import echo
agent = Agent(tools=[..., echo.tool])
```

That's it — the schema goes to the model on the next request, and `ToolHandler` will dispatch to your `fn` when the model calls it.

## Tuning for your GPU

Edit `server/config.py`. The fields that matter most:

| Field | Default | Notes |
|---|---|---|
| `max_model_len` | `16384` | NVIDIA's recommended max is 262144. Raise as VRAM allows. |
| `max_num_seqs` | `8` | Concurrent sequences. Lower if you OOM. |
| `gpu_memory_utilization` | `0.90` | Drop to `0.80` if other processes need VRAM. |
| `kv_cache_dtype` | `fp8` | Set to `auto` for fp16 KV cache (more accurate, more memory). |
| `enforce_eager` | `False` | Flip to `True` if CUDA graphs misbehave on the hybrid Mamba+Attn path. |

## License

MIT (or whatever you prefer — add a `LICENSE` file).

## Acknowledgements

- [NVIDIA Nemotron 3 Nano](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8) for the model
- [vLLM](https://github.com/vllm-project/vllm) for serving
- [wttr.in](https://wttr.in) for the weather endpoint