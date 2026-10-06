# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This repo is a **uv workspace** of library packages (see Architecture). Install the whole workspace into one shared venv with `uv sync --all-packages` — a plain `uv sync` only syncs the virtual root (which depends on nothing) and leaves the members uninstalled.

Entry points:
- `uv run --package agent-harness python -m agent_harness` — a headless development REPL: base tools + `ExecuteCode`. Used as a sanity check that the streaming loop and sandbox work end-to-end.
- `uv run --package agent-harness python examples/stock_data_analysis.py` — the live stock-data and code-screening example.

Application-specific agents live in other repos and import the engine. To use the agent programmatically, `from agent_harness import Agent` and call `agent.run(task, sink=..., cancel_event=...)`. `run()` returns the final text (or a Pydantic instance when `output_model=` is set); the full transcript stays on `agent.messages`. The task can also be set at init via `Agent(task=...)` and `run()` called with no arg — useful for batch pipelines. If `sink` is None, output goes to stdout via `StdoutSink`. The TUI is opt-in: `TUIApp(agent).run_async()` from `tui/app.py`.

Supports Python 3.12–3.13 (`>=3.12,<3.14` in `pyproject.toml`); `.python-version` keeps this repo's own venv on 3.12. uv will refuse to sync on a 3.14 interpreter.

Tests live in root `tests/`. `uv run --all-packages pytest` runs the offline behavior contracts (scripted HTTP responses through the real SDK, loop, and tools — no credentials needed; see `docs/testing/contracts.md`). `test_code_execution.py`, `test_subagent_tools.py`, and `test_code_screen.py` are excluded from collection by `conftest.py` and run as scripts with `uv run --package agent-harness python tests/<file>.py`; the screening checks need live OpenRouter credentials. No linter is configured. The library packages build as wheels via hatchling (`uv build --all-packages`).

## Response Type 
- Please be clear, concise, and to the point in your responses and do your best to avoid unecessary verbosity
- I am a visual learner — when explaining anything, always include visuals (diagrams, flowcharts, tables, ASCII/Mermaid sketches) alongside the text

## Overall Goal of Code 
- To write clean, clear, well architected code that is easy for humans to understand 

## Architecture

### Workspace layout

The repo is a **uv workspace** with a virtual root (`pyproject.toml` at the repo root holds the workspace members, pytest config and dev dependencies, `package = false`). Members:

- `packages/agent_harness/` — the `agent-harness` distributable (import `agent_harness`). The base `Agent` class, streaming loop, tooling (`tooling/`: decorator, handler, result), hooks, gates, subagents, sinks, and base tools (`base_tools/`: WebSearch, WebExtract, Plan, LoadTool, DeploySubagent, and `code_execution/` for ExecuteCode). Domain-agnostic. Treat it as the shared package: no domain logic leaks in, and the dependency direction is one-way (frontend/domains → `agent_harness`, never the reverse). It **reads** configuration (`os.getenv`) but never **loads** it — applications (the entry points) own bootstrap, including `load_dotenv()`. Core deps are `openai`/`httpx`/`pydantic`/`langfuse`/`parallel-web` (WebSearch/WebExtract) — the LangfuseSink ships with the engine and auto-registers when `LANGFUSE_PUBLIC_KEY` is present (its import in `sinks/__init__.py` is lazy, gated on that env var, so the package is pulled but only loaded when tracing is on). Built with hatchling under `src/` layout.
- `packages/tui/` — the `tui` distributable (import `tui`). prompt_toolkit + Rich frontend; depends on `agent-harness`. Generic — no domain knowledge. `prompt_toolkit`/`rich` live here, not in the engine, so headless consumers of `agent_harness` never pull terminal-UI deps.
- `packages/architectures/` — the `architectures` distributable (import `architectures`). Multi-agent architectures built on `agent-harness`. `organization/` holds `Organization`: an agent registry plus a FIFO message bus. `register_agent(role, agent)` binds the org tools (`ListOrgMembers`, `GetOrgInfo`, `SendMessage`) to that member's id and appends an `<organization>` block (name, role, id, goal) to its system prompt via `Agent.extend_system_prompt` — so the agent must not have run yet. `run_until_idle()` delivers queued messages serially by calling `agent.run` on the recipient; replies travel only via `SendMessage`.

Root-level support folders: `examples/` (runnable demos), `tests/` (contracts + standalone scripts), `docs/` (technical docs by topic, e.g. `docs/tools/code_execution.md`). `dist/` holds built wheels and is gitignored.

