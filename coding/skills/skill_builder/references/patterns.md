# Skill Patterns

Proven patterns for different skill types. Most production skills combine 2–3.

## Table of Contents
1. [Task-Based](#task-based) — single well-defined task
2. [Workflow](#workflow) — multi-step with decision points
3. [Domain Expertise](#domain-expertise) — specialized knowledge
4. [Tool Integration](#tool-integration) — specific tool/format/API
5. [Code Generation](#code-generation) — templated scaffolding
6. [Analysis](#analysis) — input → structured insights

---

## Task-Based

Single purpose, clear trigger, deterministic output. Often script-backed.

### Example: Commit Message Generator

```yaml
---
name: commit-message-generator
description: Generates conventional commit messages from git diffs. Use when writing commit messages or reviewing staged changes.
---
```

Body outline:
1. Run `git diff --staged`
2. Classify change type: `feat | fix | docs | style | refactor | test | chore`
3. Emit `<type>(<scope>): <subject>` (≤50 chars, imperative, no period)
4. Optional body wrapped at 72 chars explaining *what* and *why*

Concrete output:
```
feat(auth): add email validation to signup form

Add regex-based email validation to prevent invalid
emails during user registration.
```

---

## Workflow

Multi-step processes with branches and checkpoints.

### Example: Code Review

```yaml
---
name: code-review-workflow
description: Comprehensive code review covering security, performance, and maintainability. Use when reviewing pull requests or code changes.
---
```

Body organized as phases with checklists:

```markdown
## Phase 1: Overview
Read PR description, understand intent, assess scope.

## Phase 2: Security
- [ ] Input validation on all user data
- [ ] Parameterized queries (no SQL injection)
- [ ] Output encoding (no XSS)
- [ ] Auth/authz on protected paths
- [ ] No exposed secrets or PII

If issues found → flag as blocking.

## Phase 3: Performance
- [ ] No N+1 queries
- [ ] Indexes on queried fields
- [ ] Pagination on list endpoints
- [ ] No O(n²) where O(n) is possible

## Phase 4: Quality
- [ ] Clear naming
- [ ] Single responsibility
- [ ] Adequate tests
- [ ] Comments only where logic is non-obvious

## Phase 5: Summary
Output: status (Approved | Changes Requested | Needs Discussion),
required changes, suggestions, positive feedback.
```

---

## Domain Expertise

Specialized field knowledge or org-specific schemas.

### Example: Financial Analysis

```yaml
---
name: financial-analysis
description: Financial analysis using standard accounting metrics. Use for quarterly reports, budget analysis, revenue projections, or financial modeling.
---
```

Body provides reference formulas Claude shouldn't reinvent:

```markdown
## Profitability
- Gross Margin = (Revenue - COGS) / Revenue
- Operating Margin = Operating Income / Revenue
- ROE = Net Income / Shareholders' Equity
- ROA = Net Income / Total Assets

## Liquidity
- Current Ratio = Current Assets / Current Liabilities
- Quick Ratio = (Current Assets - Inventory) / Current Liabilities

## Growth
- YoY Growth = (Current - Prior) / Prior × 100
- CAGR = (Ending / Beginning)^(1/years) - 1

## Data hygiene
1. Verify date of figures
2. Ensure consistent accounting periods
3. Note restatements / one-time items
4. Adjust non-GAAP items when comparing
```

For deep org-specific schemas, put them in `references/schemas.md`.

---

## Tool Integration

Specific tool, format, or API expertise. Almost always paired with `scripts/`.

### Example: PDF Processing

```yaml
---
name: pdf-processing
description: Extract text and tables from PDFs, fill forms, merge/split documents. Use when working with PDF files or when the user mentions PDFs, forms, or document extraction.
---
```

Body shows the canonical snippet for each operation:

```python
# Text extraction
import pdfplumber
with pdfplumber.open(path) as pdf:
    text = "\n".join(p.extract_text() or "" for p in pdf.pages)

# Table extraction
with pdfplumber.open(path) as pdf:
    tables = [t for p in pdf.pages for t in p.extract_tables()]

# Merge
from pypdf import PdfReader, PdfWriter
writer = PdfWriter()
for src in inputs:
    for page in PdfReader(src).pages:
        writer.add_page(page)
writer.write(open(out, "wb"))
```

Decision tree for routing:
- Has fillable fields? → `references/forms.md`
- Scanned image? → OCR (pytesseract + pdf2image)
- Encrypted? → request password
- Otherwise → direct extraction with pdfplumber

Dependencies stated up front: `pdfplumber`, `pypdf`, optionally `pdf2image` (needs poppler).

---

## Code Generation

Templated scaffolding. Templates live in `assets/`.

### Example: React Component Generator

```yaml
---
name: react-component-generator
description: Generate React components with TypeScript, tests, and Storybook stories. Use when creating new React components or scaffolding frontend code.
---
```

Output structure:
```
ComponentName/
├── ComponentName.tsx
├── ComponentName.test.tsx
├── ComponentName.stories.tsx
├── ComponentName.module.css
└── index.ts
```

Body specifies conventions Claude must follow (functional components, named exports, `{Name}Props` interface, CSS modules) and points to `assets/templates/` for the actual template files. Don't inline templates in SKILL.md — load them from assets.

---

## Analysis

Input → structured report.

### Example: Codebase Analyzer

```yaml
---
name: codebase-analyzer
description: Analyze codebase structure, dependencies, complexity, and technical debt. Use when evaluating code quality, planning refactors, or onboarding to a new project.
---
```

Define the output schema explicitly so Claude produces consistent reports:

```markdown
# Codebase Analysis Report

## Overview
- Language, framework, size (LOC/files), last commit

## Structure
[Annotated directory tree]

## Dependencies
- Production: [name@version, security status]
- Dev: [name@version]
- Issues: [outdated, vulnerable]

## Quality
- Avg complexity, test coverage, doc assessment

## Technical Debt
[Prioritized list with severity]

## Recommendations
1. [P1 action] — [rationale]
2. [P2 action] — [rationale]
```

For automated metrics, defer to `scripts/analyze_codebase.py` rather than computing in-context.

---

## In-Repo: Using This Agent's Tools

The six patterns above describe shapes. This one shows what a skill looks like when it composes the tools *this agent* already has — `Read`, `Edit`, `Bash`, `Glob`, `Grep`, `Plan`, `Skill`, `LoadTool`. Use it as the concrete template for skills that live in `coding/skills/` or `agent/skills/`.

### Example: Module Splitter

```yaml
---
name: module-splitter
description: Split an oversized Python module (>500 lines per project rule) into smaller files grouped by responsibility, preserving public API and updating callers. Use when a file exceeds the size cap or the user asks to "split", "break up", or "modularize" a module.
---
```

Body outline — note how each step names the tool it expects to use:

```markdown
## When to trigger
A single `.py` file >500 lines, or the user mentions splitting / modularizing.

## Procedure

1. **Survey** — `Read` the target file end to end. Identify cohesive groups: pure helpers, IO, types, public API. Use `Grep` to list every external caller of each symbol (`from <module> import <name>`).

2. **Plan** — call the `Plan` tool with the proposed file layout, the import-rewrite list, and the rollback step. Pause for approval before any edits.

3. **Extract** — for each new module:
   - `Write` the new file (helpers at top under the banner block, per the project's whitespace rules).
   - `Edit` the original file to remove the moved code and re-export from the new module so the public API stays stable.

4. **Rewrite callers** — for each external caller found in step 1, `Edit` the import line. Run `Grep` once more to confirm no stale references remain.

5. **Verify** — `Bash` `uv run python -c "import <package>"` to confirm the package still imports. If a circular import surfaces, the split was wrong — revert and regroup.

## Constraints
- Do not introduce a `__init__.py` re-export shim if one already exists; extend it.
- Never rename a public symbol during the split. Renames are a separate change.
- Stop and ask if the file has no obvious responsibility seams — splitting by line count alone produces worse code.
```

What makes this an "in-repo" pattern rather than a generic Workflow:
- **Tool names are explicit** (`Read`, `Edit`, `Grep`, `Plan`, `Bash`) so the agent doesn't have to guess what's available.
- **References project rules** the agent already follows (500-line cap, helper banner block, whitespace conventions in `CLAUDE.md`) instead of restating them.
- **Has a real verification step** that uses this project's actual entry point (`uv run python -c ...`), not a generic "run the tests."
- **Has a stop condition** — skills should know when *not* to fire, not just when to fire.

---

## Combining Patterns

When a skill spans multiple patterns:
1. Identify the **primary** pattern (the main user intent)
2. Add supporting patterns only as needed
3. Keep SKILL.md focused on the primary flow
4. Push secondary-pattern details into reference files

Example: an "incident response" skill is primarily Workflow, with Domain Expertise (runbooks) and Analysis (post-mortems) as supporting references.
