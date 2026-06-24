# Code Smells Catalogue

Smells the recon script looks for, why they matter, and how to confirm they're real (vs. false positives).

## Table of Contents
1. [Python smells](#python-smells)
2. [JavaScript / TypeScript smells](#javascript--typescript-smells)
3. [Cross-language smells](#cross-language-smells)
4. [False positives to avoid](#false-positives-to-avoid)

---

## Python smells

### Bare `except:`

**Pattern:** `except:` (no exception class)

**Why it matters:** Catches `KeyboardInterrupt` and `SystemExit`, hiding bugs and breaking Ctrl-C.

**Confirm:** Sometimes legitimate in top-level signal handlers and `__del__` methods. Check context.

```python
# Bad
try:
    do_thing()
except:
    pass

# Acceptable (cleanup that must not raise)
def __del__(self):
    try:
        self.cleanup()
    except Exception:
        pass
```

### Missing `encoding="utf-8"` on file I/O

**Pattern:** `open(path)`, `open(path, "r")`, `open(path, "w")` without `encoding=`; `Path.write_text(...)` / `read_text(...)` without `encoding=`.

**Why it matters:** On Windows, defaults to `cp1252`. Causes `UnicodeDecodeError` on any non-ASCII content. Bites cross-platform projects silently.

**Confirm:** True positive unless the file is being opened in binary mode (`"rb"`, `"wb"`).

### `print()` debugging in non-CLI code

**Pattern:** `print(` in modules that aren't CLI entry points or scripts.

**Why it matters:** Pollutes stdout, breaks programs that pipe their output, indicates abandoned debugging.

**Confirm:** Check if the file is a CLI (has `if __name__ == "__main__"` or is in `scripts/`). If so, `print` is fine.

### Mutable default arguments

**Pattern:** `def f(x=[]):`, `def f(x={}):`, `def f(x=set()):`

**Why it matters:** Python evaluates defaults once; the list/dict/set is shared across calls.

**Confirm:** Always a bug. Fix is `def f(x=None): x = x or []`.

### Empty `__init__.py` where exports expected

**Pattern:** Package directory with submodules but `__init__.py` is empty or only has comments.

**Why it matters:** Forces consumers to import via `package.submodule.thing` instead of `package.thing`. Often unintentional.

**Confirm:** Sometimes deliberate (namespace packages, or strict isolation). Look at how the package is imported elsewhere.

### Missing `py.typed` marker

**Pattern:** Package ships type hints (`*.pyi` files or annotated `*.py`) but no `py.typed` file.

**Why it matters:** Downstream type checkers (mypy, pyright) ignore the package's types without this marker per PEP 561.

**Confirm:** True positive if the package has annotations and is meant to be installed.

### Wildcard imports

**Pattern:** `from module import *`

**Why it matters:** Pollutes namespace, makes static analysis impossible, breaks on rename.

**Confirm:** Sometimes acceptable in `__init__.py` re-exports if `__all__` is defined in the source module. Otherwise a smell.

### `# type: ignore` clusters

**Pattern:** Multiple `# type: ignore` comments in one file.

**Why it matters:** Indicates a fragile boundary where types don't line up. Often a sign of an untyped dependency or a hack around a real type bug.

**Confirm:** Look at what's being ignored. `# type: ignore[import-untyped]` for a known-untyped lib is fine. `# type: ignore` (blanket) on logic code is suspect.

---

## JavaScript / TypeScript smells

### `any` type usage

**Pattern:** `: any`, `as any`, `Array<any>`

**Why it matters:** Disables type checking; usually a sign someone gave up.

**Confirm:** Look at context. `JSON.parse` results legitimately produce `any` until validated.

### `@ts-ignore` / `@ts-expect-error` clusters

**Pattern:** Multiple suppression comments in one file.

**Why it matters:** Same as Python's `# type: ignore` — indicates type-system mismatch.

### `console.log` in production code

**Pattern:** `console.log(` outside of test files and explicit logging modules.

**Why it matters:** Logging discipline. Production should use a proper logger.

### `eslint-disable` clusters

**Pattern:** `// eslint-disable`, `/* eslint-disable */`

**Why it matters:** Lint rules disabled often hide real issues. Per-line disables for genuine exceptions are fine; whole-file disables rarely are.

### Missing `await` on async functions

**Pattern:** Calling an `async` function without `await` or `.then()`, ignoring the returned promise.

**Why it matters:** Silent failures, race conditions.

**Confirm:** Static detection is unreliable; flag suspicious patterns but verify.

---

## Cross-language smells

### TODO / FIXME / XXX / HACK density

**Pattern:** `TODO`, `FIXME`, `XXX`, `HACK` in comments.

**Severity by marker:**
- `TODO` — known incomplete work, low urgency
- `FIXME` — known bug, should be fixed
- `XXX` — unclear / dangerous, high urgency
- `HACK` — known kludge, technical debt

**Report format:** count per marker per file. Don't list every occurrence.

### Files >500 LOC

**Why it matters:** Hard to hold in working memory, usually doing too many things.

**Confirm:** Some files legitimately large — generated code, data tables, comprehensive enums. Filter those out before flagging.

### Hardcoded credentials / URLs

**Pattern:** Strings matching `api_key=`, `password=`, `secret=`, http(s) URLs to non-localhost hosts in source.

**Why it matters:** Credentials in source = credential leak on commit. Hardcoded URLs = environment coupling.

**Confirm:** Test fixtures and example values are common false positives. Check the value: `"sk-..."` is real, `"your-api-key-here"` is documentation.

### Duplicate dependencies

**Pattern:** Same package listed twice (e.g., once in `dependencies` and once in `devDependencies`), or two packages providing the same functionality (`requests` + `httpx`, `moment` + `date-fns`).

**Why it matters:** Bloat, version conflicts, indecision.

---

## False positives to avoid

The recon script is a heuristic tool. These patterns trigger frequent false positives:

| Pattern | Why it false-positives |
|---------|------------------------|
| `print(` in scripts | CLI tools legitimately print |
| `bare except` in `__del__` | Destructors must not raise |
| Large files in `migrations/` | Auto-generated, not hand-written |
| `# type: ignore` next to imports | Often unavoidable for untyped libs |
| `console.log` in `bin/` scripts | CLI tools legitimately log |
| Hardcoded `localhost` URLs | Dev defaults, not real secrets |
| `password` in test fixtures | Test data |

**Rule:** when uncertain, mention the count and the example file, but don't claim severity. Let the human judge.