```
Agent(system=, tools=, subagents=, output_model=)
  ├─ base_tools/  search · extract · plan · load_tool · deploy_subagent · code_execution/
  ├─ tooling/     decorator (@agent_tool, bind_tool) · handler (dispatch) · result (ToolResult)
  ├─ loop.py      stream → reassemble tool calls → dispatch → repeat
  ├─ hooks.py / gates.py   observe / allow-deny-rewrite
  └─ sinks/       Sink Protocol · StdoutSink · LogSink · LangfuseSink · HookSink
packages/tui/   TUISink + TUIApp (optional frontend)
packages/architectures/   Organization (registry + message bus + org tools)
```

**On distribution & backwards-compat:** the library packages are meant to be consumed by domains in *other* repos (via Git dependency, e.g. `agent-harness @ git+…#subdirectory=packages/agent_harness`). Because external systems pin a version, `agent_harness`'s public API warrants SemVer discipline — the "No backwards-compatibility shims / update every caller" Hard Rule below applies cleanly *within* this workspace, but a breaking change to the engine's public surface is a real major-version event for outside consumers.

Domains assemble an Agent by passing constructor args: `system`, `tools` (and optionally `task` for batch / one-shot use, `subagents`, `output_model`, `reasoning_effort`). The base ships generic methodology only. The domain appends a `<role>` block via `system=` and registers its tools. No subclassing — just composition through `Agent(...)`. The one exception is `SubAgent` (`sub_agent.py`), an engine-internal `Agent` variant that refuses gates, hooks, and nested subagents.

### Extension points

| Mechanism | Register | Fires | Authority |
|---|---|---|---|
| Hook (`hooks.py`) | `agent.add_hook(event, fn, tool=...)` | turn / loop / iteration / tool boundaries | None — observe only, return value ignored |
| Gate (`gates.py`) | `agent.add_gate(fn, tool=...)` | before tool dispatch | `allow` / `deny` / `rewrite` args |
| Subagent (`subagent_config.py`) | `Agent(subagents=[SubAgentConfig(...)])` | model calls `DeploySubagent` | fresh `SubAgent` per deployment, isolated history |
| Sink (`sinks/base.py`) | `agent.run(sink=...)` or `register_always_on(factory)` | every lifecycle event | presentation / tracing |

Hooks that append to `agent.messages` must do so only at start boundaries (`turn_start`, `loop_start`, `iteration_start`) where no tool_call awaits its result. Tool-filtered hooks and gates fail fast on an unknown tool name.

### TUI

`packages/tui/` is the prompt_toolkit + Rich frontend (import `tui`). Architecture:
- `tui/cells/` — Cell taxonomy (User/Assistant/Tool/Error/Header). Each cell renders to ANSI via Rich and caches the result on `cell.ansi`.
- `tui/history.py` — Lock-protected list of cells. Mutated by Sink (worker thread); read by renderer (UI thread).
- `tui/sink.py` — `TUISink`, one implementation of the engine's `Sink` Protocol (`on_user_message`, `on_reasoning_delta`, `on_content_delta`, `on_assistant_end`, `on_tool_start`, `on_tool_end`, `on_error`, `on_interrupted`, …) that mutates History + invalidates the app. The Protocol itself and the headless `StdoutSink` live in `agent_harness/sinks/`.
- `tui/panels/` — `OutputPanel` (FormattedTextControl + ANSI), `InputPanel` (TextArea, multi-line, Shift+Enter newline), `StatusBar`.
- `tui/app.py` — `TUIApp` class. Async shell, sync loop. On Enter, `agent.run(prompt, sink, cancel_event)` runs in a worker via `asyncio.to_thread`. Esc sets `cancel_event` AND closes the in-flight stream. Ctrl+C double-tap exits.

**Transparent background is a hard constraint** — Rich and prompt_toolkit are configured to never set a background color, so the terminal's native theme shows through.

### Provider abstraction

Both providers (`vllm`, `openrouter`) talk through the **OpenAI Python SDK**. `agent_harness/client.py` is the single place that knows about provider differences:

- `vllm` uses a placeholder API key (the hosted endpoint is unauthenticated) and pulls `VLLM_API_URL` / `VLLM_MODEL` from env.
- `openrouter` requires `OPENROUTER_API_KEY` and a `model` argument (any model string from openrouter.ai/models); `OPENROUTER_API_URL` is optional.

Note: the `Agent` class defaults to `provider='openrouter'`. `agent_harness/__main__.py` (the development entry point) also selects its OpenRouter model explicitly. Pass `provider='vllm'` to use the hosted vLLM endpoint. The client is built lazily on first `run()`, so `Agent(...)` at module level is import-safe without a `.env`.

### The streaming loop (`agent_harness/loop.py`)

