# Agent workspace

An `AgentWorkspace` is one agent's persistent home: a code sandbox, a skill library and, optionally, a long-term memory. Configure it once, then attach it to an agent before its first run.

```python
from agent_harness import Agent, AgentWorkspace, SandboxConfig

workspace = AgentWorkspace(
    'analyst',                                         # a name, not a path
    curated_skills='examples/skills',                  # human-reviewed skills (optional)
    sandbox=SandboxConfig(                             # ExecuteCode options, the same object execute_code_tool takes
        env={'FMP_API_KEY': os.environ['FMP_API_KEY']},   # secrets the agent's code can read
        packages=['pandas'],                               # extra sandbox libraries
    ),
    memory=True,                                       # long-term memory with semantic search (off by default)
    auto_memory_recall=True,                                  # also inject matching memories each request (off by default)
)

agent = Agent(system='<role>You are an analyst.</role>')
workspace.attach(agent)
agent.run('...')
```

## Layout

Workspaces live in the user's home, never inside a repository, and are addressed by name:

```
~/.agent_harness/
├── workspaces/analyst/          ◄─ AgentWorkspace('analyst')
│   └── sandbox/                 ExecuteCode's working directory
│       └── skills/
│           ├── curated/<name>/SKILL.md     re-copied from curated_skills on every attach
│           └── learned/<name>/SKILL.md     the agent's own
└── memory/analyst.sqlite        long-term memory (memory=True only), reached only through the memory tools
```

The memory store sits in its own branch, not beside `sandbox/`, so code browsing the folders around its working directory never comes across it.

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
| `ExecuteCode` | `agent.add_tool` | Sandbox in `sandbox/`, configured by the workspace's `sandbox=SandboxConfig(...)` |
| `<skills_protocol>` | `agent.extend_system_prompt` | The SKILL.md format and rules (cached system prompt) |
| `<skills>` index | `agent.extend_system_prompt` | Each skill's name and description, a snapshot taken at attach (cached system prompt) |
| `Remember` / `Recall` / `Forget` | `agent.add_tool` | The agent's only access to its memory store (`memory=True` only) |
| `<memory_context_protocol>` + `<memories>` provider | `agent.extend_system_prompt` + `agent.add_dynamic_provider` | Matching memories in the dynamic context (`auto_memory_recall=True` only) |
| `<memory_protocol>` | `agent.extend_system_prompt` | What to save (sparingly), memory vs skills, Recall at the start of related work, never touch the store through code |

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

## Memory

Opt-in with `AgentWorkspace(..., memory=True)`. Without it the workspace adds no memory tools or protocol and creates no store.

Memory holds facts, lessons, preferences and events (what is true or what happened); skills hold procedures. Each memory is one or two sentences with a `kind` (`fact`, `lesson`, `preference`, `event`) and an `importance` (`critical`, `high`, `medium`, `low`).

```
Remember(content, kind, importance) ──► memories (record) + memories_fts (keywords) + memory_vectors (embedding)
Recall(query)  ──► keyword candidates ∪ semantic candidates ──► score = relevance + 0.5·recency + 0.5·importance
Forget(id)     ──► delete from all three tables
```

| Table | Holds | Role |
|---|---|---|
| `memories` | id, content, kind, importance, created_at, last_used_at, use_count | Source of truth |
| `memories_fts` | Full-text index of content (FTS5) | Keyword search |
| `memory_vectors` | (memory_id, model) → unit float32 vector | Semantic search |

