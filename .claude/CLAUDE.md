# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependencies live in the `agent` group, not the default group. Use `uv sync --group agent` to install — a plain `uv sync` will leave `openai`, `httpx`, and `pydantic` missing.

Entry point: `uv run python -m agent.agent` (or `uv run python agent/agent.py`). The README's `python -m agent` does not work — there is no `agent/__main__.py`. `agent/agent.py` adds the project root to `sys.path` at import time so both invocation forms work.

Pinned to Python 3.12 (`<3.13`) via `pyproject.toml` and `.python-version`. uv will refuse to sync on a 3.13 interpreter.

There is no test suite, linter, or build step configured.

## Architecture

### Tool location

Tools live at the **top-level `tools/` package**, not `agent/tools/` as the README claims. `agent/agent.py` imports them as `from tools.base import bash, edit, glob, grep, read, tree, write`. When adding tools, follow this layout — do not create `agent/tools/`.

### Provider abstraction

All three providers (`vllm`, `openai`, `anthropic`) talk through the **OpenAI Python SDK**. Anthropic is reached via its OpenAI-compatible shim, not the `anthropic` SDK. `agent/client.py` is the single place that knows about provider differences:

- `vllm` uses a placeholder API key (the hosted endpoint is unauthenticated) and pulls `VLLM_API_URL` / `VLLM_MODEL` from env.
- `openai` / `anthropic` require both an API key env var and a model argument; the URL env var is optional.

Note: the `Agent` class default is `provider='vllm'`, but `agent/agent.py`'s `__main__` block overrides it to `provider='anthropic', model='claude-opus-4-7'`. Changing the default behavior of `python -m agent.agent` means editing that block, not the class default.

### The streaming loop (`agent/loop.py`)

This is the load-bearing file. Two non-obvious invariants:

1. **Tool-call fragment reassembly.** In streaming mode, OpenAI emits `tool_calls` as deltas keyed by `index`. The first fragment carries `id` and `function.name`; subsequent fragments append to `function.arguments`. `call_llm_stream` accumulates these into a dict-by-index, then sorts to a list. If you change the streaming logic, preserve the index-keyed merge — concatenating fragments in arrival order will corrupt parallel tool calls.

2. **Reasoning content is printed but never persisted.** `delta.reasoning_content` (and the non-stream `message.reasoning`) are surfaced live to stdout but deliberately **not** appended to `messages`. This matches the convention for thinking-model APIs and keeps `<think>` blocks out of subsequent prompts. Don't "fix" this by adding it to history.

The loop bails at `max_iters=10` to prevent runaway tool-call cycles.

### Agent ↔ ToolHandler split

`Agent` owns the tool **registry** (`self.tools` schema list + `self.tool_functions` callable map) and message history. `ToolHandler` owns **execution only** — it reads from `agent.tool_functions` and returns `role: "tool"` messages. The handler does not register tools. Keep this split when extending: registration on `Agent`, dispatch on `ToolHandler`.

### Tool schema

A tool module exports a `tool` dict with exactly four keys: `name`, `description`, `parameters` (JSON Schema), `function` (callable). Register via `agent.add_tool(**module.tool)`. `add_tool` is idempotent by name — re-registering is a silent no-op, not an error.

### Bash tool platform handling

`tools/base/bash.py` intentionally avoids `shell=True` and resolves a real bash binary at import time. On Windows it prefers Git Bash paths and skips `System32\bash.exe` (WSL), which sees a different filesystem. `BASH_PATH` env var overrides the lookup. Don't replace this with `shell=True` — it would silently dispatch to `cmd.exe` on Windows, which doesn't understand the POSIX commands the model emits.

## Configuration

`.env` is required. `.env.example` lists all three provider blocks (`ANTHROPIC_*`, `OPENAI_*`, `VLLM_*`). Only the credentials for the provider you actually use need real values.

System prompt and persistent memory are plain markdown at `agent/context/system_prompt.md` and `agent/context/memory.md`. Both are read at `Agent.__init__` and concatenated into the initial system message — there is no runtime reload.

## Development Guidelines

### Core Philosophy

- **KISS** — choose straightforward solutions; simple is easier to maintain and debug.
- **YAGNI** — implement only what's needed now, not what might be useful later.
- **DRY** — single source of truth for every piece of knowledge. Search for an existing helper before writing a new one; extract shared logic into pure reusable functions.

### Design Principles

- **Dependency Inversion** — high-level modules depend on abstractions, not low-level modules.
- **Open/Closed** — open for extension, closed for modification.
- **Single Responsibility** — one clear purpose per function/class/module.
- **Fail Fast** — validate early, raise immediately when something's wrong.
- **Type safety** — type hints and explicit return types are mandatory; the codebase should read as self-documenting.
- **Resource efficiency** — context managers for all I/O; vectorize data-heavy work.

### Code Constraints

- Files: max 500 lines — split into modules if approaching the limit.
- Functions: max 50 lines, single responsibility.
- Classes: max 100 lines, one concept.
- Group code by feature/responsibility.

### Whitespace & Vertical Formatting (CRITICAL)

Code must breathe. Use blank lines to separate logical blocks within functions:

- Blank line after the initial declaration block.
- Blank line between distinct steps inside a loop (fetch → validate → transform → assign).
- Blank line before `return`.
- Blank line between independent `if` checks in a loop.

```python
def process_items(items: list[str], lookup: dict):
    results: dict[str, float] = {}
    errors: list[str] = []

    for item in items:
        value = lookup.get(item)

        if value is None:
            errors.append(item)
            continue

        transformed = value * 2.0

        results[item] = transformed

    return results, errors
```

### Naming

- Variables/functions: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private attributes: `_leading_underscore`
- Type aliases / Enums: `PascalCase` / `UPPER_SNAKE_CASE`
- Never prefix folders or files with `_`.

### Documentation

- Module docstring explaining purpose.
- Complete docstrings on public functions.
- Inline comments with `# Reason:` prefix only when the WHY is non-obvious.
- Helper functions live at the **top** of the file under a banner block:

  ```
      ================================
  --> Helper funcs
      ================================
  ```

### Complexity Gauging

Before writing or planning: assess whether the approach is under-engineered, optimally engineered, or over-engineered. Aim for the middle.

### Testing

- No pytest scaffolding — write **real tests with real data**.
- A test exercises the full flow: pull real inputs, call the function, grade the output. Lint/format afterward.
- Don't create parallel `test_x.py` and `test_x_fixed.py` files — fix the one test in place.

### Hard Rules

- **No backwards-compatibility shims.** If a change is needed, build the new solution and update every caller. Backwards-compat violates the design principles.
- **Never create CLI flag–driven test scripts** like `tests/foo.py --mode long-only`. If behavior needs to switch, write separate entry points or pass arguments programmatically.
- **Never auto-create READMEs** for specific functionality unless explicitly requested.
- **Disagree freely** — correctness beats agreement. If the user is wrong, say so.
- For specs, standards, or patterns worth referencing later, write a document under `docs/`, organized by topic (e.g. `docs/tools/`, `docs/agents/`). Institutional knowledge belongs in the repo, not just chat history.
- **Agent system prompts use XML tags** (`<role>`, `<methodology>`, `<constraints>`, `<output_format>`) for top-level structure; markdown headers are sub-structure within those XML sections.
- Use the LSP / Pyright server when available.

### Branching

`main` (production) · `dev` (integration) · `feature/*` · `fix/*` · `refactor/*` · `docs/*` · `test/*`
