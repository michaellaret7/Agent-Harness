# Common bug shapes

Catalogue of recurring bug patterns. Use during the **hypothesize** phase: scan this list, see if your symptom matches a shape, then check that shape first. Faster than reasoning from scratch.

Each entry: the shape, the symptom it produces, the cheap check that confirms or rejects it.

---

## Off-by-one

**Shape:** loop bound, slice index, or range terminator is one off.
**Symptom:** last item missing, first item duplicated, `IndexError` on the boundary case.
**Check:** run the repro on inputs of length 0, 1, 2, and N. Off-by-ones almost always show on length 0 or 1.

```python
# Classic
for i in range(len(xs) - 1):  # skips last
for i in range(len(xs) + 1):  # IndexError on last iter
xs[0:len(xs)-1]               # drops last
```

## None / missing key

**Shape:** optional value treated as required.
**Symptom:** `AttributeError: 'NoneType' has no attribute X`, `KeyError`, `TypeError: 'NoneType' is not subscriptable`.
**Check:** trace back to where the value is *produced*. Is there a path that returns `None` / doesn't set the key? If yes, that's the bug — either guarantee the value or handle the absence.

```python
config["api_key"]          # KeyError if missing — use .get() + explicit error
user.profile.email         # crashes if profile is None
result = maybe_fetch()     # returns None on miss, caller forgot to check
return result.value
```

## Mutable default argument

**Shape:** `def f(x=[]):` or `def f(x={}):`. Default is created **once** at function definition, shared across calls.
**Symptom:** function "remembers" data between calls; tests pass alone but fail when run together.
**Check:** grep for `def .*=\[\]` and `def .*=\{\}`. Both are bugs unless explicitly intended.

```python
def append(item, into=[]):     # BUG — `into` persists across calls
    into.append(item)
    return into
```

Fix: `def append(item, into=None): into = [] if into is None else into`

## Aliasing

**Shape:** two names refer to the same list/dict; mutation through one surprises the other.
**Symptom:** "I only modified A but B changed."
**Check:** is the second reference assigned with `=`, `.copy()`, or `copy.deepcopy()`? Bare `=` is aliasing, not copying.

```python
b = a              # alias
b = a.copy()       # shallow copy — nested objects still aliased
b = deepcopy(a)    # full copy
```

## Type coercion / truthiness

**Shape:** Python's "falsy" includes `0`, `""`, `[]`, `{}`, `None`, `False`. `if x:` treats them all the same.
**Symptom:** logic skips valid zero/empty inputs; defaults applied when user explicitly passed `0`.
**Check:** `if x is None:` if you mean "missing"; `if not x:` if you mean "empty or missing"; `if x == 0:` if you mean zero.

```python
def render(width=None):
    if not width:           # BUG — width=0 hits the default
        width = 80
```

## Path / encoding (Windows in particular)

**Shape:** hardcoded `/`, missing `encoding=`, assuming POSIX paths.
**Symptom:** "works on my Mac, breaks on Windows", `UnicodeDecodeError` reading a file with non-ASCII content.
**Check:**
- `open(path)` without `encoding="utf-8"` — Windows default is locale-dependent (often `cp1252`).
- String-concatenated paths (`base + "/" + name`) — use `Path(base) / name`.
- Git Bash paths (`/c/Dev/...`) on Windows — pass through normalizer before `pathlib`.

## Async / ordering

**Shape:** coroutine not awaited, fire-and-forget without `gather`, race on shared mutable state.
**Symptom:** "the function returns immediately and the result is empty / wrong", warnings about un-awaited coroutines.
**Check:** grep for `async def` calls without `await`. Any `asyncio.create_task` whose result is never collected.

## Stale cache / module-level state

**Shape:** `@lru_cache`, `@functools.cache`, module-level dict that accumulates, singleton with mutable state.
**Symptom:** behavior depends on test order; first run differs from second; "it works after a restart".
**Check:** any global state? Any decorator that caches? Tests should not require process restart between them.

## Off-by-context

**Shape:** function works in isolation, fails when called from the real call site.
**Symptom:** unit test green, integration test red.
**Check:** what's different between the test invocation and the real one? Common culprits: working directory, env vars, current event loop, sys.path, presence of side-effect imports.

## Silent shadowing

**Shape:** local variable, function name, or import shadows a name from an outer scope.
**Symptom:** function does nothing / always returns the same value / mysterious `NameError` on second call.
**Check:** in the suspect function, grep its local names against the module's imports and globals. A function named `list` shadows the builtin.

```python
def process(list):       # shadows builtin `list`
    return list(filter(...))   # TypeError: 'list' object is not callable
```

## Integer division / float precision

**Shape:** `/` vs `//`, naive equality on floats.
**Symptom:** "the value is 2.9999999 instead of 3", division returns float when int expected.
**Check:** any `==` between computed floats — replace with `math.isclose`. Any `/` that should be `//` (e.g., indexing).

## Exception swallowing

**Shape:** `except:` or `except Exception:` with no logging, no re-raise.
**Symptom:** "it just silently does nothing"; bugs persist invisibly for weeks.
**Check:** grep `except:` and `except Exception:`. If the body is `pass` or just returns a default, the real error is being hidden — log it or narrow the except.

---

## Process tip

If your symptom doesn't match anything here, the bug is probably either:
1. **Domain-specific** — you need to read the surrounding code, not pattern-match.
2. **A combination** — two of the above interacting (e.g., aliasing + mutable default).

Don't force a match. If nothing fits, just isolate harder.
