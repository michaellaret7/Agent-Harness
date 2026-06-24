---
name: bug-hunt
description: Diagnose and fix bugs using a repro-first loop — reproduce, isolate, hypothesize, patch, verify. Use when the user reports a bug, a stack trace, a failing test, unexpected behavior, "X is broken", "why does Y happen", or any "this doesn't work" prompt. Use before writing a fix.
license: Apache-2.0
metadata:
  author: coding-agent
  version: "1.0"
  tags: ["debugging", "discipline", "core"]
---

# Bug Hunt

A disciplined loop for fixing bugs: **reproduce → isolate → hypothesize → patch → verify**. The point is to refuse to write a fix until the bug is reproducible on demand, and to refuse to declare done until the repro is green and nothing else regressed.

The single biggest debugging failure mode is jumping from symptom to fix without a repro. This skill exists to make that jump impossible.

## When to run

Trigger on:
- A reported bug, crash, stack trace, or "X is broken"
- A failing test (existing or newly written)
- Unexpected output, wrong return value, silent corruption
- Flaky behavior ("works sometimes")
- A regression after a recent change

Skip when:
- The user is asking how something *works*, not why it's broken — that's exploration, not debugging
- The fix is a one-character typo the user has already pinpointed (just apply it)

## The loop

Five phases. Do not skip ahead. If a later phase fails, return to an earlier one — don't push through.

### 1. Reproduce

**Goal:** a single command (or one-paragraph procedure) that makes the bug appear every time.

- Read the report twice. Extract: what was run, what was expected, what happened, the full error if any.
- Read the stack trace bottom-up: the innermost frame is where it blew up; the outer frames are how we got there.
- Build the smallest invocation that triggers the bug. Prefer (in order): an existing failing test → a new failing test → a `python -c "..."` one-liner → a `repro.py` script (see `assets/repro_template.py`).
- **Run the repro.** Confirm the bug fires. If it doesn't, the repro is wrong — fix it before going further.

If the bug is flaky, run the repro 5–10 times and record the hit rate. Flaky bugs need a *deterministic* repro before fixing — otherwise "verify" is meaningless. Search for shared state, ordering, timing, or randomness without a seed.

If you genuinely cannot reproduce, stop and ask the user for the missing piece (exact command, env, input file, OS). Do not guess.

### 2. Isolate

**Goal:** narrow the bug to the smallest possible region of code.

- From the stack trace, identify the *first* frame in project code (skip stdlib/third-party frames).
- Read that function. Read its callers. Read recent changes to it (`git log -p -- <file>` if git is available).
- Use `Grep` to find every callsite of the suspect function and every place the suspect variable is mutated.
- Bisect when useful: comment out half the offending function, see if the bug persists; repeat. Cheap and effective on long functions.
- For regressions: `git log --oneline -- <file>` to find recent changes, then `git show <sha>` to inspect them. A bug that worked yesterday and broke today usually has one obvious commit behind it.

By the end of this phase you should be able to point at ≤ 20 lines of code and say "the bug is in here."

### 3. Hypothesize

**Goal:** a single, testable claim about *why* the bug happens.

State it in one sentence: *"The bug happens because X."* If you can't, you're not ready to fix — go back to isolate.

A good hypothesis predicts something checkable beyond the original symptom. *"X is None because Y is never called when Z is empty"* predicts that adding a print before Y will show it skipped on the failing input. Verify the prediction before patching — a hypothesis that only explains the symptom is a guess.

