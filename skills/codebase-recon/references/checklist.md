# Recon Checklist — Long Form

The condensed version lives in `SKILL.md`. This file is the deep reference: each checklist item with rationale, where to look, and worked examples.

## Table of Contents
1. [Project identity](#1-project-identity)
2. [Layout](#2-layout)
3. [Entry points](#3-entry-points)
4. [Configuration surface](#4-configuration-surface)
5. [Dependencies](#5-dependencies)
6. [Tests](#6-tests)
7. [Areas of interest](#7-areas-of-interest)
8. [Risks and smells](#8-risks-and-smells)

---

## 1. Project identity

**Why it matters:** Without a one-sentence purpose statement, every later observation is unanchored.

**Where to look (in order):**
1. `README.md` — first paragraph, or first non-badge content
2. `pyproject.toml` `[project] description`
3. `package.json` `description`
4. `Cargo.toml` `[package] description`
5. Repository name as last resort

**Language and version:**
- Python: `.python-version` > `pyproject.toml` `requires-python` > shebangs
- Node: `package.json` `engines.node` > `.nvmrc`
- Go: `go.mod` `go <version>`
- Rust: `Cargo.toml` `rust-version`

**Framework inference (Python):**
| Dep present | Likely framework |
|-------------|------------------|
| `fastapi` | FastAPI web service |
| `flask` | Flask web app |
| `django` | Django app |
| `click`, `typer` | CLI |
| `streamlit`, `gradio` | Data app |
| `pytorch`, `tensorflow` | ML |
| `pandas` + `jupyter` | Data analysis |
| `anthropic`, `openai` | LLM client |

---

## 2. Layout

**Depth cap:** 3 levels. Anything deeper is noise for the brief.

**Annotations:** Each top-level directory should have a one-line description. If you can't tell what a directory is for from its name + contents, that's itself a finding worth flagging.

**Common patterns to recognize:**
- `src/<package>/` — src-layout Python package
- `<package>/` at root — flat-layout Python package
- `app/`, `lib/`, `pkg/` — generic source roots
- `cmd/<tool>/main.go` — Go CLI convention
- `internal/` (Go) — package-private code

**Unusual structure to flag:**
- Code files at repo root (suggests script collection, not a package)
- Two unrelated package roots (possible monorepo, or accidental)
- Mixed languages without clear separation
- `vendor/`, `third_party/`, `external/` — bundled deps to ignore

---

## 3. Entry points

**Why it matters:** The entry points are where execution starts. Understanding them gives you the call graph's roots.

**Python entry point sources:**
1. `pyproject.toml` `[project.scripts]` — declared CLI commands
2. `setup.py` `entry_points` — legacy
3. Files containing `if __name__ == "__main__":`
4. `__main__.py` files (run via `python -m <package>`)
5. Web framework instantiation: `FastAPI()`, `Flask(__name__)`, `app = Starlette(...)`

**Node entry points:**
1. `package.json` `bin` — CLI commands
2. `package.json` `main`, `module`, `exports` — library entry
3. `package.json` `scripts.start` — dev entry

**Library public surface (Python):**
- `__init__.py` with `__all__` — explicit exports (good)
- `__init__.py` with `from .x import *` — implicit, harder to audit
- Empty `__init__.py` — package is namespace-only or exports are accessed via submodule

---

## 4. Configuration surface

**Why it matters:** Config is where production breaks. Knowing the full surface prevents "works on my machine."

**Env vars — search patterns:**
- Python: `os.environ`, `os.getenv`, `os.environ.get`
- Node: `process.env.`
- Go: `os.Getenv`, `os.LookupEnv`
- Generic: `getenv` calls

**Config files to look for:**
- `.env`, `.env.example`, `.env.template`, `.env.local`
- `config.toml`, `config.yaml`, `config.json`, `config.ini`
- `settings.py` (Django convention)
- `pyproject.toml` `[tool.<name>]` sections
- `.<tool>rc` files (`.eslintrc`, `.prettierrc`, ...)

**Output format:** for each env var, capture name + whether it has a default + where it's read. Missing defaults on required vars are a deployment risk.

---

## 5. Dependencies

**Direct vs transitive:** Only direct deps belong in the brief. Transitive deps are lockfile concern.

**Sources by ecosystem:**
- Python: `pyproject.toml` `[project] dependencies` and `[project.optional-dependencies]`, or `requirements*.txt`
- Node: `package.json` `dependencies`, `devDependencies`, `peerDependencies`
- Go: `go.mod` `require` blocks
- Rust: `Cargo.toml` `[dependencies]`, `[dev-dependencies]`

**What to flag:**
- No lockfile present (reproducibility risk)
- Pinned to known-vulnerable versions (look up only if asked — don't claim CVE knowledge from training data)
- Deps marked deprecated in their own docs (e.g., `python-dotenv` is fine; `urllib3<2` may need attention)
- Dev deps in production list, or vice versa

**"Notable" deps:** the ones that shape the architecture. `fastapi`, `sqlalchemy`, `pydantic`, `react`, `express`. Skip noise like `pytest`, `black`, `prettier` for production listings.

---

## 6. Tests

**Detection:**
- Directory: `tests/`, `test/`, `__tests__/`, `spec/`
- Files: `test_*.py`, `*_test.py`, `*.test.ts`, `*.spec.ts`
- Pytest config in `pyproject.toml` `[tool.pytest.ini_options]`

**Ratios — rough rule of thumb:**
- 0 tests → 🚨 immediate flag
- < 0.2 test files per source file → low coverage signal
- 0.2 – 0.5 → typical
- > 0.5 → well-tested

**CI:**
- `.github/workflows/*.yml` — GitHub Actions
- `.gitlab-ci.yml` — GitLab CI
- `.circleci/config.yml` — CircleCI
- `Jenkinsfile` — Jenkins

**Don't claim coverage you didn't measure.** If there's no `coverage.xml` or `.coverage` file, don't estimate a percentage.

---

## 7. Areas of interest

The goal of this section is to point at the 3-5 files that matter most for whatever the user is about to do.

**Heuristics for "interesting":**
1. **Large files (>500 LOC)** — usually either god objects, generated code, or the actual core
2. **High fan-in** — files imported by many others; these are the load-bearing modules
3. **High TODO density** — TODO/FIXME/XXX/HACK comments concentrated in one file
4. **Recent activity** — `git log --since="1 month ago" --name-only | sort | uniq -c | sort -rn` (if git available)
5. **Type-ignore density** — `# type: ignore`, `@ts-ignore` clusters indicate fragile boundaries

**Filter out:**
- Generated code: `*_pb2.py`, `*.pb.go`, `dist/`, `build/`, `.next/`
- Vendored deps: `vendor/`, `node_modules/`, `.venv/`
- Migrations: `migrations/*.py` (Django), `*.sql` migration files

---

## 8. Risks and smells

See `smells.md` for the full catalogue. The brief should list each smell with a count and at least one example location.

**Don't moralize.** Report findings; don't lecture about them. The user knows `bare except:` is bad.

**Don't double-count.** If a file has 5 smells, list the file once with all 5; don't repeat the file 5 times.
