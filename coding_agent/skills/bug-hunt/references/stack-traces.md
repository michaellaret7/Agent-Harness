# Reading stack traces fast

A stack trace is a map. Read it right and it points at the bug in 10 seconds. Read it wrong and you waste 10 minutes.

## Python

### Anatomy

```
Traceback (most recent call last):
  File "/app/coding/__main__.py", line 47, in <module>
    main()
  File "/app/coding/__main__.py", line 31, in main
    agent.run(prompt)
  File "/app/agent/loop.py", line 88, in run
    response = self.client.send(messages)
  File "/app/agent/client.py", line 23, in send
    api_key = os.environ['ANTHROPIC_API_KEY']
KeyError: 'ANTHROPIC_API_KEY'
```

- **Bottom line is the exception** — type + message. Read this first.
- **Frames are oldest-first, newest-last.** "most recent call last" means the bug happened in the **bottom** frame.
- **Read bottom-up** for the *what*. Read **top-down** for the *how we got here*.

### Three-second triage

1. **Exception type** — tells you the bug category. `KeyError` → missing key. `AttributeError` → None or wrong type. `TypeError` → calling something wrong. `ImportError` → packaging / path. `RecursionError` → infinite recursion.
2. **Exception message** — names the thing. `'ANTHROPIC_API_KEY'` is the missing key. The message is usually enough to find the bug without reading frames.
3. **Bottom-most project frame** — skip stdlib (`site-packages/`, `python3.X/`) frames. The first project frame from the bottom is where *we* did the wrong thing.

### Common shapes

- **`KeyError: 'foo'`** → dict access; the dict doesn't have `foo`. Grep for `['foo']` in project code; the access site is the bug.
- **`AttributeError: 'NoneType' object has no attribute 'X'`** → something returned None and the caller didn't check. Look for what produces the None *upstream* of the failing line.
- **`TypeError: X() got an unexpected keyword argument 'Y'`** → API drift. The caller is using an old/new signature. Check git blame on both sides.
- **`TypeError: 'NoneType' object is not subscriptable`** → indexed into None. Same hunt as AttributeError.
- **`RecursionError`** → look for the recursive call; the base case is wrong or missing.
- **`ImportError: cannot import name X from Y`** → circular import or `X` was removed/renamed. Check the importing file and `Y`'s public surface.
- **`UnicodeDecodeError`** → file opened without `encoding="utf-8"`. Find the `open()` call in the trace.

### Chained exceptions

```
... ValueError: bad input
During handling of the above exception, another exception occurred:
... RuntimeError: failed
```

The **first** one is the root cause. The second is what the handler did wrong. Fix the root.

`raise X from Y` is the explicit form: Y is the cause.

### Suppressed frames

`pytest` shows shortened tracebacks by default. `pytest --tb=long` for full frames. `pytest --tb=native` for unprocessed Python format.

Async tracebacks may show `<frame omitted>` or interleave with event-loop machinery — focus on `await` sites and the awaited function's first project frame.

---

## JavaScript / TypeScript

### Anatomy

```
TypeError: Cannot read properties of undefined (reading 'name')
    at renderUser (src/ui/user.ts:42:18)
    at App.render (src/App.tsx:88:12)
    at processChild (react-dom.js:1234:5)
```

- **Top line is the exception.** Bottom frames are oldest. (Opposite of Python convention in framing, but Node prints newest-first.)
- **Skip node_modules / framework frames.** First project frame is the culprit.

### Common shapes

- **`Cannot read properties of undefined (reading 'X')`** → accessed `.X` on undefined. The thing was supposed to have `.X` and didn't.
- **`Cannot read properties of null`** → same, but null instead of undefined. Often means an API returned null vs. missing.
- **`is not a function`** → import default vs. named confusion, or wrong type.
- **`Unexpected token`** → syntax or JSON parse on non-JSON.

### Source maps

If line numbers point at minified code (`bundle.js:1:54321`), source maps are off. Either build with source maps or read the source file at the *logical* location, not the bundled one.

---

## When the trace lies

Tracebacks point at where the exception was **raised**, not always where the bug **is**.

- **Defensive raise** — `if foo is None: raise ValueError("foo required")`. The trace points at the `raise` line; the bug is wherever foo *should have been set* and wasn't. Trace it upstream.
- **Validation libraries** (`pydantic`, `zod`) raise at the model boundary. The bug is in whatever produced the invalid input.
- **Assertion frameworks** — the assertion line is the symptom; the bug is in the code under test.

Rule of thumb: if the failing line is a `raise`, `assert`, or validator call, the *real* bug is upstream. Walk the trace backwards through project frames until you find the first frame that's doing actual work (not just checking).
