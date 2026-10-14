# Code execution tool

`ExecuteCode` runs model-written Python in a sandbox with a **persistent kernel**: variables, imports and functions defined in one call are visible in every later call of the same agent run.

## Pieces

| File | Role |
|---|---|
| `agent_harness/base_tools/code_execution/kernel.py` | Child-side REPL. Reads `{"code": ...}` JSON lines, execs in one long-lived namespace, replies `{"stdout", "stderr", "ok"}`. Stdlib only, never imported. |
| `agent_harness/base_tools/code_execution/sandbox.py` | `SubprocessSandbox`: launches the kernel, owns the workspace dir, builds the package venv, enforces per-call timeouts, restarts on timeout or crash. |
| `agent_harness/base_tools/code_execution/config.py` | `SandboxConfig`: the one definition of the sandbox options (`env`, `packages`, `j_screen`), shared by `execute_code_tool` and `AgentWorkspace`. |
| `agent_harness/base_tools/code_execution/tool.py` | The `@agent_tool`. Formats `ExecResult` into a `ToolResult` (head+tail truncated, `error` status on failure). |
| `agent_harness/base_tools/code_execution/screening.py` | Optional Jev screening before execution. Defines rules and interprets the screening response. |

## Wiring

The tool needs a sandbox instance, so it is not registered by `Agent` automatically:

```python
from agent_harness import Agent, SandboxConfig, execute_code_tool

agent = Agent(tools=[execute_code_tool()])
```

```python
execute_code_tool(
    SandboxConfig(
        env={'FMP_API_KEY': key},  # the only host secrets model code can read from its environment
        packages=['pandas'],       # extras on top of the host venv's libraries
        j_screen=True,             # screen every call before it runs (see Code screen)
    ),
    working_dir=Path('out'),       # default is a temp dir removed on exit
)
```

The sandbox options live in one place, `SandboxConfig`. Two entry points take it, chosen by how long the sandbox's files should live:

| Need | Use | Working directory |
|---|---|---|
| Ephemeral: one run, a subagent, an eval | `execute_code_tool(SandboxConfig(...))` | Temp dir removed at exit, or `working_dir=` (never removed) |
| Persistent: files, skills, memory across runs | `AgentWorkspace(name, sandbox=SandboxConfig(...)).attach(agent)` | `~/.agent_harness/workspaces/<name>/sandbox` (see `docs/agents/agent_workspace.md`) |

The kernel starts on the first call and is killed at interpreter exit. For cleanup at a definite point, build the sandbox yourself and use it as a context manager with `bind_tool(execute_code, _sandbox=sandbox)`.

## Packages

