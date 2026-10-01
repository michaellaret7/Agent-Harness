# Code execution tool

`ExecuteCode` runs model-written Python in a sandbox with a **persistent kernel**: variables, imports and functions defined in one call are visible in every later call of the same agent run.

## Pieces

| File | Role |
|---|---|
| `agent_harness/base_tools/code_execution/kernel.py` | Child-side REPL. Reads `{"code": ...}` JSON lines, execs in one long-lived namespace, replies `{"stdout", "stderr", "ok"}`. Stdlib only, never imported. |
| `agent_harness/base_tools/code_execution/sandbox.py` | `SubprocessSandbox`: launches the kernel, owns the workspace dir, builds the package venv, enforces per-call timeouts, restarts on timeout or crash. |
| `agent_harness/base_tools/code_execution/tool.py` | The `@agent_tool`. Formats `ExecResult` into a `ToolResult` (head+tail truncated, `error` status on failure). |
| `agent_harness/base_tools/code_execution/screening.py` | Optional Jev screening before execution. Defines rules and interprets the screening response. |

## Wiring

The tool needs a sandbox instance, so it is not registered by `Agent` automatically:

```python
from agent_harness import Agent
from agent_harness.base_tools.code_execution.tool import execute_code_tool

agent = Agent(tools=[execute_code_tool()])
```

```python
execute_code_tool(
    env={'FMP_API_KEY': key},      # the only host secrets model code can read from its environment
    packages=['pandas'],           # extras on top of the host venv's libraries
    workspace=Path('out'),         # default is a temp dir removed on exit
)
```

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

- Scrubbed environment: only `PASSTHROUGH_ENV` variables reach the kernel, so `.env` credentials are not in model code's environment. Expose specific secrets with `execute_code_tool(env={'FMP_API_KEY': key})`.
- Own working directory, wall-clock timeout per call, each output stream trimmed to its first and last 10k bytes inside the kernel.
- A timeout or kernel crash kills the process and starts a fresh kernel. State is lost and the tool says so.

No filesystem, network, or resource limits: the kernel runs as the host user and can read or write anything that user can, including `.env` files on disk. For real containment, run the whole agent in a container.

## Code screen

Because the sandbox is not a container, `execute_code_tool(j_screen=True)` runs every call past `code_execution/screening.py` before it executes. The code is sent as text to Jev (`typesafe/jev-1.13` through OpenRouter's Decisions API, one request, four yes/no questions) and denied when any rule's probability reaches its threshold. The denial text names the rule and tells the model what is allowed instead, so it can rewrite and retry.

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
| `sabotage` | hidden/encoded payloads, download-and-exec, reverse shells, persistence, tampering with the harness | ordinary computation, HTTP calls, well-known CLI tools, `eval` of visible source | 0.5 |

Wiring:

```python
agent = Agent(tools=[execute_code_tool(env={'FMP_API_KEY': key}, j_screen=True)])
```

The flag is a factory argument, not a tool parameter, so the model cannot switch it off. The tool passes its own sandbox workspace to the screen, so absolute paths under it count as inside. The screen fails closed: a missing `OPENROUTER_API_KEY`, a network error or a malformed reply all block the call, and `execute_code_tool(j_screen=True)` raises at construction when the key is absent. Thresholds and criteria are tuned against the labelled cases in `tests/test_code_screen.py`; Jev's scores vary by about ±0.03 between runs, so keep a margin of 0.1 or more on both sides of each threshold when editing a rule.

## Verify

```
uv run python tests/test_code_execution.py
uv run python tests/test_code_screen.py
```
