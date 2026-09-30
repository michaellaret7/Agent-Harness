# Code execution tool

`ExecuteCode` runs model-written Python in a sandbox with a **persistent kernel**: variables, imports and functions defined in one call are visible in every later call of the same agent run.

## Pieces

| File | Role |
|---|---|
| `agent_harness/base_tools/helpers/kernel.py` | Child-side REPL. Reads `{"code": ...}` JSON lines on stdin, execs in one long-lived namespace, replies `{"stdout", "stderr", "ok"}`. Stdlib only, never imported. |
| `agent_harness/base_tools/helpers/sandbox.py` | `SubprocessSandbox`: launches the kernel, owns the workspace dir, enforces per-call timeouts, restarts on timeout or crash. |
| `agent_harness/base_tools/execute_code.py` | The `@agent_tool`. Formats `ExecResult` into a `ToolResult` (head+tail truncated, `error` status on failure). |

## Wiring

The tool needs a sandbox instance, so it is not registered by `Agent` automatically:

```python
agent = Agent(tools=[execute_code_tool()])
```

The kernel starts on the first call and is killed at interpreter exit. Pass `workspace=Path(...)` to run in a directory you control (files written there are visible to `ReadFile`); the default is a temp dir removed on exit. For cleanup at a definite point, build the sandbox yourself and use it as a context manager with `bind_tool(execute_code, _sandbox=sandbox)`.

## Isolation model

Subprocess only. It contains a buggy or runaway script, not a hostile one:

- Scrubbed environment: only `PASSTHROUGH_ENV` variables reach the kernel, so `.env` credentials never leak into model code. Expose specific secrets with `execute_code_tool(env={'FMP_API_KEY': key})`.
- Own working directory, wall-clock timeout per call, output capped at 20k chars.
- A timeout or kernel crash kills the process and starts a fresh kernel. State is lost and the tool says so.

No filesystem, network, or resource limits. Stronger backends (container, hosted) can replace `SubprocessSandbox` behind the same `start` / `exec` / `reset` / `stop` surface.

## Verify

```
uv run python tests/test_code_execution.py
```