Model code can import everything installed in the host environment (the agent's venv). `packages=[...]` adds more without touching that environment:

```
kernel import order
1. ~/.cache/agent-harness/sandbox-venvs/<hash>   packages=[...] and their dependencies
2. the host venv's site-packages                 everything else
```

- The venv is built once per (interpreter, package list) and reused. With `uv` on PATH that takes about a second from a warm cache; otherwise it falls back to stdlib `venv` + pip.
- A `host_environment.pth` file in the cached venv adds the host's site-packages after its own, so the cached venv's copy wins on a version clash.
- A clash can break an inherited package that was built against the host's version. If that happens, list that package in `packages=` too so both come from the same layer.

## Kernel I/O

The protocol runs on private copies of the kernel's original stdin / stdout. File descriptors 0, 1 and 2 are repointed before model code runs, so prints, child-process output and C-extension output all land in the reply, and reading stdin gets EOF instead of the next request.

## Isolation model

Subprocess only. It contains a buggy or runaway script, not a hostile one:

- Scrubbed environment: only `PASSTHROUGH_ENV` variables reach the kernel, so `.env` credentials are not in model code's environment. Expose specific secrets with `execute_code_tool(SandboxConfig(env={'FMP_API_KEY': key}))`.
- Own working directory, wall-clock timeout per call, each output stream trimmed to its first and last 10k bytes inside the kernel.
- A timeout or kernel crash kills the process and starts a fresh kernel. State is lost and the tool says so.
- Per-call `timeout` defaults to 60s, max 1800s, so a call can run a whole sub-agent.

No filesystem, network, or resource limits: the kernel runs as the host user and can read or write anything that user can, including `.env` files on disk. For real containment, run the whole agent in a container.

## Agents built in model code

The kernel runs on the host's Python, so model code can `from agent_harness import Agent`, define `@agent_tool` functions, build an agent and `print(agent.run(task))`. Pass the provider credentials (e.g. `OPENROUTER_API_KEY`, `OPENROUTER_API_URL`) through `SandboxConfig(env=...)`. Langfuse keys and the host's `LANGFUSE_SESSION_ID` are forwarded automatically, so these agents trace into the parent's session as sibling traces.

Recursion stops after one level. Each sandbox sets `AGENT_HARNESS_SANDBOX_DEPTH` to its parent's depth + 1 (after caller `env`, so it cannot be overridden), and `Agent.__init__` refuses above `MAX_AGENT_DEPTH` (1):

| Process | Depth | May build an Agent |
|---|---|---|
| Host | 0 | yes |
| Host agent's kernel | 1 | yes (sub-agents, which may have ExecuteCode) |
| Sub-agent's kernel | 2 | no — `RuntimeError` |

This is a guardrail against runaway recursion, not a security boundary.

## Code screen

Because the sandbox is not a container, `execute_code_tool(SandboxConfig(j_screen=True))` runs every call past `code_execution/screening.py` before it executes. The code is sent as text to Jev (`typesafe/jev-1.13` through OpenRouter's Decisions API, one request, four yes/no questions) and denied when any rule's probability reaches its threshold. The denial text names the rule and tells the model what is allowed instead, so it can rewrite and retry.

```
model emits ExecuteCode(code)
        │
        ▼
  execute_code: screen_code(code, workspace) ──── Jev ────▶ scores {rule: p}
        │
   any p ≥ threshold ──yes──▶ ToolResult('blocked by code screen: [rule p=0.87] <what to do instead>', status='error')
        │ no
        ▼
  SubprocessSandbox.exec(code)
```

| Rule | Blocks | Allows | Threshold |
|---|---|---|---|
| `secret_exposure` | printing, logging, returning, storing or transmitting a key value, or more than its first 4 chars, including via a printed `response.url` / `request.headers` | using the key in a header, param or client constructor; a 4-char prefix; name-only checks and listings | 0.45 |
| `env_file_read` | any read of `.env` / `.env.*`, including `load_dotenv`, shell `cat`, copies and recursive searches that don't exclude them | reading source, data and other config files; listing a directory that holds one | 0.5 |
| `modify_outside_workspace` | delete, truncate, write, move or rename outside the workspace, and `pip install` (writes to the interpreter env) | the same inside the workspace; reading anywhere | 0.5 |
| `memory_store_access` | any read, write, copy, delete, existence check or search of the workspace memory store (`~/.agent_harness/memory/<name>.sqlite`, or any `memory.sqlite`), including disguised names | its own SQLite files inside the workspace, `:memory:` databases, anything merely named "memory" | 0.25 |
| `sabotage` | hidden/encoded payloads, download-and-exec, reverse shells, persistence, tampering with the harness | ordinary computation, HTTP calls, well-known CLI tools, `eval` of visible source | 0.5 |

Wiring:

```python
agent = Agent(tools=[execute_code_tool(SandboxConfig(env={'FMP_API_KEY': key}, j_screen=True))])
```

The flag is a config option, not a tool parameter, so the model cannot switch it off. The tool passes its own sandbox workspace to the screen, so absolute paths under it count as inside. The screen fails closed: a missing `OPENROUTER_API_KEY`, a network error or a malformed reply all block the call, and `execute_code_tool(SandboxConfig(j_screen=True))` raises at construction when the key is absent. Thresholds and criteria are tuned against the labelled cases in `tests/test_code_screen.py`; Jev's scores vary by about ±0.03 between runs, so keep a margin of 0.1 or more on both sides of each threshold when editing a rule.

## Verify

```
uv run python tests/test_code_execution.py
uv run python tests/test_code_screen.py
```
