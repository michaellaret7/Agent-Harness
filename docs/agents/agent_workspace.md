# Agent workspace

An `AgentWorkspace` is one agent's persistent home: a code sandbox and a skill library in one folder. Configure it once, then attach it to an agent before its first run.

```python
from agent_harness import Agent, AgentWorkspace

workspace = AgentWorkspace(
    'analyst',                                         # a name, not a path
    curated_skills='examples/skills',                  # human-reviewed skills (optional)
    env={'FMP_API_KEY': os.environ['FMP_API_KEY']},   # secrets the agent's code can read
    packages=['pandas'],                               # extra sandbox libraries
)

agent = Agent(system='<role>You are an analyst.</role>')
workspace.attach(agent)
agent.run('...')
```

## Layout

Workspaces live in the user's home, never inside a repository, and are addressed by name:

```
~/.agent_harness/workspaces/analyst/      ◄─ AgentWorkspace('analyst')
└── sandbox/                 ExecuteCode's working directory
    └── skills/
        ├── curated/<name>/SKILL.md     re-copied from curated_skills on every attach
        └── learned/<name>/SKILL.md     the agent's own
```

- The name must be a lowercase slug, so it cannot point anywhere else.
- `AGENT_HARNESS_HOME` replaces `~/.agent_harness` (tests use a temp dir; a server can use a data disk).
- Not under `~/.cache`: a cache may be wiped, but workspaces hold skills and memory that must survive.

| Survives a new run or process | Yes / no |
|---|---|
| Files in `sandbox/`, including skills | Yes: a caller-supplied workspace is never deleted |
| Python variables in the kernel | No: the kernel restarts; code reloads from files |

## What `attach` adds

| Piece | Through | Effect |
|---|---|---|
| `ExecuteCode` | `agent.add_tool` | Sandbox in `sandbox/`, with the workspace's `env`, `packages`, `j_screen` |
| `<skills_protocol>` | `agent.extend_system_prompt` | The SKILL.md format and rules (cached system prompt) |
| `<skills>` index | `agent.add_dynamic_provider` | Each skill's name and description, refreshed every turn |

`attach` raises if the agent already has `ExecuteCode` (it would keep that sandbox and ignore the workspace's) or has already run (the system prompt is fixed by then).

## Skills

Skills are procedures (how to do something); memory, when added, holds facts and past events. Skills come in two tiers:

| Tier | Folder | Who writes it | Lifetime |
|---|---|---|---|
| Curated | `skills/curated/` | A human, version-controlled at the `curated_skills` source | Wiped and re-copied on every `attach`, so agent edits never persist |
| Learned | `skills/learned/` | The agent, through ExecuteCode | Persists in the workspace |

```
repo skills/  ── attach syncs ──►  curated/   (agent reads, follows, never edits)
                                   learned/   (agent creates and improves)
                                       │
              human reviews a learned skill ──► good? move it into the repo ──► curated
```

Learning stays open-ended, but the core methods cannot be corrupted: a mistake an agent writes into a skill stays in `learned/`, where a human sees it before it can become curated. When a curated skill seems wrong, the protocol tells the agent to write a learned `<name>-notes` skill instead of editing it, and on conflict the curated skill wins.

Every `SKILL.md` must start with:

```markdown
---
name: <lowercase-slug, identical to the folder name>
description: <one line: what it does and when to use it>
---
```

The index enforces this softly: a learned skill that breaks it is flagged `INVALID (...), rewrite it`; a curated one is flagged for a human instead. `examples/skills/dcf-valuation/` is the first curated skill.

## Dependency direction

`workspace/` imports from the engine (`execute_code_tool`, `Agent`'s public methods); nothing in the engine imports `workspace/`. Deleting the folder leaves the engine working.

## Next

Long-term memory (`memory.sqlite` next to `sandbox/`, with Remember / Recall tools and an auto-recall block) attaches the same way. Design notes: NOOA paper Sec 3.7 and Appendix C.

Source: `packages/agent_harness/src/agent_harness/workspace/`. Contracts: `tests/test_workspace.py`.
