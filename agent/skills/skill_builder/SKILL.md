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

## Skills in this project

This agent has its own minimal skill runtime — separate from Claude Code / Claude.ai. Before authoring, know how it behaves:

- **Location**: skills live in `<domain>/skills/` (e.g. `coding/skills/`, `agent/skills/`). The domain passes `skills_dir=...` to `Agent(...)`.
- **Loader**: `agent/skills.py` scans `<skills_dir>/*/SKILL.md` once at `Agent.__init__`. There is no hot reload — restart the agent after adding or editing a skill.
- **Progressive disclosure**: Level 1 (frontmatter `name` + `description`) is injected into the system prompt. Level 2 (the SKILL.md body) is fetched on demand via the `Skill` tool. Level 3 (`scripts/`, `references/`, `assets/`) is read with `Read` / `Bash` against the skill's directory.
- **Frontmatter parsing is loose**: only top-level scalar keys are read. Nested blocks like `metadata:` are silently skipped, and the strict name/description rules in `references/specification.md` are *not* enforced at runtime. Follow them anyway — they're forward-compat with the Anthropic format.

## What Makes a Skill Useful

A loadable skill is not the same as a useful skill. Before writing, satisfy this bar:

1. **It changes behavior.** Run the target prompt once *without* the skill. If the agent does the right thing already, the skill is dead weight. Skip it or scope it down.
2. **It encodes knowledge Claude doesn't have.** Real values: in-house schemas, project conventions buried in obscure files, multi-step procedures with non-obvious sequencing, hard-won fixes for specific failure modes. Empty calories: "use clear naming," "write tests," "validate inputs," restating framework docs.
3. **It triggers when it should and stays silent when it shouldn't.** The description is the only thing the agent sees in the no-skill state. If a near-miss prompt would also trigger it, the skill will fire on the wrong tasks and degrade behavior elsewhere.
4. **It's narrower than you think.** One skill, one clear job. A skill that "helps with the backend" will compete poorly against three skills that handle migrations, query optimization, and auth respectively.

If you can't name a concrete prompt where (a) the agent fails without the skill and (b) succeeds with it, you don't have a skill yet — you have a wish.

## Skill Layout

```
skill-name/
├── SKILL.md         # Required — entry point
├── scripts/         # Executable code (run, don't load)
├── references/      # Docs loaded on demand
└── assets/          # Templates, images, static files
```

**When to use which directory:**
- `scripts/` — operations needing deterministic behavior, repeated logic, external tools
- `references/` — domain knowledge too large for SKILL.md
- `assets/` — templates, binary files, static resources used in output

## Authoring Workflow

The two bundled scripts are the spine of this workflow — reach for them first, not last:
- `scripts/init_skill.py` creates the directory layout so you don't hand-craft it.
- `scripts/validate_skill.py` catches every mechanical failure (bad YAML, missing files, broken Python) so the only thing left to evaluate is *quality*.

### 1. Scaffold

```bash
python scripts/init_skill.py <skill-name> --path ./skills --resources scripts,references,assets
```

### 2. Write the frontmatter

```yaml
---
name: my-skill-name
description: Specific action verbs + file types/domains + trigger phrases. Use when [concrete situation].
---
```

Only `name` and `description` are read by this agent's loader. `metadata:` (and any other nested blocks) are valid but ignored at runtime — include them only if you also intend the skill to ship under the Anthropic format.

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

**❌ Useless body** (the agent already knows this):
```markdown
## Instructions
- Write clean, maintainable code.
- Follow SOLID principles.
- Use meaningful variable names.
- Validate user input.
- Handle errors gracefully.
- Write tests for your code.
```

**✅ Useful body** (project-specific procedure the agent cannot guess):
```markdown
## Instructions

1. Run `Bash` `uv run python -m coding --dry-run` to confirm the env is sane before any edit.
2. Tools live in `<domain>/tools/`. Register via `agent.add_tool(module.tool)` in the domain's `__main__.py` — `add_tool` is idempotent by name, re-registration is a silent no-op.
3. The `tool` dict needs exactly four keys: `name`, `description`, `parameters` (JSON Schema), `function`. Decorated functions carry the dict on `.tool` — pass the function itself.
4. Never set `shell=True` in `Bash`-style tools. On Windows it dispatches to `cmd.exe`, which doesn't understand POSIX commands the model emits. See `coding/tools/bash.py` for the binary-resolution pattern.

## Edge cases
- If the new tool name collides with an existing one, registration silently no-ops — your tool won't be active. Rename, don't `del agent.tools[...]`.
```

The first body could be deleted without losing anything. The second teaches the agent things it would otherwise have to reverse-engineer from the codebase.

### Specificity: prescribe vs. direct

