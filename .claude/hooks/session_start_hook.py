#!/usr/bin/env python3
"""SessionStart hook: inject a *status report*, not a brochure.

Claude Code invokes this at session start with the SessionStart event JSON on
stdin. Rather than re-describe the static shape of the repo (which CLAUDE.md
already covers and the agent can derive with its own tools), this emits the
volatile state the agent genuinely can't cheaply derive:

  Tier 1 — where did we leave off : uncommitted work + branch divergence.
  Tier 2 — is the world healthy    : .env / venv / lockfile / interpreter drift.
  Tier 3 — cheap structural signal : package import graph + recent churn.

Stdlib only — no dependency on the `agent` group. Reuses `run_recon` from the
sibling `recon.py` purely for the Tier-3 fields, so the whole `.claude/` folder
stays self-contained and droppable into any codebase.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Make the sibling recon module importable regardless of the process cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from recon import run_recon

# Force UTF-8 stdout so the JSON payload survives Windows' cp1252 default.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


#     ================================
# --> Helper funcs
#     ================================


def run_git(args: list[str], repo_root: Path) -> str | None:
    """Run a git command under `repo_root`; return stdout, or None on any failure.

    Fail-quiet by design: a missing git binary or a non-repo must never block the
    session from starting, so every error collapses to None for the caller to skip.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    return result.stdout


def parse_numstat(repo_root: Path) -> dict[str, tuple[str, str]]:
    """Map each tracked, changed path to its (added, removed) line counts vs HEAD.

    Covers staged and unstaged edits together. Untracked files never appear here
    (they have no HEAD baseline) and are labelled separately by the caller.
    """
    raw = run_git(["diff", "--numstat", "HEAD"], repo_root)

    if not raw:
        return {}

    stats: dict[str, tuple[str, str]] = {}

    for line in raw.splitlines():
        parts = line.split("\t")

        if len(parts) != 3:
            continue

        added, removed, path = parts

        stats[path] = (added, removed)

    return stats


def uncommitted_changes(repo_root: Path) -> list[str]:
    """Summarize dirty files: status letter, path, and +/- line magnitude.

    The git snapshot in the system prompt lists filenames but not the shape of
    each change — this adds the change type and line counts so the agent knows
    what was mid-flight, not just which files were open.
    """
    porcelain = run_git(["status", "--porcelain=v1"], repo_root)

    if porcelain is None:
        return ["- (not a git repo, or git unavailable)"]

    if not porcelain.strip():
        return ["- working tree clean"]

    stats = parse_numstat(repo_root)
    lines: list[str] = []

    for entry in porcelain.splitlines()[:25]:
        status = entry[:2]
        path = entry[3:]

        # Renames render as "old -> new"; the new path is what carries the change.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]

        if status == "??":
            lines.append(f"- {path} — new, untracked")
            continue

        added, removed = stats.get(path, ("?", "?"))

        lines.append(f"- [{status.strip() or '·'}] {path} (+{added}/-{removed})")

    remaining = len(porcelain.splitlines()) - 25

    if remaining > 0:
        lines.append(f"- … and {remaining} more")

    return lines


def branch_divergence(repo_root: Path) -> list[str]:
    """Report the current branch and how far it is ahead/behind main and dev.

    Tells the agent whether it's sitting on a stale base or holding unpushed work
    before it reasons about merges, rebases, or where new commits should land.
    """
    current = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)

    if not current:
        return []

    current = current.strip()
    lines = [f"- current branch: `{current}`"]

    for base in ("main", "dev"):
        if base == current:
            continue

        exists = run_git(["rev-parse", "--verify", "--quiet", base], repo_root)

        if not exists:
            continue

        counts = run_git(["rev-list", "--left-right", "--count", f"{current}...{base}"], repo_root)

        if not counts:
            continue

        parts = counts.split()

        if len(parts) != 2:
            continue

        ahead, behind = parts

        lines.append(f"- vs `{base}`: {ahead} ahead, {behind} behind")

    return lines


def recent_commits(repo_root: Path, count: int = 5) -> list[str]:
    """List the last `count` commits as 'hash — subject' lines.

    The subject is the author's own summary of what each commit accomplished —
    the cheapest honest signal of recent intent, with no inference required.
    """
    raw = run_git(["log", f"-{count}", "--no-merges", "--format=%h%x09%s"], repo_root)

    if not raw:
        return []

    lines: list[str] = []

    for entry in raw.splitlines():
        parts = entry.split("\t", 1)

        if len(parts) != 2:
            continue

        short_hash, subject = parts

        lines.append(f"- `{short_hash}` — {subject}")

    return lines