This is the load-bearing file. Two non-obvious invariants:

1. **Tool-call fragment reassembly.** OpenAI emits `tool_calls` as deltas keyed by `index`. The first fragment carries `id` and `function.name`; subsequent fragments append to `function.arguments`. `call_llm` accumulates these into a dict-by-index, then sorts to a list. If you change the streaming logic, preserve the index-keyed merge — concatenating fragments in arrival order will corrupt parallel tool calls.

2. **Reasoning content is printed but never persisted.** `delta.reasoning_content` (and the non-stream `message.reasoning`) are surfaced live to the sink but deliberately **not** appended to `messages`. This matches the convention for thinking-model APIs and keeps `<think>` blocks out of subsequent prompts. Don't "fix" this by adding it to history.

The loop bails at `max_iters` (default 100) to prevent runaway tool-call cycles. Override per-agent via `Agent(max_iters=...)`. Cancellation via `cancel_event` is checked at iteration boundaries and inside the chunk loop; partial state is fixed up so `messages` stays well-formed.

### Agent ↔ ToolHandler split

Tool infrastructure lives in `agent_harness/tooling/`: `decorator.py` generates schemas and binds dependencies, `handler.py` dispatches calls, and `result.py` defines `ToolResult`. Model-facing tools live separately in `base_tools/`.

`Agent` owns the tool **registry** (`self.tools` schema list + `self.tool_functions` callable map) and message history. `ToolHandler` owns **execution only** — it reads from `agent.tool_functions`, consults gates, and returns `role: "tool"` messages. The handler does not register tools. Keep this split when extending: registration on `Agent`, dispatch on `ToolHandler`. Consecutive calls to tools marked `safe_parallel=True` run in a thread pool; everything else is sequential.

### Tool schema

A tool module exports a `tool` dict with keys `name`, `description`, `parameters` (JSON Schema), `function` (callable), plus optional `deferred` and `safe_parallel`. Register via `agent.add_tool(module.tool)`. Functions decorated with `@agent_tool` (see `agent_harness/tooling/decorator.py`) carry the dict on their `.tool` attribute; pass the function itself: `agent.add_tool(my_fn)`. Use `bind_tool(fn, _dep=...)` to inject underscore-prefixed dependencies the model never sees (a sandbox, a registry). Every tool function returns `ToolResult(payload, status)` — payload text never determines status. `add_tool` is idempotent by name — re-registering is a silent no-op, not an error.

### Code execution (`base_tools/code_execution/`)

`ExecuteCode` is not registered by `Agent` automatically — it needs a sandbox: `Agent(tools=[execute_code_tool(workspace=..., env=..., packages=..., j_screen=...)])`. `sandbox.py` launches the sibling `kernel.py` with an explicit Python executable, never `shell=True`; the kernel is a stdlib-only script that runs in the sandbox workspace and must stay importable as a standalone file. State persists across calls within one sandbox. `j_screen=True` routes every call through the Jev code screen (`screening.py`) before execution. Full reference: `docs/tools/code_execution.md`.

## Configuration

`.env` is required. `.env.example` lists both provider blocks (`OPENROUTER_*`, `VLLM_*`), `PARALLEL_API_KEY` for WebSearch, and the optional `LANGFUSE_*` block. Only the credentials for the provider you actually use need real values.

System prompts live in two places:
- `agent_harness/context/system_prompt.md` — the always-loaded base methodology (Tools, Planning + generic constraints). Domain-agnostic.
- The caller's `system=` string — appended to the base inside a `<domain>` block by `Agent.__init__`. Holds the `<role>` and any domain-specific constraints. The caller reads its own prompt file and passes the string; the framework does not auto-discover it.

Applications own any memory loading and can include that context in `system`. The base agent has no memory file of its own. The system prompt is assembled at `Agent.__init__` — there is no runtime reload.

## Development Guidelines

### Core Philosophy

- **KISS** — choose straightforward solutions; simple is easier to maintain and debug.
- **YAGNI** — implement only what's needed now, not what might be useful later.
- **DRY** — single source of truth for every piece of knowledge. Search for an existing helper before writing a new one; extract shared logic into pure reusable functions.

#### 1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

#### 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

#### 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

#### 4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

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
- **Preserve existing comments.** Do NOT delete or "clean up" inline comments that are already in the code — including short step-marker comments like `# Emit tool start event to the sink`. Treat them as intentional. Only remove a comment if it is factually wrong after your change, and then replace it with a correct one rather than deleting it outright. This overrides any default tendency to strip "narration" comments.
- When editing a function, leave untouched comments exactly as they are unless the line they describe is itself being changed.
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
