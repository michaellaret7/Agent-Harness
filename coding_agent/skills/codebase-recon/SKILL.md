---
name: codebase-recon
description: Produce a structured brief of an unfamiliar codebase covering layout, entry points, dependencies, config surface, test coverage, and areas of interest. Use when starting work on a repo for the first time, when asked to "review", "understand", "explore", or "get oriented in" a project, or before any non-trivial refactor.
license: Apache-2.0
metadata:
  author: mini-agent
  version: "1.0"
  tags: ["meta", "exploration", "onboarding"]
---

# Codebase Recon

Produce a consistent, structured brief of an unfamiliar codebase before doing real work in it. Replaces ad-hoc `tree` + `grep` exploration with a repeatable checklist and a deterministic recon script.

## When to run

Trigger this skill when:
- Opening a repo for the first time in a session
- The user asks to "review", "understand", "explore", "get oriented in", or "audit" a codebase
- Before any refactor, migration, or architectural change
- Before answering a broad question like "how does X work in this project?"

Skip when the task is narrow and local (e.g., "fix this typo", "add a print statement here").

## Quick start

```bash
python scripts/recon.py <repo-path> --format brief
```

That single command produces the structured brief. Read it, then dive into specific files only as the task requires.

## The recon checklist

Always answer these questions, in order. The script answers most of them; fill gaps by reading files.

### 1. Project identity
- **Name + purpose** — from `README.md` first paragraph, `pyproject.toml` `[project]`, or `package.json`
- **Language + version** — `.python-version`, `pyproject.toml` `requires-python`, `package.json` `engines`
- **Framework / stack** — inferred from top dependencies

### 2. Layout
- Directory tree, depth ≤ 3, with annotations (what each top-level dir is for)
- Flag unusual structure: code in repo root, deeply nested packages, multiple unrelated projects in one repo

### 3. Entry points
- **CLI scripts** — `pyproject.toml` `[project.scripts]`, `package.json` `bin`, executables in `scripts/`
- **`__main__` blocks** — files with `if __name__ == "__main__"`
- **Web apps** — `FastAPI()`, `Flask(...)`, `app =` patterns
- **Libraries** — public surface from `__init__.py` `__all__` or top-level exports

### 4. Configuration surface
- Env vars referenced in code (`os.environ`, `os.getenv`, `process.env`)
- `.env.example` / `.env.template` keys
- Config files: `*.toml`, `*.yaml`, `*.ini`, `config/`
- Hardcoded paths or URLs (suspicious)

### 5. Dependencies
- Direct production deps with versions
- Direct dev deps
- Lockfile present? (`uv.lock`, `poetry.lock`, `package-lock.json`)
- Anything obviously outdated, deprecated, or pinned to an unsafe version

### 6. Tests
- Test directory location and framework (`pytest`, `unittest`, `vitest`, ...)
- Test file count vs source file count (rough ratio)
- CI config present? (`.github/workflows/`, `.gitlab-ci.yml`, ...)

### 7. Areas of interest
- Files >500 LOC (likely god objects or generated code)
- TODO/FIXME/XXX/HACK density
- `# type: ignore`, `@ts-ignore`, `eslint-disable` density
- Most-imported internal modules (the "hot" code)
- Recently modified files (where current work is happening) — `git log` if available

### 8. Risks / smells
- Missing `encoding="utf-8"` on file I/O (Python on Windows)
- Bare `except:` clauses
- `print` debugging left in non-CLI code
- Empty `__init__.py` where exports are expected
- Missing `py.typed` marker if package ships type hints

## Output format

Produce a single Markdown brief, ~80–150 lines. Use this skeleton:

```markdown
# <Project Name> — Codebase Brief

**Purpose:** <one sentence>
**Stack:** <language version> + <main framework(s)>
**Size:** <N> files, <M> LOC

## Layout
<annotated tree, depth ≤ 3>

## Entry points
- `<path>` — <what it does>

## Configuration
- Env vars: <list>
- Config files: <list>

## Dependencies
**Production:** <count>, notable: <list>
**Dev:** <count>, notable: <list>
**Lockfile:** <present | missing>

## Tests
<framework>, <count> test files, <ratio> vs source

## Areas of interest
- <file>: <reason>

## Risks
- <smell>: <count> occurrences (e.g., `bare except: 3 in agent/loop.py`)

## Suggested next reads
1. `<file>` — <why>
2. `<file>` — <why>
3. `<file>` — <why>
```

The "Suggested next reads" section is the payoff: 3 files that, if read, give the best ROI for understanding the project.

## Examples

### Example: small Python CLI repo

**Input:** repo with `pyproject.toml`, `main.py`, `agent/` package, `tools/` package, no tests.

**Output excerpt:**
```markdown
# mini-agent — Codebase Brief

**Purpose:** Minimal coding agent with tool-use loop.
**Stack:** Python 3.12 + Anthropic SDK
**Size:** 14 files, ~800 LOC

## Entry points
- `main.py` — CLI entry, calls `agent.agent.run()`
- `agent/loop.py` — core tool-use loop

## Risks
- No tests directory — 0 test files for 14 source files
- `agent/loop.py`: 1 bare `except:` at line 47

## Suggested next reads
1. `agent/loop.py` — the loop is the heart of the project
2. `agent/tool_handler.py` — how tools are dispatched
3. `pyproject.toml` — declared deps + entry point wiring
```

## Edge cases

- **Monorepo** — run recon per top-level package; produce one brief each, with a parent overview.
- **Generated code** — flag and exclude from LOC totals (e.g., `*_pb2.py`, `dist/`, `build/`).
- **No `pyproject.toml` / `package.json`** — fall back to file-extension census + README parsing.
- **Polyglot repos** — report per-language stats; pick the dominant language for the deep dive.
- **Very large repos (>10k files)** — `recon.py` caps depth and per-directory file count; note truncation in the brief.
- **No README** — say so explicitly; don't fabricate purpose.

## What NOT to do

- Don't read every file. Read what the brief points to.
- Don't run the recon if you've already done it this session — re-use the prior brief.
- Don't claim to "understand" the codebase from the brief alone. The brief is a map, not the territory.
- Don't fabricate metrics. If the script didn't compute it, omit it.

## References

- `scripts/recon.py` — main recon script; produces the structured brief
- `scripts/find_entrypoints.py` — heuristics for locating CLI/web/library entry points
- `references/checklist.md` — long-form version of the recon checklist with examples
- `references/smells.md` — catalogue of code smells the script looks for, with rationale
