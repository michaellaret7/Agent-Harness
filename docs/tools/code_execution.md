# Code execution tool

`ExecuteCode` runs model-written Python in a sandbox with a **persistent kernel**: variables, imports and functions defined in one call are visible in every later call of the same agent run.

## Pieces

| File | Role |
|---|---|
| `agent_harness/base_tools/helpers/kernel.py` | Child-side REPL. Reads `{"code": ...}` JSON lines, execs in one long-lived namespace, replies `{"stdout", "stderr", "ok"}`. Stdlib only, never imported. |
| `agent_harness/base_tools/helpers/sandbox.py` | `SubprocessSandbox`: launches the kernel, owns the workspace dir, builds the package venv, enforces per-call timeouts, restarts on timeout or crash. |
| `agent_harness/base_tools/execute_code.py` | The `@agent_tool`. Formats `ExecResult` into a `ToolResult` (head+tail truncated, `error` status on failure). |

## Wiring

The tool needs a sandbox instance, so it is not registered by `Agent` automatically:

```python
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

## Verify

```
uv run python tests/test_code_execution.py
```
