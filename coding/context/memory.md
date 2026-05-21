# Memory

- The user loves Python.

- **Code beats notes.** When a user asks about a fact that lives in the repo (what model am I, what does X do, where is Y configured), read the code FIRST. Notes in `memory.md` are hints, not ground truth, and can go stale. If a user pushes back on a claim, the correct next move is to widen the search, not re-cite the same note.
- **Core engineering principles (always apply):** Write serious code that follows:
  - **DRY** — Don't Repeat Yourself. Factor out duplication into well-named abstractions, but only once the duplication is real (not speculative).
  - **YAGNI** — You Aren't Gonna Need It. No speculative features, no "just in case" abstractions, no premature generality. Build what's needed now.
  - **KISS** — Keep It Simple, Stupid. Prefer the simplest design that solves the problem. Clarity > cleverness.
- **Codebase goal:** Build a simple but brilliantly effective agent harness. Optimize for simplicity and effectiveness — no bloat, no unnecessary abstractions, just a harness that works exceptionally well.
- **Code style:** Write clean, readable code with simple, concise comments that help the user understand what's going on. Comments should illuminate intent, not narrate the obvious.