def read_pyvenv_version(repo_root: Path) -> str | None:
    """Pull the interpreter minor version (e.g. '3.12') recorded in .venv/pyvenv.cfg."""
    cfg = repo_root / ".venv" / "pyvenv.cfg"

    if not cfg.is_file():
        return None

    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().lower().startswith("version"):
            value = line.split("=", 1)[-1].strip()
            parts = value.split(".")

            if len(parts) >= 2:
                return f"{parts[0]}.{parts[1]}"

    return None


def env_health(repo_root: Path) -> list[str]:
    """Cheap, file-only drift checks that catch failures before they cost cycles.

    Validates the four things that silently break a run minutes in: a missing
    .env, an absent venv, a lockfile staler than pyproject, and an interpreter
    that violates the project's `.python-version` pin.
    """
    lines: list[str] = []

    env_file = repo_root / ".env"
    lines.append(f"- .env: {'present' if env_file.is_file() else '🚨 MISSING'}")

    venv = repo_root / ".venv"

    if not venv.is_dir():
        lines.append("- venv: 🚨 absent — run `uv sync --group agent`")
    else:
        lines.append("- venv: present")

    pyproject = repo_root / "pyproject.toml"
    lockfile = repo_root / "uv.lock"

    if pyproject.is_file() and lockfile.is_file():
        if pyproject.stat().st_mtime > lockfile.stat().st_mtime:
            lines.append("- lockfile: ⚠️ uv.lock older than pyproject.toml — may be stale")
        else:
            lines.append("- lockfile: up to date with pyproject.toml")
    elif not lockfile.is_file():
        lines.append("- lockfile: 🚨 uv.lock MISSING")

    pin = ""
    pin_file = repo_root / ".python-version"

    if pin_file.is_file():
        pin = pin_file.read_text(encoding="utf-8", errors="replace").strip()

    venv_version = read_pyvenv_version(repo_root)

    if pin and venv_version:
        flag = "" if venv_version == pin else f" 🚨 pinned to {pin}"
        lines.append(f"- interpreter: venv on {venv_version}{flag}")
    elif pin:
        lines.append(f"- interpreter: pinned to {pin} (venv version unknown)")

    return lines


def tier3_signal(repo_root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Reuse recon for the two cheap structural facts worth pre-loading.

    Returns the package import graph and the recent-churn file list. Falls back
    to empty values if recon raises, so a brittle scan never blocks the session.
    """
    try:
        brief = run_recon(repo_root)
    except Exception:
        return {}, []

    return brief.package_imports, brief.recent_files


def render(repo_root: Path) -> str:
    """Assemble the three tiers into the markdown status report."""
    out: list[str] = [f"# Session status — {repo_root.name}", ""]

    out.append("## Where we left off")
    out.extend(uncommitted_changes(repo_root))

    divergence = branch_divergence(repo_root)

    if divergence:
        out.append("")
        out.extend(divergence)

    commits = recent_commits(repo_root)

    if commits:
        out.append("")
        out.append("## Recent commits")
        out.extend(commits)

    out.append("")
    out.append("## Environment health")
    out.extend(env_health(repo_root))

    package_imports, recent_files = tier3_signal(repo_root)

    if package_imports or recent_files:
        out.append("")
        out.append("## Structural signal")

        for pkg, targets in package_imports.items():
            arrow = ", ".join(targets) if targets else "(no internal deps)"
            out.append(f"- `{pkg}` → {arrow}")

        if recent_files:
            out.append(f"- recent churn: {', '.join(recent_files[:4])}")

    out.append("")

    return "\n".join(out)


#     ================================
# --> Main
#     ================================


def resolve_repo_root(event: dict) -> Path:
    """Pick the project root from the harness env var, the event cwd, or '.'."""
    candidate = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or "."

    return Path(candidate).resolve()


def main() -> int:
    raw = sys.stdin.read()

    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}

    repo_root = resolve_repo_root(event)

    if not repo_root.is_dir():
        # Fail quiet: an unusable root must not block the session from starting.
        return 0

    markdown = render(repo_root)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": markdown,
        }
    }

    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
