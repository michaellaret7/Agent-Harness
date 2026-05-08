---
name: skill-builder
description: Create, structure, validate, and package Agent Skills. Use when building a new skill, refining an existing skill, or when the user mentions SKILL.md, skill authoring, or Agent Skills.
license: Apache-2.0
metadata:
  author: anthropic-community
  version: "3.0"
  tags: ["meta", "development"]
---

# Skill Builder

Build Agent Skills: filesystem-based bundles that give Claude domain-specific expertise via progressive disclosure.

## Mental Model

Three load levels:

| Level | Content | When loaded | Budget |
|-------|---------|-------------|--------|
| 1 | YAML frontmatter (name + description) | Always | ~100 tokens |
| 2 | SKILL.md body | When triggered | <5k tokens (<500 lines) |
| 3 | `scripts/`, `references/`, `assets/` | On demand | Unlimited per file |

The frontmatter `description` is the most important field — it determines *whether the skill triggers at all*.

## Skill Layout

```
skill-name/
├── SKILL.md         # Required — entry point
├── LICENSE          # Recommended
├── scripts/         # Executable code (run, don't load)
├── references/      # Docs loaded on demand
└── assets/          # Templates, images, static files
```

**When to use which directory:**
- `scripts/` — operations needing deterministic behavior, repeated logic, external tools
- `references/` — domain knowledge too large for SKILL.md
- `assets/` — templates, binary files, static resources used in output

## Authoring Workflow

### 1. Scaffold

```bash
python scripts/init_skill.py <skill-name> --path ./skills --resources scripts,references,assets
```

### 2. Write the frontmatter

```yaml
---
name: my-skill-name
description: Specific action verbs + file types/domains + trigger phrases. Use when [concrete situation].
license: Apache-2.0
metadata:
  author: your-name
  version: "1.0"
---
```

The description is the entire trigger signal. Be specific.

**❌ Bad → ✅ Good:**
- "Helps with code" → "Reviews Python and JavaScript code for security vulnerabilities, PEP 8 compliance, and performance issues. Use when asked to review code."
- "PDF stuff" → "Extract text and tables from PDFs, fill forms, merge documents. Use when working with PDF files."
- "Database queries and data stuff" → "Query and analyze BigQuery datasets using company schemas. Use for data analysis or SQL generation."

Rules: max 1024 chars, no XML tags, include action verbs + file types/domains + when-to-use clause.

### 3. Write the body

Keep under 500 lines. Use imperative form ("Extract X using Y"), not second person ("You should...").

Recommended sections:
```markdown
## Overview         (2–3 sentences)
## Quick Start      (minimal working example)
## Instructions     (organized by task type)
## Examples         (input → output)
## Edge Cases       (unusual situations)
## References       (point to bundled files)
```

**Include only what Claude doesn't already know.** Skip generic programming advice, obvious framework explanations, security platitudes.

### 4. Add bundled resources

Reference every bundled file explicitly in SKILL.md so Claude knows it exists:

```markdown
## References
- `references/api.md` — full API reference
- `scripts/validate.py` — input validation
- `assets/template.json` — output template
```

If SKILL.md exceeds 500 lines, move topical content into `references/<topic>.md` and leave a pointer.

### 5. Validate

```bash
python scripts/validate_skill.py <skill-directory> --verbose
```

Validates frontmatter, name format, description quality, line count, referenced-file existence, and Python script syntax.

### 6. Package

```bash
cd <skill-directory> && zip -r ../skill-name.zip .
```

Distribute via Claude.ai (Settings > Features), the API `skills` parameter, or `~/.claude/skills/` for Claude Code.

## Common Mistakes

1. **Vague description** — skill never triggers
2. **Restating things Claude knows** — wastes context
3. **Monolithic SKILL.md** — split into references when >500 lines
4. **Untested scripts** — always run before bundling
5. **Unreferenced bundled files** — Claude won't know they exist
6. **Wrong specificity** — high-stakes tasks (migrations) need exact sequences; creative tasks need direction, not steps

## Iterating

Skills improve through observation:
1. Use the skill on real tasks
2. Note where Claude struggles or goes off-track
3. Refine the section that caused the issue
4. Re-test

Useful prompts:
- "When you used this skill, what was missing or unclear?"
- "What edge cases weren't covered?"

## Bundled Resources

- `references/specification.md` — complete SKILL.md format spec (canonical reference)
- `references/patterns.md` — proven skill patterns with full examples (task-based, workflow, domain, tool integration, codegen, analysis)
- `scripts/init_skill.py` — scaffold a new skill
- `scripts/validate_skill.py` — validate format and structure
- `assets/template-skill/` — minimal skill to copy
