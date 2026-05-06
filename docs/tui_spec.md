# TUI Spec — `mini_agent`

A concrete design for a Textual-based TUI that wraps the existing `Agent`
loop without rewriting it. The spec is anchored to what already lives in
`agent/agent.py`, `agent/loop.py`, and `agent/tool_handler.py`; every
section names the file and symbol it touches.

---

## 1. Goals & non-goals

**Goals**
- Replace the current `print()`-based REPL in `agent/agent.py` with a
  Textual app that renders streamed assistant output, collapsible tool
  calls, and a status bar.
- Keep the existing `Agent` API (`add_tool`, `run`, `messages`,
  `tool_functions`, `tools`) usable from scripts and tests. The TUI is a
  *frontend*, not a fork.
- Make streaming feel correct under bursty token rates: no flicker, no
  partial-markdown jank, no torn frames.
- Render tool calls as first-class widgets (collapsed by default,
  expandable with status, args, and result).
- Headless fallback: if stdout isn't a TTY or `MINI_AGENT_NO_TUI=1`, run
  the existing line-mode loop unchanged.

**Non-goals (v1)**
- Multi-pane / split-window layouts. One vertical column.
- Inline image rendering (Sixel / Kitty). Defer.
- Server / client split (Toad-style). Defer.
- Conversation search across past sessions. Defer; just keep a JSONL
  transcript on disk so it's possible later.
- Permission prompts. The current tools are local and trusted; add when a
  network/exec policy actually exists.

---

## 2. Stack

| Concern | Library | Why |
|---|---|---|
| Layout, event loop, focus | **Textual** | 60fps compositor, `@work` async workers, snapshot tests, dev console. Async-first matches the streaming loop. |
| Markdown / syntax / diffs | **Rich** | Already Textual's renderer; `Markdown`, `Syntax`, `Table` cover everything we need. |
| Input editing | Textual `Input` (v1), upgrade to embedded **prompt_toolkit** later for vi/emacs, history-search, fuzzy `@`/`/` completion. |
| Diff (optional) | Shell out to `delta` if `shutil.which('delta')`; otherwise Rich `Syntax(... 'diff')`. |
| LLM client | unchanged — `agent/client.py` returns the OpenAI-compatible client. |

Add to `pyproject.toml`: `textual>=0.80`, `rich>=13.7`. Both are pure
Python and work on Windows / Git Bash / Windows Terminal.

---

## 3. File layout

```
agent/
  agent.py              # unchanged public API; gains an `events` hook (§5)
  loop.py               # refactored to yield events instead of print()
  tool_handler.py       # refactored to yield events; no print()
  client.py             # unchanged
  tui/                  # NEW — all UI code lives here
    __init__.py
    app.py              # AgentApp(App) — composes the screen
    events.py           # dataclasses for the event protocol (§5)
    widgets/
      __init__.py
      chat_log.py       # scroll region of message bubbles
      message.py        # one user / assistant turn
      tool_call.py      # collapsible tool-call card
      status_bar.py     # docked footer
      input_box.py      # the prompt input
    streaming.py        # ProducerConsumer buffer for markdown streaming
    transcript.py       # JSONL writer
docs/
  tui_spec.md           # this file
```

Nothing under `agent/tui/` is imported from `agent/agent.py` — the TUI
imports the agent, not the other way around. This keeps the headless
path clean.

---

## 4. Entry points

`agent/agent.py` keeps its `__main__` block but delegates:

```python
if __name__ == '__main__':
    import os, sys
    agent = Agent(provider='anthropic', model='claude-opus-4-7')
    if os.environ.get('MINI_AGENT_NO_TUI') or not sys.stdout.isatty():
        run_headless(agent)               # current while-loop, kept verbatim
    else:
        from agent.tui.app import AgentApp
        AgentApp(agent).run()
```

A new console script `mini-agent` in `pyproject.toml` calls the same
function so users don't need `python -m`.

---

## 5. Event protocol — the seam between agent and UI

Today `loop.py` and `tool_handler.py` both `print()` directly. We replace
that with a single `Iterable[Event]` that the TUI consumes. The agent
loop knows nothing about Textual.

`agent/tui/events.py`:

```python
from dataclasses import dataclass

@dataclass(frozen=True) class TurnStart: pass
@dataclass(frozen=True) class ReasoningDelta:    text: str
@dataclass(frozen=True) class ContentDelta:      text: str
@dataclass(frozen=True) class ToolCallStart:     id: str; name: str
@dataclass(frozen=True) class ToolCallArgsDelta: id: str; text: str    # streamed JSON
@dataclass(frozen=True) class ToolCallReady:     id: str; args: dict   # parsed
@dataclass(frozen=True) class ToolCallResult:    id: str; ok: bool; output: str; ms: int
@dataclass(frozen=True) class TurnEnd:           content: str
@dataclass(frozen=True) class UsageUpdate:       prompt_tokens: int; completion_tokens: int
@dataclass(frozen=True) class Error:             message: str
```

`agent/loop.py` becomes a generator:

```python
def execution_loop(agent, model, max_iters=10) -> Iterator[Event]:
    for _ in range(max_iters):
        yield TurnStart()
        content, tool_calls = yield from _stream_turn(agent, model)
        agent.messages.append({'role': 'assistant', 'content': content,
                               **({'tool_calls': tool_calls} if tool_calls else {})})
        if not tool_calls:
            yield TurnEnd(content); return
        yield from _run_tools(agent, tool_calls)
```

`_stream_turn` and `_run_tools` `yield` events instead of printing.
`call_llm_stream`'s loop body translates SDK chunks 1:1 into
`ReasoningDelta` / `ContentDelta` / `ToolCallArgsDelta` / etc.

A thin `run_headless()` consumes the same iterator and prints, so the
two frontends share one source of truth.

---

## 6. Layout

A single docked column:

```
┌───────────────────────────────────────────────────────────┐
│ Header: model · cwd · git branch                          │  Header (Textual built-in)
├───────────────────────────────────────────────────────────┤
│                                                           │
│  ChatLog (VerticalScroll)                                 │
│   ├─ MessageView (user)                                   │
│   ├─ MessageView (assistant — streaming markdown)         │
│   │    └─ ToolCallCard #1  [▸ Bash · running]             │
│   │    └─ ToolCallCard #2  [▾ ReadFile · ok · 0.04s]      │
│   │           args: {"file_path": "agent/agent.py"}       │
│   │           result: ──────────────────────────────────  │
│   │                  1   from __future__ import ...       │
│   │                  ...                                  │
│   └─ MessageView (assistant)                              │
│                                                           │
├───────────────────────────────────────────────────────────┤
│ InputBox (multiline, Enter=submit, Shift+Enter=newline)   │
├───────────────────────────────────────────────────────────┤
│ StatusBar: tokens 1.2k/4.8k · ctx 12% · $0.03 · ⏎ submit  │  Footer
└───────────────────────────────────────────────────────────┘
```

Composed in `AgentApp.compose()`:

```python
def compose(self) -> ComposeResult:
    yield Header(show_clock=False)
    yield ChatLog(id='chat')
    yield InputBox(id='input')
    yield StatusBar(id='status')
```

---

## 7. Widgets

### 7.1 `ChatLog` (`widgets/chat_log.py`)
- Subclass of `VerticalScroll`.
- `append_user(text)` → mounts a `MessageView(role='user', text=text)` at
  the bottom and scrolls to it.
- `start_assistant()` → mounts an empty `MessageView(role='assistant')`
  and stores a reference as `self.current`.
- All streaming events are routed to `self.current` (see §8).

### 7.2 `MessageView` (`widgets/message.py`)
- Two regions: a small role label (`you` / `claude` / `tool`) and a
  `Markdown` widget.
- Uses the **block-streaming** pattern: parse the buffer into top-level
  blocks (paragraph, fence, list, table); only the *last* block is
  re-rendered as new tokens arrive; earlier blocks are frozen Markdown
  widgets. This is the McGugan pattern from
  *Efficient streaming of Markdown in the terminal*.
- Public methods:
  - `append_content(delta: str)` — push text into the streaming buffer.
  - `append_reasoning(delta: str)` — push into a separate dim,
    italicized region docked above the main content; collapsible via
    Ctrl+R.
  - `mount_tool_call(card: ToolCallCard)` — embeds the card inline.

### 7.3 `ToolCallCard` (`widgets/tool_call.py`)
- A Textual `Collapsible` with custom title.
- Title format: `[▸|▾] {name} · {state} · {duration}`
  - `state ∈ {pending, running, ok, error}` driven by a reactive var.
  - States map to colors: pending=dim, running=yellow, ok=green,
    error=red.
