# Agent Skills Format Specification

Canonical format reference. SKILL.md covers the workflow; this file is the spec.

## Table of Contents
1. [SKILL.md File Format](#skillmd-file-format)
2. [YAML Frontmatter Fields](#yaml-frontmatter-fields)
3. [Optional Directories](#optional-directories)
4. [Validation Rules](#validation-rules)

---

## SKILL.md File Format

```markdown
---
name: skill-name
description: What the skill does and when to use it.
license: Apache-2.0
metadata:
  author: author-name
  version: "1.0"
  tags: ["tag1", "tag2"]
---

# Skill Title

Markdown content.
```

### Critical formatting rules
1. YAML frontmatter MUST start on line 1 (no blank lines before `---`)
2. Frontmatter MUST end with a closing `---` before the Markdown body
3. Use spaces (not tabs) for YAML indentation
4. Include a blank line after the closing `---`

### Directory naming
- Lowercase letters, numbers, hyphens only
- Max 64 characters
- Must match the `name` field in frontmatter

---

## YAML Frontmatter Fields

### Required: `name` (string)

- Max 64 characters
- Lowercase letters, numbers, hyphens only
- Must not start or end with a hyphen
- No consecutive hyphens (`--`)
- No XML tags
- Must not be a reserved word (`skill`, `skills`, `claude`, `anthropic`, `system`)

```yaml
name: pdf-processing          # ✅
name: code-review             # ✅
name: My Cool Skill           # ❌ uppercase + spaces
name: skill_with_underscores  # ❌ underscores
name: -leading-hyphen         # ❌ leading hyphen
```

### Required: `description` (string)

- Max 1024 characters
- Non-empty
- No XML tags

Authoring guidance lives in SKILL.md (good/bad examples). The validator additionally flags vague words: `helps`, `various`, `stuff`, `things`.

### Optional: `license` (string)

```yaml
license: Apache-2.0
license: MIT
license: ./LICENSE.txt
```

### Optional: `metadata` (object)

Free-form, used for organization/discovery. Common fields:

```yaml
metadata:
  author: anthropic
  version: "1.0.0"
  tags: ["development", "testing"]
  category: "development"
  created: "2025-01-15"      # ISO 8601
  updated: "2025-03-20"
  requires:
    - python >= 3.8
    - pandas
```

---

## Optional Directories

### `scripts/`

Executable code agents can run.

- Self-contained, or document dependencies in a docstring
- Include usage examples in docstrings/comments
- Handle edge cases gracefully
- Test before bundling

Common languages: Python (`.py`), Bash (`.sh`), Node (`.js`). Actual support depends on the runtime.

### `references/`

Documentation loaded on demand.

- One topic per file
- Include a TOC for files >100 lines
- Max one nesting level below SKILL.md
- Descriptive filenames (`api.md`, `schema.md`, `troubleshooting.md`, not `doc1.md`)

### `assets/`

Static files used in output: templates, images, fonts, config defaults, sample data. Prefer text formats; keep binary minimal.

---

## Validation Rules

| Field | Rule |
|-------|------|
| `name` | Required, ≤64 chars, lowercase alphanumeric + hyphens, no leading/trailing/consecutive hyphens |
| `description` | Required, ≤1024 chars, non-empty, no XML tags |
| `license` | Optional, string |
| `metadata` | Optional, object |

### Structural checks
- `SKILL.md` must exist (case-sensitive)
- YAML must parse without errors
- All files referenced from SKILL.md should exist on disk
- Python scripts should compile without syntax errors

### Content checks
- No hardcoded credentials, API keys, or secrets
- No unexpected network calls
- Dependencies documented

Run `scripts/validate_skill.py <dir> --verbose --strict` to enforce all of the above.

---

## Token Budgets

| Level | Content | Loaded | Recommended |
|-------|---------|--------|-------------|
| 1 | Frontmatter | Always | ~100 tokens |
| 2 | SKILL.md body | On trigger | <5,000 tokens (<500 lines) |
| 3 | Reference files | On demand | <10,000 tokens per file |

Rough estimate: ~4 chars per token. For exact counts, use the Anthropic tokenizer or `tiktoken`.
