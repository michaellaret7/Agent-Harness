# Tool results (1.0)

Every registered tool callable returns `agent_harness.tool_result.ToolResult`:

```python
from agent_harness.decorator import agent_tool
from agent_harness.tool_result import ToolResult


@agent_tool
def echo(text: str) -> ToolResult:
    """Return text without interpreting its contents."""
    return ToolResult(text, status='ok')
```

`payload` must be a string. `status` must be `ok` or `error`; both fields are
required. Payloads are passed verbatim into model-facing tool messages and
sink outcomes. A payload starting with `error:` can be successful, and a failure
does not need any text prefix.

## Migrating tools

- Replace successful plain returns with `ToolResult(text, status='ok')`.
- Replace error-string returns with `ToolResult(text, status='error')`.
- Convert non-string data to the intended model-facing text explicitly.
- Direct Python callers read `.payload` and `.status` instead of treating the
  return as a string.

This applies to decorated functions and dictionary-registered callables.
The handler rejects non-ToolResult returns as contract errors; there is no
legacy return-value conversion. Uncaught tool exceptions and decorator argument
validation failures become explicit error results.

## Dispatch and batch semantics

The handler adds duration to produce the existing sink `ToolOutcome`.
`denied` and `interrupted` remain dispatch outcomes, not tool return statuses.
The sink API and model-facing message format are unchanged.

`LoadTool` and `WebExtract` report `error` if any requested item fails, retaining
successful items and error details in the same payload. Empty search/extraction
results without reported failures remain successful.

A completed subagent deployment returns its answer as successful payload text;
the answer's wording does not determine execution status. Exceptions from the
deployment are handled as errors by the parent tool handler.
