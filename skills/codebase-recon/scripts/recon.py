#!/usr/bin/env python3
"""
Produce a structured brief of a codebase.

Usage:
    python recon.py <repo-path> [--format brief|json] [--max-depth N] [--no-git]

Examples:
    python recon.py .
    python recon.py /path/to/repo --format json
    python recon.py . --max-depth 2

The brief format is human-readable Markdown intended for the agent to read
once and then reference. The JSON format is for programmatic consumption.

Dependencies: standard library only. Uses `git` CLI if available; falls back
gracefully if not.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Force UTF-8 stdout so the brief renders on Windows consoles (cp1252 default).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

IGNORE_DIRS = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox",
    "node_modules", "bower_components",
    "dist", "build", "target", "out",
    ".next", ".nuxt", ".svelte-kit",
    "vendor", "third_party",
    ".idea", ".vscode",
    ".claude",  # tool-specific; users typically don't want it scanned
}

SOURCE_EXTS = {
    ".py": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#",
    ".swift": "Swift",
}

GENERATED_PATTERNS = [
    re.compile(r"_pb2(_grpc)?\.py$"),
    re.compile(r"\.pb\.go$"),
    re.compile(r"\.generated\."),
]

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Brief:
    name: str = ""
    purpose: str = ""
    language: str = ""
    language_version: str = ""
    framework_hints: list[str] = field(default_factory=list)
    file_count: int = 0
    loc: int = 0
    layout: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    deps_prod: list[str] = field(default_factory=list)
    deps_dev: list[str] = field(default_factory=list)
    lockfile: str = ""
    test_framework: str = ""
    test_file_count: int = 0
    source_file_count: int = 0
    ci: list[str] = field(default_factory=list)
    large_files: list[tuple[str, int]] = field(default_factory=list)
    todo_counts: dict[str, int] = field(default_factory=dict)
    smells: dict[str, list[str]] = field(default_factory=dict)
    recent_files: list[str] = field(default_factory=list)
    suggested_reads: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_ignored(path: Path, repo_root: Path) -> bool:
    rel_parts = path.relative_to(repo_root).parts
    return any(p in IGNORE_DIRS for p in rel_parts)


def is_generated(path: Path) -> bool:
    name = path.name
    return any(p.search(name) for p in GENERATED_PATTERNS)


def walk_files(repo_root: Path):
    for dirpath, dirnames, filenames in os.walk(repo_root):
        # Prune ignored dirs in-place
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if is_generated(p):
                continue
            yield p


def count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Section detectors
# ---------------------------------------------------------------------------


def detect_identity(repo_root: Path, brief: Brief) -> None:
    # Project name
    pyproject = repo_root / "pyproject.toml"
    package_json = repo_root / "package.json"
    cargo_toml = repo_root / "Cargo.toml"
    go_mod = repo_root / "go.mod"

    if pyproject.exists():
        text = read_text_safe(pyproject)
        m = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            brief.name = m.group(1)
        m = re.search(r'^\s*description\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            brief.purpose = m.group(1)
        m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
        if m:
            brief.language_version = m.group(1)

    if not brief.name and package_json.exists():
        try:
            data = json.loads(read_text_safe(package_json))
            brief.name = data.get("name", "") or brief.name
            brief.purpose = data.get("description", "") or brief.purpose
        except json.JSONDecodeError:
            pass

    if not brief.name and cargo_toml.exists():
        text = read_text_safe(cargo_toml)
        m = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            brief.name = m.group(1)

    if not brief.name:
        brief.name = repo_root.resolve().name

    # Purpose fallback: README first paragraph
    if not brief.purpose:
        for readme_name in ("README.md", "README.rst", "README.txt", "README"):
            readme = repo_root / readme_name
            if readme.exists():
                text = read_text_safe(readme)
                # Skip headings and badges; find first prose line
                for line in text.splitlines():
                    s = line.strip()
                    if not s or s.startswith("#") or s.startswith("!["):
                        continue
                    if s.startswith("[![") or s.startswith("<"):
                        continue
                    brief.purpose = s[:200]
                    break
                break

    # Python version fallback
    if not brief.language_version:
        pv = repo_root / ".python-version"
        if pv.exists():
            brief.language_version = read_text_safe(pv).strip()

    # Dominant language
    counts: Counter[str] = Counter()
    for p in walk_files(repo_root):
        lang = SOURCE_EXTS.get(p.suffix.lower())
        if lang:
            counts[lang] += 1
    if counts:
        brief.language = counts.most_common(1)[0][0]


def detect_layout(repo_root: Path, brief: Brief, max_depth: int = 3) -> None:
    lines: list[str] = []

    def walk(d: Path, depth: int, prefix: str = ""):
        if depth > max_depth:
            return
        try:
            entries = sorted(
                [e for e in d.iterdir() if e.name not in IGNORE_DIRS and not e.name.startswith(".")],
                key=lambda e: (not e.is_dir(), e.name),
            )
        except PermissionError:
            return
        for i, e in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{e.name}{'/' if e.is_dir() else ''}")
            if e.is_dir():
                ext_prefix = prefix + ("    " if is_last else "│   ")
                walk(e, depth + 1, ext_prefix)

    walk(repo_root, 1)
    brief.layout = lines


def detect_entry_points(repo_root: Path, brief: Brief) -> None:
    eps: list[str] = []

    # pyproject.toml [project.scripts]
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        text = read_text_safe(pyproject)
        m = re.search(r"\[project\.scripts\](.*?)(?=^\[|\Z)", text, re.DOTALL | re.MULTILINE)
        if m:
            for line in m.group(1).splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    name = line.split("=")[0].strip()
                    target = line.split("=", 1)[1].strip().strip('"')
                    eps.append(f"[script] {name} → {target}")

    # package.json bin
    package_json = repo_root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(read_text_safe(package_json))
            bin_entry = data.get("bin", {})
            if isinstance(bin_entry, str):
                eps.append(f"[bin] {data.get('name', '?')} → {bin_entry}")
            elif isinstance(bin_entry, dict):
                for k, v in bin_entry.items():
                    eps.append(f"[bin] {k} → {v}")
            main = data.get("main")
            if main:
                eps.append(f"[main] {main}")
        except json.JSONDecodeError:
            pass

    # __main__ blocks and FastAPI/Flask instantiation
    main_re = re.compile(r'^\s*if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:', re.MULTILINE)
    web_re = re.compile(r'\b(FastAPI|Flask)\s*\(')

    for p in walk_files(repo_root):
        if p.suffix != ".py":
            continue
        rel = str(p.relative_to(repo_root)).replace("\\", "/")
        text = read_text_safe(p)
        if main_re.search(text):
            eps.append(f"[__main__] {rel}")
        m = web_re.search(text)
        if m:
            eps.append(f"[{m.group(1)}] {rel}")

    brief.entry_points = eps


def detect_config(repo_root: Path, brief: Brief) -> None:
    env_vars: set[str] = set()
    config_files: list[str] = []

    # .env.example etc.
    for name in (".env.example", ".env.template", ".env.sample", ".env.dist"):
        f = repo_root / name
        if f.exists():
            config_files.append(name)
            for line in read_text_safe(f).splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    env_vars.add(line.split("=")[0].strip())

    # Env var references in Python source
    py_env_re = re.compile(r'os\.(?:environ\.get|environ|getenv)\s*[\(\[]\s*[\'"]([A-Z_][A-Z0-9_]*)[\'"]')
    js_env_re = re.compile(r"process\.env\.([A-Z_][A-Z0-9_]*)")
    js_env_re2 = re.compile(r"process\.env\[\s*[\'\"]([A-Z_][A-Z0-9_]*)[\'\"]\s*\]")

    for p in walk_files(repo_root):
        if p.suffix == ".py":
            for m in py_env_re.finditer(read_text_safe(p)):
                env_vars.add(m.group(1))
        elif p.suffix in {".js", ".ts", ".jsx", ".tsx", ".mjs"}:
            text = read_text_safe(p)
            for m in js_env_re.finditer(text):
                env_vars.add(m.group(1))
            for m in js_env_re2.finditer(text):
                env_vars.add(m.group(1))

    # Common config files at root
    for name in ("config.toml", "config.yaml", "config.yml", "config.json", "config.ini", "settings.py"):
        if (repo_root / name).exists():
            config_files.append(name)

    brief.env_vars = sorted(env_vars)
    brief.config_files = config_files


def _parse_toml_array(text: str, key: str) -> list[str]:
    """Parse a simple TOML array of strings without taking a tomli dep."""
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=\s*\[(.*?)\]', re.DOTALL | re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return []
    body = m.group(1)
    return [item.strip().strip('"').strip("'") for item in re.findall(r'["\']([^"\']+)["\']', body)]


def detect_dependencies(repo_root: Path, brief: Brief) -> None:
    pyproject = repo_root / "pyproject.toml"
    package_json = repo_root / "package.json"

    if pyproject.exists():
        text = read_text_safe(pyproject)
        prod = _parse_toml_array(text, "dependencies")
        brief.deps_prod = prod
        # optional-dependencies.dev or [tool.uv] dev-dependencies
        m = re.search(r"\[project\.optional-dependencies\](.*?)(?=^\[|\Z)", text, re.DOTALL | re.MULTILINE)
        if m:
            for line in m.group(1).splitlines():
                if "=" in line:
                    arr = re.findall(r'["\']([^"\']+)["\']', line)
                    brief.deps_dev.extend(arr)
        m = re.search(r"\[tool\.uv\](.*?)(?=^\[|\Z)", text, re.DOTALL | re.MULTILINE)
        if m:
            block = m.group(1)
            arr_m = re.search(r"dev-dependencies\s*=\s*\[(.*?)\]", block, re.DOTALL)
            if arr_m:
                brief.deps_dev.extend(re.findall(r'["\']([^"\']+)["\']', arr_m.group(1)))

    if package_json.exists():
        try:
            data = json.loads(read_text_safe(package_json))
            brief.deps_prod.extend([f"{k}@{v}" for k, v in data.get("dependencies", {}).items()])
            brief.deps_dev.extend([f"{k}@{v}" for k, v in data.get("devDependencies", {}).items()])
        except json.JSONDecodeError:
            pass

    # Lockfiles
    for name in ("uv.lock", "poetry.lock", "Pipfile.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "go.sum"):
        if (repo_root / name).exists():
            brief.lockfile = name
            break


def detect_tests(repo_root: Path, brief: Brief) -> None:
    test_files = 0
    source_files = 0

    for p in walk_files(repo_root):
        if p.suffix not in SOURCE_EXTS:
            continue
        rel = p.relative_to(repo_root)
        parts = rel.parts
        is_test = (
            "tests" in parts or "test" in parts or "__tests__" in parts
            or p.name.startswith("test_")
            or p.name.endswith("_test.py")
            or p.stem.endswith(".test")
            or p.stem.endswith(".spec")
        )
        if is_test:
            test_files += 1
        else:
            source_files += 1

    brief.test_file_count = test_files
    brief.source_file_count = source_files

    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists() and "[tool.pytest" in read_text_safe(pyproject):
        brief.test_framework = "pytest"
    elif (repo_root / "pytest.ini").exists():
        brief.test_framework = "pytest"
    elif (repo_root / "vitest.config.ts").exists() or (repo_root / "vitest.config.js").exists():
        brief.test_framework = "vitest"
    elif (repo_root / "jest.config.js").exists() or (repo_root / "jest.config.ts").exists():
        brief.test_framework = "jest"
    elif test_files > 0:
        brief.test_framework = "unknown"

    # CI detection
    gh = repo_root / ".github" / "workflows"
    if gh.exists() and gh.is_dir():
        wfs = [f.name for f in gh.iterdir() if f.suffix in {".yml", ".yaml"}]
        if wfs:
            brief.ci.append(f"GitHub Actions ({len(wfs)} workflow{'s' if len(wfs) != 1 else ''})")
    if (repo_root / ".gitlab-ci.yml").exists():
        brief.ci.append("GitLab CI")
    if (repo_root / ".circleci" / "config.yml").exists():
        brief.ci.append("CircleCI")
    if (repo_root / "Jenkinsfile").exists():
        brief.ci.append("Jenkins")


def detect_smells(repo_root: Path, brief: Brief) -> None:
    smells: dict[str, list[str]] = defaultdict(list)
    todo_counts: Counter[str] = Counter()
    large_files: list[tuple[str, int]] = []

    bare_except_re = re.compile(r"^\s*except\s*:\s*(?:#.*)?$", re.MULTILINE)
    open_no_enc_re = re.compile(r"\bopen\s*\(\s*[^)]*\)")
    write_text_no_enc_re = re.compile(r"\.(write_text|read_text)\s*\(\s*[^)]*\)")
    todo_re = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")

    for p in walk_files(repo_root):
        rel_str = str(p.relative_to(repo_root)).replace("\\", "/")
        loc = count_lines(p)

        if p.suffix in SOURCE_EXTS and loc > 500:
            large_files.append((rel_str, loc))

        text = read_text_safe(p)
        if not text:
            continue

        # Universal: TODO markers
        for m in todo_re.finditer(text):
            todo_counts[m.group(1)] += 1

        if p.suffix == ".py":
            if bare_except_re.search(text):
                smells["bare_except"].append(rel_str)

            for m in open_no_enc_re.finditer(text):
                snippet = m.group(0)
                if "encoding" not in snippet and '"rb"' not in snippet and "'rb'" not in snippet \
                        and '"wb"' not in snippet and "'wb'" not in snippet:
                    smells["open_without_encoding"].append(rel_str)
                    break  # one report per file is enough

            for m in write_text_no_enc_re.finditer(text):
                snippet = m.group(0)
                if "encoding" not in snippet:
                    smells["write_text_without_encoding"].append(rel_str)
                    break

            if re.search(r"^\s*from\s+[\w.]+\s+import\s+\*", text, re.MULTILINE):
                smells["wildcard_import"].append(rel_str)

            type_ignore_count = len(re.findall(r"#\s*type:\s*ignore", text))
            if type_ignore_count >= 3:
                smells["type_ignore_cluster"].append(f"{rel_str} ({type_ignore_count})")

        if p.suffix in {".ts", ".tsx", ".js", ".jsx"}:
            if re.search(r":\s*any\b", text) or re.search(r"\bas\s+any\b", text):
                smells["any_type"].append(rel_str)
            if "@ts-ignore" in text or "@ts-expect-error" in text:
                smells["ts_ignore"].append(rel_str)

    # Dedupe lists
    brief.smells = {k: sorted(set(v))[:10] for k, v in smells.items() if v}
    brief.todo_counts = dict(todo_counts)
    large_files.sort(key=lambda t: -t[1])
    brief.large_files = large_files[:10]


def detect_recent_files(repo_root: Path, brief: Brief, use_git: bool = True) -> None:
    if not use_git:
        return
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--since=1.month.ago", "--name-only", "--pretty=format:"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    if out.returncode != 0:
        return
    counts: Counter[str] = Counter()
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(part in IGNORE_DIRS for part in line.split("/")):
            continue
        counts[line] += 1
    brief.recent_files = [f"{f} ({n})" for f, n in counts.most_common(5)]


def compute_totals(repo_root: Path, brief: Brief) -> None:
    file_count = 0
    loc = 0
    for p in walk_files(repo_root):
        if p.suffix in SOURCE_EXTS:
            file_count += 1
            loc += count_lines(p)
    brief.file_count = file_count
    brief.loc = loc


def suggest_reads(brief: Brief) -> None:
    suggestions: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(path: str, reason: str):
        if path and path not in seen:
            suggestions.append((path, reason))
            seen.add(path)

    # Entry points first
    for ep in brief.entry_points[:2]:
        # ep formats: "[__main__] path", "[script] name → path", "[FastAPI] path"
        m = re.search(r"→\s*(\S+)", ep)
        path = m.group(1) if m else ep.split(" ", 1)[-1]
        add(path, "entry point")

    # Largest source file
    if brief.large_files:
        path, loc = brief.large_files[0]
        add(path, f"largest file ({loc} LOC)")

    # Recent activity
    for entry in brief.recent_files[:2]:
        path = entry.split(" (")[0]
        add(path, "recently modified")

    brief.suggested_reads = suggestions[:5]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_brief(brief: Brief) -> str:
    out: list[str] = []
    out.append(f"# {brief.name} — Codebase Brief")
    out.append("")
    if brief.purpose:
        out.append(f"**Purpose:** {brief.purpose}")
    stack_bits = []
    if brief.language:
        bit = brief.language
        if brief.language_version:
            bit += f" {brief.language_version}"
        stack_bits.append(bit)
    if stack_bits:
        out.append(f"**Stack:** {', '.join(stack_bits)}")
    out.append(f"**Size:** {brief.file_count} source files, {brief.loc} LOC")
    out.append("")

    if brief.layout:
        out.append("## Layout")
        out.append("```")
        out.extend(brief.layout[:60])
        if len(brief.layout) > 60:
            out.append(f"... ({len(brief.layout) - 60} more entries)")
        out.append("```")
        out.append("")

    if brief.entry_points:
        out.append("## Entry points")
        for ep in brief.entry_points[:15]:
            out.append(f"- {ep}")
        out.append("")

    if brief.env_vars or brief.config_files:
        out.append("## Configuration")
        if brief.env_vars:
            out.append(f"- Env vars ({len(brief.env_vars)}): {', '.join(brief.env_vars[:20])}")
        if brief.config_files:
            out.append(f"- Config files: {', '.join(brief.config_files)}")
        out.append("")

    out.append("## Dependencies")
    out.append(f"- Production ({len(brief.deps_prod)}): {', '.join(brief.deps_prod[:15]) if brief.deps_prod else 'none'}")
    out.append(f"- Dev ({len(brief.deps_dev)}): {', '.join(brief.deps_dev[:15]) if brief.deps_dev else 'none'}")
    out.append(f"- Lockfile: {brief.lockfile or 'MISSING'}")
    out.append("")

    out.append("## Tests")
    if brief.test_file_count == 0:
        out.append("- 🚨 No test files detected")
    else:
        ratio = brief.test_file_count / max(brief.source_file_count, 1)
        out.append(
            f"- {brief.test_file_count} test files / {brief.source_file_count} source "
            f"(ratio {ratio:.2f}), framework: {brief.test_framework or 'unknown'}"
        )
    if brief.ci:
        out.append(f"- CI: {', '.join(brief.ci)}")
    else:
        out.append("- CI: none detected")
    out.append("")

    if brief.large_files or brief.todo_counts or brief.recent_files:
        out.append("## Areas of interest")
        for path, loc in brief.large_files[:5]:
            out.append(f"- `{path}` — {loc} LOC")
        if brief.todo_counts:
            parts = [f"{k}={v}" for k, v in sorted(brief.todo_counts.items(), key=lambda x: -x[1])]
            out.append(f"- TODO markers: {', '.join(parts)}")
        if brief.recent_files:
            out.append(f"- Recent activity: {', '.join(brief.recent_files)}")
        out.append("")

    if brief.smells:
        out.append("## Risks / smells")
        for kind, files in brief.smells.items():
            sample = files[0] if files else ""
            out.append(f"- `{kind}`: {len(files)} file(s); e.g. `{sample}`")
        out.append("")

    if brief.suggested_reads:
        out.append("## Suggested next reads")
        for i, (path, reason) in enumerate(brief.suggested_reads, 1):
            out.append(f"{i}. `{path}` — {reason}")
        out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_recon(repo_root: Path, max_depth: int = 3, use_git: bool = True) -> Brief:
    brief = Brief()
    detect_identity(repo_root, brief)
    compute_totals(repo_root, brief)
    detect_layout(repo_root, brief, max_depth=max_depth)
    detect_entry_points(repo_root, brief)
    detect_config(repo_root, brief)
    detect_dependencies(repo_root, brief)
    detect_tests(repo_root, brief)
    detect_smells(repo_root, brief)
    detect_recent_files(repo_root, brief, use_git=use_git)
    suggest_reads(brief)
    return brief


def main() -> int:
    parser = argparse.ArgumentParser(description="Produce a structured brief of a codebase.")
    parser.add_argument("repo", help="Path to the repo root")
    parser.add_argument("--format", choices=["brief", "json"], default="brief")
    parser.add_argument("--max-depth", type=int, default=3, help="Layout tree depth (default 3)")
    parser.add_argument("--no-git", action="store_true", help="Skip git-based recent-files detection")
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"Error: not a directory: {repo_root}", file=sys.stderr)
        return 1

    brief = run_recon(repo_root, max_depth=args.max_depth, use_git=not args.no_git)

    if args.format == "json":
        # dataclass tuples → lists for JSON
        data = asdict(brief)
        data["large_files"] = [list(t) for t in brief.large_files]
        data["suggested_reads"] = [list(t) for t in brief.suggested_reads]
        print(json.dumps(data, indent=2))
    else:
        print(render_brief(brief))
    return 0


if __name__ == "__main__":
    sys.exit(main())