| Signal | Computed as | Range |
|---|---|---|
| relevance | `0.7·semantic + 0.3·keyword` (NOOA's weights) | 0–1 |
| ↳ keyword | BM25 match as a fraction of the best match | 0–1 |
| ↳ semantic | Cosine similarity; below the model's floor (0.27 for voyage-4) a memory is not a candidate | 0–1 |
| recency | sigmoid of `ln(1 + uses) − 0.5·ln(1 + hours since last use)` (ACT-R style) | 0–1 |
| importance | level ÷ 4 | 0.25–1 |

- Nothing is min-max scaled across candidates: that turns a near-tie in relevance into a full 1-vs-0 gap importance can never overcome.
- Memory reaches the model through `Recall`, and with `auto_memory_recall=True` also through the `<memories>` block (below). The protocol tells it to Recall at the start of work that may relate to the past.
- Identical content is stored once; `Remember` returns the existing id.
- The protocol tells the agent most tasks should save nothing or one or two memories.
- The store's path never appears in any prompt or tool output. With `SandboxConfig(j_screen=True)`, the `memory_store_access` rule blocks code that reads, copies, edits, deletes or searches for it (see `docs/tools/code_execution.md`). This is a guard against accidents, not a security boundary.

### Semantic search

Built into `memory=True`: the default embedder is `ApiEmbedder()`, OpenRouter's `voyageai/voyage-4`, chosen in `reports/Embedding models for agent memory.md` for speed and finance-retrieval evidence. Pass `embedder=` only to override it (another model, or an offline fake in tests); any object with `model`, `min_similarity` and `embed(texts)` works. There is no keyword-only mode, but if the embedding API is down, search falls back to keywords on its own. Vectors are derived data:

- **Save never fails because of embeddings.** The record is committed first; if the embedding call fails, the memory is still keyword-searchable and its vector is backfilled the next time a store opens with an embedder.
- **Switching models** re-embeds into new `memory_vectors` rows keyed by model; records are untouched and vectors from different models are never compared.
- **Cost** (measured, voyage-4 via OpenRouter): ~0.13–0.15 s per `Remember` or `Recall` (occasional ~1 s outlier); scoring 1,000 vectors locally takes ~0.05 s. Turns that don't touch memory pay nothing. Price $0.06 per 1M tokens.
- **The similarity floor is per model** (`KNOWN_FLOORS` in `embeddings.py`), because models spread cosine scores differently: off-topic queries scored up to 0.08 on `text-embedding-3-small` but up to 0.23 on `voyage-4`, whose real matches start at 0.30. voyage-4 uses 0.27, 3-small 0.2. `ApiEmbedder` refuses an unmeasured model unless you pass `min_similarity=`.

Live comparison on the same five memories:

| Query | Keyword only | Hybrid |
|---|---|---|
| MSFT leasing liabilities | the MSFT DCF event (shares "MSFT") | the Microsoft lease-debt lesson |
| how should I format my answer | nothing | "user wants valuations as a table" |
| API throttling | nothing | "FMP returns HTTP 429 above ~300 req/min" |
| NVDA investment spending | nothing | "Nvidia's FY2026 capex tripled" |

### Automatic recall

Opt-in with `auto_memory_recall=True` (needs `memory=True`). `DynamicMemoryContext` (`memory/dynamic_context.py`) is a dynamic context provider: before every model call it puts the memories matching the latest user message into a `<memories>` block in the trailing `<dynamic_context>` message, after the cached prefix.

```
latest user message ──► search(touch=False) ──► keep semantic matches (top 3) ──► <memories>
```

| Rule | Why |
|---|---|
| Searches only when the latest user message changes | It runs every iteration; a long tool loop costs one embedding call |
| `touch=False` | An injection is not a deliberate use; counting it would keep boosting whatever was injected |
| Injects only semantic matches (above the embedder's floor), not by `score` | Score adds recency and importance, so an off-topic memory still scores ~0.5; one shared keyword is not enough |
| No cutoff of its own | The floor is measured per model (0.27 for voyage-4); a fixed relevance cutoff tuned on one embedder silently blocked every real voyage-4 match (cosine 0.30–0.40) |

`Recall` stays the deep search: the `<memory_context_protocol>` tells the agent the block is a starting point.

Not in v1: links between memories, background reflection (merge, decay, prune).

## Dependency direction

`workspace/` imports from the engine (`execute_code_tool`, `Agent`'s public methods); nothing in the engine imports `workspace/`. Deleting the folder leaves the engine working.


Source: `packages/agent_harness/src/agent_harness/workspace/`. Contracts: `tests/test_workspace.py`, `tests/test_memory.py`.
