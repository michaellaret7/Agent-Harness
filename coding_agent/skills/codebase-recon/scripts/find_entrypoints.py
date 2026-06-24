#!/usr/bin/env python3
"""
Locate entry points in a codebase.

Usage:
    python find_entrypoints.py <repo-path>

Reports:
    - Declared CLI scripts (pyproject.toml [project.scripts], package.json bin)
    - Files with `if __name__ == "__main__":`
    - Web framework instantiations (FastAPI, Flask, Starlette, Django)
    - Library public surface (top-level __init__.py with __all__)

Standard library only. Read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

IGNORE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "vendor",
    ".claude",
}


def walk_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in filenames:
            yield Path(dirpath) / fn


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def find_pyproject_scripts(root: Path) -> list[str]:
    f = root / "pyproject.toml"
    if not f.exists():
        return []
    text = read_text(f)
    m = re.search(r"\[project\.scripts\](.*?)(?=^\[|\Z)", text, re.DOTALL | re.MULTILINE)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, target = line.partition("=")
            out.append(f"{name.strip()} → {target.strip().strip(chr(34)).strip(chr(39))}")
    return out


def find_package_json_bins(root: Path) -> list[str]:
    f = root / "package.json"
    if not f.exists():
        return []
    try:
        data = json.loads(read_text(f))
    except json.JSONDecodeError:
        return []
    out: list[str] = []
    bin_entry = data.get("bin")
    if isinstance(bin_entry, str):
        out.append(f"{data.get('name', '?')} → {bin_entry}")
    elif isinstance(bin_entry, dict):
        for k, v in bin_entry.items():
            out.append(f"{k} → {v}")
    if data.get("main"):
        out.append(f"[main] {data['main']}")
    return out


def find_main_blocks(root: Path) -> list[str]:
    pat = re.compile(r'^\s*if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:', re.MULTILINE)
    out = []
    for p in walk_files(root):
        if p.suffix != ".py":
            continue
        if pat.search(read_text(p)):
            out.append(str(p.relative_to(root)).replace("\\", "/"))
    return sorted(out)


def find_web_apps(root: Path) -> list[str]:
    pat = re.compile(r"\b(FastAPI|Flask|Starlette|Sanic|Quart)\s*\(")
    out = []
    for p in walk_files(root):
        if p.suffix != ".py":
            continue
        m = pat.search(read_text(p))
        if m:
            rel = str(p.relative_to(root)).replace("\\", "/")
            out.append(f"{m.group(1)} in {rel}")
    return sorted(out)


def find_public_surface(root: Path) -> list[str]:
    """Top-level package __init__.py files exposing __all__."""
    out = []
    for p in walk_files(root):
        if p.name != "__init__.py":
            continue
        # Only top-level packages (one dir below root)
        rel = p.relative_to(root)
        if len(rel.parts) != 2:
            continue
        text = read_text(p)
        m = re.search(r"__all__\s*=\s*\[(.*?)\]", text, re.DOTALL)
        if m:
            names = re.findall(r'["\']([^"\']+)["\']', m.group(1))
            out.append(f"{rel.parts[0]}: {', '.join(names)}")
        elif text.strip():
            out.append(f"{rel.parts[0]}: (no __all__, has code)")
    return sorted(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Find entry points in a codebase")
    parser.add_argument("repo", help="Path to the repo root")
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    if not root.is_dir():
        print(f"Error: not a directory: {root}", file=sys.stderr)
        return 1

    sections = [
        ("Declared CLI scripts (pyproject)", find_pyproject_scripts(root)),
        ("Declared CLI scripts (package.json)", find_package_json_bins(root)),
        ("__main__ blocks", find_main_blocks(root)),
        ("Web app instantiations", find_web_apps(root)),
        ("Top-level public surface", find_public_surface(root)),
    ]

    any_found = False
    for title, items in sections:
        if not items:
            continue
        any_found = True
        print(f"## {title}")
        for it in items:
            print(f"  - {it}")
        print()

    if not any_found:
        print("No entry points detected.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
