# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependencies live in the `agent` group, not the default group. Use `uv sync --group agent` to install — a plain `uv sync` will leave `openai`, `httpx`, and `pydantic` missing.

Entry point: `uv run python -m agent`. This launches the TUI (`tui/app.py`). The legacy `python -m agent.agent` / `python agent/agent.py` paths are gone — there is no headless CLI mode. To use the agent programmatically, import `Agent` and call `agent.run(prompt, sink=..., cancel_event=...)`. If `sink` is None, output goes to stdout via `StdoutSink` (used for tests/scripts).

Pinned to Python 3.12 (`<3.13`) via `pyproject.toml` and `.python-version`. uv will refuse to sync on a 3.13 interpreter.

There is no test suite, linter, or build step configured.

## Architecture

### Top-level package layout

`tools/` and `tui/` both live at the **project root**, not under `agent/`. `agent/agent.py` imports them as `from tools.base import ...` and `from tui.sink import Sink`. When adding new packages, follow this convention — keep them top-level so they're importable from anywhere in the repo.

### TUI

`tui/` is the prompt_toolkit + Rich frontend. Architecture:
- `tui/cells.py` — Cell taxonomy (User/Assistant/Tool/Error). Each cell renders to ANSI via Rich and caches the result on `cell.ansi`.
- `tui/history.py` — Lock-protected list of cells. Mutated by Sink (worker thread); read by renderer (UI thread).
- `tui/sink.py` — `Sink` Protocol with 8 methods (`on_user_message`, `on_reasoning_delta`, `on_content_delta`, `on_assistant_end`, `on_tool_start`, `on_tool_end`, `on_error`, `on_interrupted`). Two implementations: `TUISink` (mutates History + invalidates app), `StdoutSink` (legacy fallback).
- `tui/panels.py` — `OutputPanel` (FormattedTextControl + ANSI), `InputPanel` (TextArea, multi-line, Shift+Enter newline), `StatusBar`.
- `tui/app.py` — `TUIApp` class. Async shell, sync loop. On Enter, `agent.run(prompt, sink, cancel_event)` runs in a worker via `asyncio.to_thread`. Esc sets `cancel_event` AND closes the in-flight stream. Ctrl+C double-tap exits.

**Transparent background is a hard constraint** — Rich and prompt_toolkit are configured to never set a background color, so the terminal's native theme shows through.

### Provider abstraction

Both providers (`vllm`, `openrouter`) talk through the **OpenAI Python SDK**. `agent/client.py` is the single place that knows about provider differences:

- `vllm` uses a placeholder API key (the hosted endpoint is unauthenticated) and pulls `VLLM_API_URL` / `VLLM_MODEL` from env.
- `openrouter` requires `OPENROUTER_API_KEY` and a `model` argument (any model string from openrouter.ai/models); `OPENROUTER_API_URL` is optional.

Note: the `Agent` class default is `provider='vllm'`, but `agent/__main__.py` overrides it to `provider='openrouter', model='nvidia/nemotron-3-super-120b-a12b'`. Changing the default behavior of `python -m agent` means editing that file, not the class default.

### The streaming loop (`agent/loop.py`)

This is the load-bearing file. Two non-obvious invariants:

1. **Tool-call fragment reassembly.** OpenAI emits `tool_calls` as deltas keyed by `index`. The first fragment carries `id` and `function.name`; subsequent fragments append to `function.arguments`. `call_llm` accumulates these into a dict-by-index, then sorts to a list. If you change the streaming logic, preserve the index-keyed merge — concatenating fragments in arrival order will corrupt parallel tool calls.

2. **Reasoning content is printed but never persisted.** `delta.reasoning_content` (and the non-stream `message.reasoning`) are surfaced live to stdout but deliberately **not** appended to `messages`. This matches the convention for thinking-model APIs and keeps `<think>` blocks out of subsequent prompts. Don't "fix" this by adding it to history.

The loop bails at `max_iters=10` to prevent runaway tool-call cycles.

### Agent ↔ ToolHandler split

`Agent` owns the tool **registry** (`self.tools` schema list + `self.tool_functions` callable map) and message history. `ToolHandler` owns **execution only** — it reads from `agent.tool_functions` and returns `role: "tool"` messages. The handler does not register tools. Keep this split when extending: registration on `Agent`, dispatch on `ToolHandler`.

### Tool schema

A tool module exports a `tool` dict with exactly four keys: `name`, `description`, `parameters` (JSON Schema), `function` (callable). Register via `agent.add_tool(**module.tool)`. `add_tool` is idempotent by name — re-registering is a silent no-op, not an error.

### Bash tool platform handling

`tools/base/bash.py` intentionally avoids `shell=True` and resolves a real bash binary at import time. On Windows it prefers Git Bash paths and skips `System32\bash.exe` (WSL), which sees a different filesystem. `BASH_PATH` env var overrides the lookup. Don't replace this with `shell=True` — it would silently dispatch to `cmd.exe` on Windows, which doesn't understand the POSIX commands the model emits.

## Configuration

`.env` is required. `.env.example` lists both provider blocks (`OPENROUTER_*`, `VLLM_*`). Only the credentials for the provider you actually use need real values.

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