Common shapes of real bugs (check these first):
- **Off-by-one** — `range`, slice bounds, loop terminators
- **None / missing key** — optional field treated as required, dict access without `.get`
- **Mutable default** — `def f(x=[]):` and friends
- **Aliasing** — two names pointing at the same list/dict, mutation surprises the other
- **Type coercion** — `"1" + 1`, `int(None)`, truthy/falsy on `0` / `""` / `[]`
- **Path / encoding** — Windows path separators, missing `encoding="utf-8"` on file I/O
- **Async / ordering** — awaited at the wrong place, fire-and-forget, race on shared state
- **Stale cache / import** — module-level state, cached property, `lru_cache` holding stale args
- **Off-by-context** — function works in isolation, fails when called from the real call site (look at *what's different*)

See `references/common-bugs.md` for the longer catalogue with repro shapes.

### 4. Patch

**Goal:** the smallest change that makes the repro pass.

- Touch the minimum number of lines. A 3-line fix is more reviewable and less risky than a 30-line refactor that happens to also fix the bug.
- Do not refactor in the same change. If you spot ugly code near the bug, note it and move on — refactors belong in a separate commit.
- Don't widen the fix to cover hypothetical related bugs. Fix *this* bug. YAGNI applies to defensive coding too.
- If the fix requires new error handling, the error message must name the actual condition ("config missing `api_key`"), not the symptom ("something went wrong").

### 5. Verify

**Goal:** the repro is green, and nothing else broke.

- Re-run the repro. It must pass.
- Run the full test suite (`pytest` or whatever the project uses). It must be green. A passing repro with broken tests elsewhere is not a fix — it's a swap of one bug for another.
- For flaky bugs: run the repro 10+ times. Once is not enough.
- Add a regression test if one doesn't exist. The repro from phase 1 is usually 80% of the regression test already — promote it into the suite.
- Sanity check: does the fix make sense to *explain*? If you can't write a one-sentence commit message that doesn't sound like "I changed some stuff and it works now", the fix is probably accidental and you don't actually understand the bug. Go back to phase 3.

## Output

When the bug is fixed, report in this shape:

```markdown
**Bug:** <one sentence>
**Root cause:** <one sentence — the *why*, not the *what*>
**Fix:** <files touched, lines changed>
**Repro:** <how to reproduce — command or test>
**Regression test:** <path to added/updated test, or "none — covered by existing test X">
**Verified:** repro green, full suite green (<N> tests).
```

If the bug turned out to be a misunderstanding (no actual bug), say so explicitly and explain the misunderstanding. Do not silently close out.

## Examples

### Example: stack trace bug

**Input:** "`agent` crashes on startup with `KeyError: 'ANTHROPIC_API_KEY'`."

**Loop:**
1. **Reproduce** — `python -m coding` → KeyError fires immediately. Repro: that one command.
2. **Isolate** — stack trace points at `agent/client.py:23`, `os.environ['ANTHROPIC_API_KEY']`. Grep for env var → only one read site.
3. **Hypothesize** — "The bug happens because `client.py` reads the env var with `os.environ[...]` (raises on missing) instead of `os.getenv(...)` (returns None), and `.env` is not loaded before this line."
4. **Patch** — call `load_dotenv()` at the top of `__main__.py` before importing `client`. 2 lines.
5. **Verify** — re-run, no crash. Full test suite green. Added `tests/test_startup.py::test_startup_with_missing_env` that mocks a missing env and asserts a clean error message.

### Example: flaky test

**Input:** "`test_tool_handler` fails ~1 in 5 runs in CI."

**Loop:**
1. **Reproduce** — `pytest tests/tool_handler -x --count 20`. Fails 3/20. Repro = that command.
2. **Isolate** — failures all involve `test_concurrent_dispatch`. Read it: spawns 4 threads writing to a shared `results` list, asserts length 4. List is `list`, no lock.
3. **Hypothesize** — "Race on `results.append` — Python's GIL makes `append` atomic in CPython, but the test also `sorts` `results` *during* appends from another fixture, which isn't atomic." Predict: removing the concurrent sort makes the flake disappear. Confirm.
4. **Patch** — move the sort to after `join()`. 1 line moved.
5. **Verify** — `--count 50`, 50/50 pass. Suite green.

## Edge cases

- **Can't reproduce.** Get the user's exact command, OS, Python version, and any input files. If it only repros for them, ask for a `pip freeze` and the failing input. Do not invent a repro.
- **Bug is in a dependency.** Confirm by repro-ing against the dep directly. If confirmed: pin around it, wrap it, or file upstream. Don't patch our code to cover for a library bug without saying so in the commit.
- **Bug is "by design" but user disagrees.** Surface the design intent (find the code/comment/doc that established it), then escalate the decision to the user. Not your call to silently change behavior.
- **Fix would touch >5 files.** Stop. Either the diagnosis is wrong (back to isolate) or this is a refactor masquerading as a bug fix. Split it.
- **The repro requires secrets / paid APIs.** Mock the boundary. The repro should not require real credentials — that's a sign the test surface is wrong.
- **Two bugs at once.** Fix them in separate commits, each with its own repro. Interleaving them makes both harder to verify.

## What NOT to do

- Don't propose a fix before you have a repro. Without a repro you cannot verify, and "I think this might fix it" is not engineering.
- Don't read 20 files when the stack trace pointed at 2. Follow the trace.
- Don't catch-and-ignore the exception to "make the test pass". That's not a fix, that's hiding.
- Don't add `try/except Exception` around the bug. Find what the actual exception is and handle *that*.
- Don't refactor the surrounding code in the same commit. Separate concern.
- Don't declare done without running the full suite. Local green ≠ global green.

## References

- `references/common-bugs.md` — longer catalogue of recurring bug shapes with repro patterns
- `references/stack-traces.md` — how to read Python and JS stack traces fast
- `assets/repro_template.py` — minimal template for a standalone repro script
