# Validated termination

An agent with `output_model` cannot decide on its own that it is finished. It must hand its answer in through the `SubmitResult` tool, and the harness checks it before the run ends.

```
model replies with plain text ─► nudge: "call SubmitResult" ─► keep working (fails after 3 in a row)
model calls SubmitResult(...)  ─► 1. shape: output_model.model_validate(args)
                                  2. rule:  verifier(result) -> None | reason   (optional)
                                     ├─ fail ─► error tool result ─► model fixes it, resubmits
                                     └─ pass ─► agent.submission = result ─► run() returns it
```

Agents without `output_model` are unchanged: plain text ends the run and `run()` returns a string.

## Usage

```python
class StockPick(BaseModel):
    ticker: str
    reason: str
    evidence: str

def check(pick: StockPick) -> str | None:
    return None if pick.ticker in KNOWN_TICKERS else f'{pick.ticker} is not a listed ticker'

agent = Agent(output_model=StockPick, verifier=check)
pick = agent.run('Find one undervalued stock')   # a validated StockPick
```

- The tool's parameters are `output_model.model_json_schema()`: the model fills the fields itself, with its full context. There is no second structured-output call.
- A `verifier` that raises is reported to the model as a tool error, like any tool.
- Fields such as `evidence` or a verification command are not required, but they give the verifier something concrete to check.
- `output_model` and `verifier` are read at run time, so they can be assigned after construction (the eval judge does this).
- An accepted submission ends the loop with stop reason `answer_ready`; sinks receive the submission's JSON as the turn's final text.

## Why

Most harnesses end a run when the model replies without a tool call, so a model can stop early and claim success. The NOOA paper (arXiv 2607.20709, Sec 4.2) traced a large part of its SWE-bench / Terminal-Bench lead to requiring a validated, typed result instead: 77% of one comparison harness's failed Terminal-Bench runs ended within ten steps.

Source: `packages/agent_harness/src/agent_harness/base_tools/submit_result.py`, `loop.py`. Contracts: `tests/test_submit_result.py`.