- Body shows `args` (Rich `Syntax(..., 'json')`) and `result` (auto-detected
  rendering: diff lexer if it looks like a diff, else `Syntax` with
  language inferred from the tool name — e.g. `Bash` → console;
  `ReadFile` → infer from extension; `Grep` → console).
- Result is truncated to 200 lines in the collapsed pane; full text on
  expand. Truncation uses Rich `Group` with a `[+ N more lines]` footer.

### 7.4 `InputBox` (`widgets/input_box.py`)
- Wraps Textual's `TextArea`.
- Bindings:
  - `enter` → submit (if not in a code fence)
  - `shift+enter` → literal newline
  - `ctrl+c` → cancel current turn (see §10)
  - `ctrl+d` on empty buffer → quit
  - `up` / `down` on first/last line → cycle history (`~/.mini_agent/history`)
- Posts an `InputSubmitted(text)` Textual message that `AgentApp`
  handles by spawning the agent worker.

### 7.5 `StatusBar` (`widgets/status_bar.py`)
- Reactive vars: `model`, `prompt_tokens`, `completion_tokens`,
  `context_pct`, `cost_usd`, `cwd`, `git_branch`, `state`
  (`idle | streaming | tool | cancelling`).
- Re-renders on reactive change, not on a timer; the only timer is for
  the spinner glyph during `state != idle`.

---

## 8. Streaming pipeline

The producer/consumer buffer keeps token rate decoupled from render
rate. Source: McGugan's *Efficient streaming of Markdown* — apply the
same pattern in `agent/tui/streaming.py`.

```
LLM SDK iterator (sync, blocking)
   │  in worker thread (Textual @work(thread=True))
   ▼
asyncio.Queue[Event]
   │  consumed by AgentApp message pump
   ▼
ChatLog.current.append_*()
   │  Textual reactive update
   ▼
Compositor (Textual) → Synchronized Output → terminal
```

Rules:
1. Producer (the SDK iterator running in `call_llm_stream`) translates
   each chunk into an `Event` and puts it on the queue. It never touches
   widgets.
2. Consumer batches events: drain the queue every 16ms (60fps), apply
   in order, then await one tick. This absorbs >100 tok/s without
   per-token re-renders.
3. Markdown re-parse happens only on the *last* block; earlier blocks
   are immutable. A block is "closed" when a blank line follows or the
   stream ends.
4. Code fences are detected lexically; while inside a fence the buffer
   renders as a single `Syntax` widget without trying to interpret
   markdown inside.

---

## 9. Hook points in existing code

Concrete diffs to land first (in this order):

1. **`agent/loop.py`** — convert `call_llm_stream` and `execution_loop`
   to generators of `Event`. Delete the `print()` calls. Keep the dict
   shape for `tool_calls` exactly the same so `tool_handler` doesn't
   change interface.
2. **`agent/tool_handler.py`** — change `execute()` to a generator
   yielding `ToolCallStart` → `ToolCallReady` → `ToolCallResult`. Time
   each call with `time.perf_counter()` for the `ms` field. Stop
   printing.
3. **`agent/agent.py`** — `run(prompt)` returns the iterator
   (`Iterator[Event]`). Add a `run_blocking(prompt) -> str` shim that
   drains it for old callers.
4. **New `agent/tui/`** — implement widgets and `AgentApp`.
5. **Headless** — `run_headless(agent)` as a 30-line function in
   `agent/agent.py` that consumes the iterator and prints; preserves
   today's behavior bit-for-bit.

---

## 10. Cancellation

Two-step Ctrl+C semantics, documented in the status bar hint:

1. **First Ctrl+C while streaming** → set a `cancel_token`. The producer
   worker checks it between SDK chunks; on next check it stops iterating
   the stream, drains pending tool calls' subprocess (sends SIGINT to
   the bash child), and emits `Error("cancelled")` then `TurnEnd("")`.
   State returns to `idle`.
2. **Second Ctrl+C within 1s** → `app.exit(1)`. Hard quit.
3. **Ctrl+D on empty input** → `app.exit(0)`.

Tools are cooperative: `Bash` already uses `subprocess.run` with a
timeout; extend it to accept a `cancel_event: threading.Event` and poll
between reads. `ReadFile` / `Glob` / `Grep` are fast enough to ignore.

