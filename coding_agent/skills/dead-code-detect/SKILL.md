---
name: dead-code-detect
description: Scan a codebase for unused, stale, or dead code causing bloat — unused imports, orphaned functions/classes, dead branches, empty modules, unused dependencies. Use when asked to "clean up", "find dead code", "remove unused", "trim bloat", "audit for stale code", or before a release.
license: Apache-2.0
metadata:
  author: coding-agent
  version: "1.0"
  tags: ["cleanup", "maintenance", "code-health"]
---

# Dead Code Detect

Scan a codebase for unused, stale, or orphaned code that adds noise and bloat without contributing anything. Produces ranked findings with confidence levels — high-confidence items can be deleted confidently, low-confidence items need a human to confirm.

The goal is real bloat reduction, not cosmetic linting. A function that's unused and 80 lines long is worth flagging. A missing `__all__` is not.

## When to run

Trigger on:
- "Find dead code", "clean up unused", "trim bloat", "what can I delete"
- "Audit for stale code", "remove unused imports/functions/deps"
- Before a release, refactor, or migration
- After a major feature removal or rewrite

Skip when:
- The user wants style/lint fixes (that's ruff/flake8 territory)
- Narrow task: "is function X used?" — just grep it, no need for a full scan

## The scan

### Phase 1: Map the project

Before scanning anything, understand what's in play:

1. **Identify project type** — `pyproject.toml` / `setup.py` / `requirements.txt` / `package.json`?
2. **Find all source files** — `Glob` for `**/*.py` (or `**/*.ts`, etc.), excluding heavy dirs (`.venv`, `node_modules`, `.git`, `__pycache__`, `build`, `dist`, `.tox`, `*.egg-info`).
3. **Identify entry points** — `__main__.py`, scripts listed in `pyproject.toml [project.scripts]`, setup entry points, `cli.py`, `app.py`, any file that serves as an API boundary. These are roots — code reachable from them is alive.
4. **Check test dirs** — `tests/`, `test_*.py`. Tests exercise production code, but test-only helpers are fair game if nothing calls them.

### Phase 2: Run the detectors

Run each detector below. Each produces findings with a confidence tag:

- **🔴 HIGH** — almost certainly dead, safe to delete
- **🟡 MEDIUM** — likely dead but needs a quick human check
- **🟢 LOW** — possible dead code, needs verification

#### 2a. Unused imports

Use `Grep` and file reading to find `import X` / `from X import Y` statements, then check if `X` or `Y` appears elsewhere in the same file.

**Safe to skip (NOT dead):**
- `__init__.py` imports that re-export names (check `__all__` or if other files import from this package)
- Imports used only for type annotations in string form (`if TYPE_CHECKING` blocks)
- Side-effect imports (`import module_for_side_effects  # noqa`)
- Re-exports in `__init__.py` where downstream code does `from package import name`

Confidence: 🔴 HIGH if name doesn't appear anywhere else in the file. 🟡 MEDIUM if it's in an `__init__.py`.

#### 2b. Orphaned functions and methods

For each `def` / `async def` in the codebase:

1. Extract the function name.
2. `Grep` the entire project for that name (excluding the definition itself).
3. If zero hits outside the definition → likely orphaned.

**Safe to skip (NOT dead):**
- Entry points identified in Phase 1
- `__init__`, `__str__`, `__repr__`, and other dunder methods
- Methods on ABCs / Protocols (they define an interface, not usage)
- `@abstractmethod` decorated methods
- Test functions (`test_*`, `*_test`) — they're called by the test runner
- Pytest fixtures (`@pytest.fixture`) — called by name from test parameters
- Functions in `__all__` (public API surface)
- Decorators / callbacks registered via `@app.route`, `@cli.command`, etc.

Confidence: 🔴 HIGH if name is unique and has zero external references. 🟡 MEDIUM if it's a common name (e.g., `run`, `process`, `handle`) that might be called dynamically.

#### 2c. Orphaned classes

Same approach as functions: define → grep → count references.

**Safe to skip:**
- Exception classes (referenced in `raise` and `except` elsewhere)
- Base classes and ABCs
- Pydantic/dataclass models that are deserialized from external input
- Classes registered in `__all__`
- Django/Flask models, serializers, views (framework-discovered)

Confidence: 🔴 HIGH if class name is unique with zero references. 🟡 MEDIUM if subclassed.

#### 2d. Dead branches and commented-out code

Look for:
- `if False:` / `if True:` blocks (literal conditionals)
- Large blocks (>3 lines) of commented-out code
- `# TODO: remove`, `# DEPRECATED`, `# HACK: delete this` comments near code blocks
- `pass` in non-stub functions (function body is just `pass` but the function is defined)
- `return  # unreachable` or code after unconditional `return`/`raise`

Confidence: 🔴 HIGH for literal `if False:`. 🟡 MEDIUM for commented-out blocks (might be intentional documentation).

#### 2e. Empty or near-empty modules

Files that are:
- Completely empty
- Only contain a docstring and/or `pass`
- Only have imports that are themselves unused (cascading from 2a)

**Safe to skip:**
- `__init__.py` (often intentionally minimal)
- Stub files (`*.pyi`)
- Placeholder files with a clear TODO comment indicating planned work

Confidence: 🔴 HIGH for truly empty non-init files. 🟡 MEDIUM if the file has any content at all.

#### 2f. Unused dependencies

Check `requirements.txt`, `pyproject.toml [project.dependencies]`, or `setup.py install_requires`. For each listed package, grep the codebase for its import name.

**Tricks to watch for:**
- Import name ≠ package name (`python-dateutil` → `import dateutil`, `Pillow` → `import PIL`, `scikit-learn` → `import sklearn`, `PyYAML` → `import yaml`, `python-dotenv` → `import dotenv`)
- Dependencies used only by tools (`pytest`, `mypy`, `ruff`, `black`) — these are dev deps, not dead code
- Dependencies used in config but not Python imports (`gunicorn`, `celery`, `supervisor`)

Confidence: 🔴 HIGH if package is completely absent from all source code and tool configs. 🟡 MEDIUM if it might be a transitive dependency of another listed package.

#### 2g. Unreachable exports

Functions/classes decorated with public decorators but never wired into routes, commands, or event handlers. Common in web frameworks where a view is defined but never added to `urls.py` or a router.

Confidence: 🟡 MEDIUM — requires understanding the framework's discovery mechanism.

### Phase 3: Report

Produce a structured report grouped by category, sorted by impact (biggest wins first — large functions/files before small ones).

Use this format:

```markdown
## Dead Code Report

**Project:** <name> | **Scanned:** <N files, N lines>

### Summary
- 🔴 High confidence: N findings
- 🟡 Medium confidence: N findings
- Estimated removable lines: ~N

### 🔴 Unused Imports
| File | Import | Line |
|------|--------|------|
| path/file.py | `from x import y` | 3 |

### 🔴 Orphaned Functions
| File | Function | Lines | Last modified |
|------|----------|-------|---------------|
| path/file.py | `unused_func` | 45-80 | 2024-01-15 |

### 🟡 Possible Dead Code
...
```

### Phase 4: Cleanup (only if asked)

Do **not** auto-delete. When the user confirms, apply deletions one category at a time:

1. Delete the confirmed findings.
2. Run the test suite after each batch.
3. If tests fail, revert that batch and re-examine — the code wasn't dead after all.

## Common package name → import name mismatches

See `references/import-names.md` for the full list of packages where the install name differs from the import name.

## Edge cases

- **Monorepo / multi-package** — scan each package independently. A function in package A might be used by package B. Cross-package imports are a common source of false positives.
- **Plugin architectures** — code discovered by naming convention or entry points, not explicit import. Look for `load_entry_point`, `importlib.import_module`, or dynamic `getattr` patterns. Flag these as 🟢 LOW, not HIGH.
- **Generated code** — files with a "generated by" header or in a `generated/` dir. Skip entirely unless the user explicitly wants them scanned.
- **Protobuf / schema files** — definitions that look unused but are consumed by code generators. Skip `*.proto`, `*.graphql`, `openapi.yaml`.
