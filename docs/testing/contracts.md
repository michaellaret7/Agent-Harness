# Harness behavior contracts

Run the ten deterministic contracts with:

```bash
uv run --all-packages pytest
```

Pytest is installed with the workspace development dependencies. After dependency
installation, these tests need no API credentials or network access. Run one
module with `uv run --all-packages pytest tests/test_hooks.py` or one test with
`uv run --all-packages pytest tests/test_hooks.py::test_hook_failure_isolation`.

`tests/conftest.py` excludes the existing standalone sandbox, subagent, and live
screening scripts from collection; those still run through their Python entry
points. New `test_*.py` modules under `tests/` are discovered automatically.

## What is exercised

```text
Scripted HTTP/SSE response -> real SDK -> Agent.run -> real loop -> real tools
                                               |
                                        sinks and hooks
```

`tests/contract_support.py` replaces only the provider's HTTP transport with an
in-memory transport. It captures serialized outgoing requests and supplies fixed
SSE deltas, so tests cover SDK parsing, fragment assembly, dispatch, and history.
Remote tracing is disabled for these standalone runs. Tool-handler tests invoke
the real handler directly with local tools and temporary files.

Cancellation is triggered at a fixed stream boundary. Parallel tests coordinate
with events and barriers; five-second waits are deadlock guards, not performance
assertions. The tests assert per-call ordering and serial boundaries, not a
global start order across concurrent tools. Expected hook/gate error messages
are captured by pytest during the failure-isolation cases.

## Stable guarantees

| Test | Contract |
|---|---|
| `test_complete_tool_cycle` | Interleaved calls execute once with the right arguments; matching result IDs and payloads reach the next request. |
| `test_reasoning_exclusion` | Reasoning reaches the sink but never history or subsequent model requests; visible answer text is preserved. |
| `test_iteration_limit` | Repeated tool calls cannot exceed `max_iters` loop requests. |
| `test_cancellation` | Mid-stream cancellation prevents pending execution, closes the stream, reports `cancelled` with the actual iteration count, and leaves history safe for a later run. Incomplete calls lacking an ID are discarded; complete calls receive interrupted results. |
| `test_hook_failure_isolation` | A failing observer cannot prevent later observers or the turn from completing. |
| `test_hook_tool_filtering` | Tool filters match only their named tools; unknown tool filters are rejected. |
| `test_hook_context_and_ordering` | Parallel calls retain their own hook identity, arguments, and outcome; each start precedes its end even when completion order reverses. |
| `test_gate_enforcement` | Denials and failing gates prevent filesystem side effects and stop the gate chain; rewrites reach later gates and the tool. |
| `test_tool_error_handling` | Invalid JSON, unknown tools, exceptions, and invalid return types yield paired errors without preventing later calls. Explicit status governs success, independent of payload wording. |
| `test_parallel_boundaries` | Opted-in calls overlap, serial tools separate batches, and returned results retain request order. |

Change these assertions only when the intended contract changes or a test is
incorrect. Refactors may require import/fixture updates, but should preserve the
guarantees. The tests do not measure model decision quality, live provider
availability, or timing performance; those require separate evaluations or
integration checks.