---

## 11. Transcript

Every event is appended to
`~/.mini_agent/transcripts/{ISO8601}.jsonl`. One JSON object per line,
shape `{ "t": iso_ts, "event": "ContentDelta", **fields }`. Used for:
- post-mortem debugging,
- future "resume session" feature,
- snapshot tests (compare expected vs. actual event stream).

---

## 12. Theming and accessibility

- Honor `NO_COLOR`: at startup, if set, switch Textual to monochrome
  theme and tell Rich to skip color (`Console(no_color=True)`).
- Honor `FORCE_COLOR=1`: opposite.
- `$TERM=dumb` → headless mode.
- Detect terminal background via Textual's built-in
  `App.dark` reactive; expose `mini-agent --light` / `--dark`.
- Don't use color *alone* to convey state; tool-call cards also show a
  text label (`running` / `ok` / `error`) and a glyph (`◐` / `✓` / `✗`).
- Spinner only when `state != idle`; never when output is piped.

---

## 13. Testing

- **Snapshot tests** with `pytest-textual-snapshot` for: empty start,
  one user message, one assistant streaming reply (mid-stream), one
  tool call running, one tool call ok, one tool call error, long
  result truncation, light/dark themes.
- **Pilot tests** for input handling: enter submits, shift+enter inserts
  newline, ctrl+c cancels, up/down cycles history.
- **Generator tests** for `execution_loop` consume the iterator with a
  fake `OpenAI` client; assert the exact event sequence. No Textual
  needed.
- **Streaming buffer tests**: feed the buffer a known token stream and
  assert that closed blocks are immutable across renders.

---

## 14. Phasing

Keep PRs small; each phase ends with a runnable agent.

| Phase | Scope | Done when |
|---|---|---|
| **0** | Refactor: events generator, headless consumer | `python -m agent.agent` behaves identically; tool-handler tests pass. |
| **1** | Skeleton TUI: `AgentApp`, `ChatLog`, `MessageView` (no streaming yet — wait for `TurnEnd` then render full content) | Can chat one round in Textual. |
| **2** | Streaming markdown with the producer/consumer buffer | No flicker on >100 tok/s synthetic stream. |
| **3** | `ToolCallCard` with collapse / expand and state colors | Bash + ReadFile + EditFile render correctly. |
| **4** | StatusBar (model, tokens, cwd, branch) | Token counter updates live. |
| **5** | Input upgrade: history file, multiline, ctrl+c semantics | Two-step cancel works against a real SDK call. |
| **6** | Theming, NO_COLOR, snapshot test suite | CI runs snapshots on PR. |

Phase 0 alone is worth shipping — it cleans up the print-debug
sprawl across `loop.py` and `tool_handler.py` even if the TUI never
follows.

---

## 15. Open questions

1. **Reasoning blocks**: Anthropic's `reasoning_content` arrives
   interleaved with `content`. Render in the *same* `MessageView` as a
   dim italic prefix that collapses on `TurnEnd`, or a separate
   sibling? Decision: same view, separate region docked at top, default
   collapsed once the turn ends.
2. **Tool args streaming**: today the loop accumulates fragments and
   only parses JSON at the end. Worth showing the raw streaming JSON
   in the card while it builds? Decision: yes, render fragments live as
   gray text; replace with formatted `Syntax(... 'json')` once parsed.
3. **Multiple tool calls in one turn**: render as siblings inside the
   same `MessageView`, in arrival order. They run sequentially today
   (`tool_handler.execute` is a for-loop) — keep that; parallelism
   would be a follow-up.
4. **Pasted large content**: bracketed paste comes through Textual as
   one input event already. v1 just inserts it; v2 may detect >2KB and
   offer to attach as a file via a modal.

---

## 16. References

The patterns above come from a literature review (full URLs in chat
history). The non-negotiable five for implementers:

1. McGugan, *7 Things I've learned building a modern TUI Framework* —
   the synchronized-output / single-write rules.
2. McGugan, *Efficient streaming of Markdown in the terminal* — the
   block-streaming buffer pattern used in §8.
3. Textual, *Anatomy of a Textual User Interface* — `compose()` +
   `@work` worker pattern.
4. clig.dev — output-stream and color-respect rules used in §12.
5. Sampath et al., *Accessibility of CLIs* (CHI '21) — frames §12.