The dial between "exact sequence" and "high-level direction" is the hardest call in skill authoring. Wrong setting and the skill either over-constrains (agent follows steps that don't fit the situation) or under-constrains (agent ignores the skill and falls back on defaults).

Heuristics:
- **Prescribe** when the cost of a wrong choice is high (migrations, security-sensitive code, anything touching prod), when there's one correct sequence (build → migrate → deploy), or when the agent reliably gets a specific step wrong.
- **Direct** when the task is open-ended (code review, design, refactoring), when multiple valid approaches exist, or when over-specification would freeze out judgment the agent already has.
- **Yellow flag**: if you find yourself writing `ALWAYS`, `NEVER`, or numbered steps for what's really a judgment call, you're over-prescribing. Reframe as "consider X because Y" and trust the agent to apply it.
- **Red flag**: if removing a step doesn't change behavior on your test prompts, the step wasn't pulling weight. Cut it.

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

### 6. Install

Drop the skill directory into the target domain's `skills/` folder (e.g. `coding/skills/<skill-name>/`) and restart the agent. The next `Agent.__init__` will pick it up.

### 7. Verify

A well-formed skill is not the same as a working skill. Confirm each level — failure at any level sends you back, not forward:

1. **Loaded** — start the agent and check the system prompt's `<skills>` block lists your skill by name with the right description. If missing, `load_skills` rejected it (bad frontmatter, wrong path, or SKILL.md not at the directory root).
2. **Triggered** — give the agent a prompt the description should match. Did it call the `Skill` tool with your skill's name? If not, the description isn't pulling — sharpen the trigger phrases.
3. **Useful** — run the skill on a real task and compare against the same prompt *without* the skill (or with the body emptied). If behavior is unchanged, the body isn't earning its tokens. Either the agent already knew this, or the body's advice isn't reaching the right decision point. Proceed to **Iterate**.

### 8. Iterate

This is where most of the quality comes from. A first-draft skill almost never works well — the difference between a useful skill and a useless one is iteration count, not authorial talent.

**The loop**:
1. **Pick 2–3 representative prompts.** Real ones the agent will actually see, not toy examples. Save them somewhere — you'll rerun them after every revision.
2. **Run each prompt twice**: once with the skill loaded, once without. Diff the two transcripts. The skill is working if the with-skill run reaches a better answer, takes a more direct path, or avoids a failure the no-skill run hits.
3. **Read the transcript, not just the output.** If the agent fetched the skill body and then ignored half of it, the ignored half is dead text — cut it. If the agent reached for the skill but did the wrong thing, the body misled it — figure out which sentence and rewrite.
4. **Change one thing per iteration.** Multiple simultaneous edits confound the signal; you won't know which change helped.
5. **Restart the agent** between iterations — skills are scanned once at `Agent.__init__`, edits to a loaded skill have no effect until restart.

**What to look at, in order**:
1. **Did the skill trigger?** No → fix the description. The body is irrelevant until triggering works.
2. **Did the agent reach for the right reference / script?** No → the SKILL.md isn't surfacing the resource clearly. Add an explicit "Use `references/X.md` when …" pointer.
3. **Did the agent follow the right step?** No → either the step is buried, the wording is hedged ("consider", "you might want to"), or the step contradicts something earlier in the body.
4. **Did the agent ignore a step it shouldn't have?** Either the step is wrong (cut it), or it's right but unmotivated. Add a one-sentence *why* — agents follow instructions they understand better than instructions they don't.
5. **Did the agent do something the skill doesn't cover?** New edge case → either add coverage or scope the skill's description tighter so it doesn't trigger there.

**When to stop**:
- The skill passes all 2–3 test prompts and removing any section makes at least one prompt worse.
- You've stopped finding things to change after a full pass through your test prompts.
- Further changes are tradeoffs (improve prompt A, regress prompt B) rather than wins. That's the ceiling for this skill at this size.

**Signs you're iterating wrong**:
- Body keeps growing → you're overfitting to specific examples. Generalize or split into a second skill.
- Adding ALL-CAPS rules to fight a recurring failure → the agent didn't understand *why*; explain instead of shouting.
- Same prompt behaves differently across runs → the skill isn't deterministic enough; the body is leaving too much to interpretation, or the trigger is borderline.

## Common Mistakes

1. **Vague description** — skill never triggers
2. **Restating things Claude knows** — wastes context, degrades signal-to-noise
3. **Monolithic SKILL.md** — split into references when >500 lines
4. **Untested scripts** — always run before bundling
5. **Unreferenced bundled files** — Claude won't know they exist
6. **Wrong specificity** — see "Specificity: prescribe vs. direct" above
7. **Shipping the first draft** — a skill that hasn't been iterated against real prompts is a draft, not a skill

## Bundled Resources

- `references/specification.md` — complete SKILL.md format spec (canonical reference)
- `references/patterns.md` — proven skill patterns with full examples, including an in-repo example that composes this agent's actual tools (`Read`, `Edit`, `Bash`, `Plan`, `Grep`)
- `scripts/init_skill.py` — scaffold a new skill
- `scripts/validate_skill.py` — validate format and structure
- `assets/template-skill/` — minimal skill to copy
