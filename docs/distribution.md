# Distribution

How to ship `local-agent` to end users. Researched 2026-05 against the current
state of Python packaging, PyInstaller/Nuitka, code signing, and AV reality.

The recommendation is **layered**: each tier reaches a wider audience at higher
maintenance cost. Start at Tier 1 and stop when you've covered enough users.

```
Tier 1   PyPI + `uv tool install`            — devs who have uv/pipx
Tier 2   One-line install script              — anyone willing to curl | sh
Tier 3   PyInstaller onedir + signed installer — anyone with a working OS
Skip     PyOxidizer (dead), Nuitka (no AV win), Briefcase, shiv
```

---

## Current state of this repo (blockers)

Three things in `pyproject.toml` must change before anything can ship:

1. `package = false` — uv is in workspace-only mode. Switch to a buildable
   package or move to `[tool.uv] package = true`.
2. No `[project.scripts]` entry — `uv tool install` needs a console-script
   target. Right now the only entry is `python -m agent`, which doesn't
   produce a binary on the user's PATH.
3. All runtime deps are in `[dependency-groups.agent]`. Dependency groups are
   for **dev workflows**, not published metadata. PyPI installs only read
   `[project.dependencies]`, so a `uv tool install` user would get a package
   missing `openai`, `httpx`, `pydantic`, `prompt_toolkit`, and `rich`.

Plus one runtime issue:

4. The `.env` loader assumes the CWD is the repo. An installed user runs the
   tool from anywhere — needs `platformdirs.user_config_dir("local-agent")`
   or `$XDG_CONFIG_HOME/local-agent/.env` lookup.

The Tier 1 section below shows the exact `pyproject.toml` rewrite.

---

## Tier 1 — Publish to PyPI

**Reach:** anyone with `uv` or `pipx` (most Python devs in 2026).
**Effort:** one afternoon, then ~2 commands per release.

### Maintainer changes

`pyproject.toml`:

```toml
[project]
name = "local-agent"
version = "0.1.0"
description = "Streaming, tool-calling agent client for any OpenAI-compatible endpoint."
requires-python = ">=3.12,<3.13"

dependencies = [
    "python-dotenv>=1.0",
    "openai>=1.40",
    "httpx>=0.27",
    "pydantic>=2.0",
    "prompt_toolkit>=3.0.50",
    "rich>=13.7",
    "platformdirs>=4.0",
]

[project.scripts]
local-agent = "agent.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["agent", "tools", "tui"]
```

Notes on the rewrite:

- Deps moved out of `[dependency-groups.agent]` into `[project.dependencies]`.
  The group can stay for dev-only tooling, but runtime deps must be top-level.
- `[project.scripts]` requires `agent/__main__.py` to expose a callable
  `main()`. Today it has `if __name__ == "__main__":` block — wrap that body
  in `def main(): ...` and call it from the guard.
- `packages = ["agent", "tools", "tui"]` mirrors the project's flat top-level
  layout (per `CLAUDE.md`: tools/ and tui/ are top-level, not under agent/).
- Hatchling is the simplest backend; `uv build` works with anything PEP 517.

`agent/__main__.py` (sketch):

```python
def main() -> None:
    # existing TUI bootstrap
    ...

if __name__ == "__main__":
    main()
```

`.env` discovery — replace the bare `load_dotenv()` call with:

```python
from pathlib import Path
from platformdirs import user_config_dir
from dotenv import load_dotenv

config_dir = Path(user_config_dir("local-agent"))
config_dir.mkdir(parents=True, exist_ok=True)
load_dotenv(config_dir / ".env")
load_dotenv()  # CWD fallback for dev
```

### Publish flow

```bash
uv build                          # produces dist/*.whl and *.tar.gz
uv publish                        # uploads to PyPI
```

First publish needs a PyPI account + the package name reserved. After that,
prefer **Trusted Publishing** in CI (OIDC, no tokens to rotate). See the
GitHub Actions snippet under Tier 2.

### User flow

```bash
uv tool install local-agent      # or: pipx install local-agent
local-agent                       # entry point on PATH

# Or one-shot, no install:
uvx local-agent
```

`uv tool install` creates an isolated venv, links the entry point to
`~/.local/bin/` (Unix) or `%LOCALAPPDATA%\uv\tools\` (Windows), and auto-fetches
Python 3.12 if the user doesn't have it (via `python-build-standalone`).

### Pitfalls

- **Name collision.** Check `pypi.org/project/local-agent` first. If taken,
  rename now — renaming later is painful.
- **`.env` location.** Document where it goes. Print the resolved path on
  first run if the file is missing.
- **Provider env-var naming.** Document `ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY` / `VLLM_*` clearly in the README — installed users don't
  have `.env.example` to crib from.

---

## Tier 2 — One-line install script

**Reach:** anyone willing to run `curl | sh` (effectively all CLI users).
**Effort:** one evening to set up GitHub Actions release + install script.

The cheapest pattern is to **not write a custom installer at all**. uv's own
installer handles platform/arch detection. Your `install.sh` is two lines:

```bash
#!/bin/sh
# install.sh
set -e
curl -LsSf https://astral.sh/uv/install.sh | sh
exec uv tool install local-agent
```

PowerShell equivalent:

```powershell
# install.ps1
irm https://astral.sh/uv/install.ps1 | iex
uv tool install local-agent
```

README install snippet:

```bash
curl -LsSf https://raw.githubusercontent.com/<you>/coding_agent/main/install.sh | sh
```

That's it. uv handles the platform-specific Python download, your tool gets
isolated in a venv, the entry point ends up on PATH.

### GitHub Actions: tag → PyPI

Save as `.github/workflows/release.yml`:

```yaml
name: release
on:
  push:
    tags: ['v*']

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write       # for Trusted Publishing OIDC
      contents: write       # for GitHub Release attachments
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv build
      - run: uv publish
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/*
```

Configure Trusted Publishing on PyPI: project settings → Publishing → add
`<your-gh-user>/coding_agent` + workflow `release.yml` + environment `pypi`.
Cuts the API-token rotation chore entirely.

### Pitfalls

- **Reading from `main` branch.** If you change `install.sh` between releases
  it changes for past users too. Pin to a tag for "stable" installs once you
  have one.
- **`curl | sh` is contentious.** Some users won't run it. Always show the
  manual `uv tool install` command as an alternative in the README.

---

## Tier 3 — Frozen binary (no Python required)

**Reach:** users who don't have or want Python at all.
**Effort:** weekend setup, ongoing maintenance per platform, **plus**
ongoing code-signing budget if you want clean installs on Windows/macOS.

Only worth doing once Tier 1 + 2 isn't enough. The honest truth: a frozen
Python binary is **fatter, slower to start, and more AV-prone** than the
`uv tool install` path. Pursue this only if you have users who literally
cannot run a shell command.

### PyInstaller (recommended over Nuitka)

Why PyInstaller and not Nuitka:

- Nuitka compiles to C and runs ~2-4x faster on CPU-bound code, but agent
  workloads are I/O-bound (LLM API calls dominate). The speedup doesn't
  matter.
- Nuitka builds take minutes per platform vs PyInstaller's seconds.
- **Both** still trigger Windows Defender false positives. Microsoft Defender
  literally ships a signature called `Trojan:Win64/Nuitka!pz`. Switching
  tools doesn't solve AV — code signing does.

### Setup

```bash
uv pip install pyinstaller
```

`local-agent.spec` (commit this):

```python
# local-agent.spec
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = (
    collect_submodules('prompt_toolkit')
    + collect_submodules('rich')
    + collect_submodules('pydantic')
    + collect_submodules('openai')
)

datas = (
    collect_data_files('rich')
    + [('agent/context/system_prompt.md', 'agent/context'),
       ('agent/context/memory.md', 'agent/context')]
)

a = Analysis(
    ['agent/__main__.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, name='local-agent', console=True, exclude_binaries=True)
coll = COLLECT(exe, a.binaries, a.datas, name='local-agent')
```

Three critical choices:

1. **`COLLECT` (onedir) not `--onefile`.** Onefile bundles unpack to `%TEMP%`
   at runtime, which matches malware-packer heuristics. Onedir produces a
   folder you ship inside an installer (NSIS / Inno Setup on Windows,
   `.pkg` on macOS, `.tar.gz` on Linux).
2. **No UPX.** Compression actively worsens AV false-positive rate.
3. **`datas` for system_prompt.md / memory.md.** PyInstaller doesn't auto-pick
   up data files referenced via `pathlib.Path` — they have to be listed.
   Then read them via `sys._MEIPASS` at runtime (see PyInstaller docs).

### CI: build matrix

```yaml
# .github/workflows/binaries.yml
name: binaries
on:
  push:
    tags: ['v*']

jobs:
  build:
    strategy:
      matrix:
        include:
          - { os: ubuntu-latest,   target: linux-x86_64 }
          - { os: ubuntu-24.04-arm, target: linux-aarch64 }
          - { os: macos-latest,    target: macos-arm64 }
          - { os: macos-13,        target: macos-x86_64 }
          - { os: windows-latest,  target: windows-x86_64 }
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --group agent
      - run: uv pip install pyinstaller
      - run: uv run pyinstaller local-agent.spec
      - run: tar -czf local-agent-${{ matrix.target }}.tar.gz -C dist local-agent
      - uses: actions/upload-artifact@v4
        with:
          name: local-agent-${{ matrix.target }}
          path: local-agent-${{ matrix.target }}.tar.gz
```

### Code signing (when this becomes worth doing)

**Windows.** As of the CA/B Forum rule change (Feb 2026), code-signing certs
max out at 1-year validity. Cheapest sane options:

- **Azure Artifact Signing** (formerly Trusted Signing) — **$9.99/month**,
  Microsoft-issued certs, HSM-backed, individual developers in US/Canada
  in public preview. Best $/value in 2026. Reputation accrues on Microsoft's
  side, so SmartScreen warnings fade.
- Standard OV cert from Sectigo/SSL.com — ~$130-$220/year. You manage the
  private key.
- EV cert — ~$290/year. Only worth it for kernel drivers or instant
  SmartScreen reputation.

**macOS.** Apple Developer Program $99/year. Use `xcrun notarytool submit`
in CI with an app-specific password. Hardened runtime entitlement required.

**Free option: sigstore.** PyPI auto-signs all releases with sigstore as of
Nov 2024 — that covers Tier 1/2 distribution. Sigstore **does not** satisfy
Windows SmartScreen or macOS Gatekeeper for binary distribution. Pair it
with Authenticode + Apple notarization; don't substitute.

### Antivirus reality

Even with signing, expect:

- A few VirusTotal flags on unsigned PyInstaller binaries. Submit to
  Microsoft's portal (`microsoft.com/wdsi/filesubmission`) to clear them.
  Days, not minutes.
- Defender heuristic flags on first-run downloads. Code-signing reputation
  fixes this over time.
- Nuitka does **not** clear the AV bar even when signed — budget for signing,
  not tool-switching.

---

## Skip list

| Tool | Status | Why skip |
|---|---|---|
| **PyOxidizer** | Effectively dead (no commits since 2023, creator's [obituary post](https://gregoryszorc.com/blog/2024/03/17/my-shifting-open-source-priorities/) March 2024) | No successor — astral-sh/python-build-standalone is what uv uses now |
| **Nuitka** | Active | Same AV problem as PyInstaller, much slower builds, marginal runtime gain for I/O-bound workload |
| **Briefcase / BeeWare** | Active | Overkill — produces `.msi`/`.dmg`/`.deb` with menu entries, designed for GUI apps. Tier 3 PyInstaller + installer covers the same ground with less ceremony |
| **shiv / pex** | Maintained | Produces a `.pyz` that **requires the user to have Python 3.12 installed**. If the user has Python, just use `uv tool install`. If they don't, `.pyz` doesn't help |

---

## Cross-cutting concerns

### Bash on Windows

The agent requires bash (Git Bash). Strategy:

1. **Detect and require, document loudly.** Your audience is devs — most
   have Git Bash. `tools/base/bash.py` already does this; just make the error
   message link to `git-scm.com/download/win` when nothing is found.
2. **Optional polish:** first-run check that offers
   `winget install --id Git.Git -e` (winget ships on Win10+ since 2024).
3. **Don't bundle Git Bash.** It's ~50 MB and 95% of your users have it.
4. **Don't fall back to PowerShell.** The LLM emits POSIX shell. Translating
   on the fly is a rabbit hole.

### `.env` and config files

For Tier 1+ users, `.env` can't live in the repo. Strategy:

- Look up config dir via `platformdirs.user_config_dir("local-agent")`:
  - Windows: `%LOCALAPPDATA%\local-agent\.env`
  - macOS: `~/Library/Application Support/local-agent/.env`
  - Linux: `~/.config/local-agent/.env`
- Fall back to CWD `.env` so dev workflow still works.
- On first run with no API key, print the resolved path and a one-line
  example.

### Versioning + release cadence

- Tag `vX.Y.Z` → CI publishes to PyPI + builds binaries + creates GH release.
- Bump `version` in `pyproject.toml` first, commit, tag, push tag.
- `uv tool upgrade local-agent` works once on PyPI.

---

## Recommended path for this project

1. **This week:** fix the four blockers (Tier 1 section). Reserve `local-agent`
   on PyPI. Publish a first version manually with `uv build && uv publish`.
2. **Next week:** add `.github/workflows/release.yml` with Trusted Publishing.
   Tag `v0.1.0` and verify auto-publish works.
3. **When you have actual users:** add `install.sh` + `install.ps1` for the
   one-line install. README install snippet drops to a single command.
4. **Only if non-Python users start asking:** Tier 3 PyInstaller + Azure
   Artifact Signing ($10/month). Don't pre-build this — wait for the demand
   to be real.

Total cost of steps 1-3: ~2 evenings. Step 4 only if needed.

---

## References

- [Astral uv — tool install](https://docs.astral.sh/uv/concepts/tools/)
- [Astral uv — packaging guide](https://docs.astral.sh/uv/guides/package/)
- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
- [PyInstaller spec files](https://pyinstaller.org/en/stable/spec-files.html)
- [PyInstaller AV false positives](https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/)
- [Nuitka vs PyInstaller 2026 comparison](https://ahmedsyntax.com/2026-comparison-pyinstaller-vs-cx-freeze-vs-nui/)
- [Nuitka AV signature issue](https://github.com/Nuitka/Nuitka/issues/2757)
- [Azure Artifact Signing pricing](https://azure.microsoft.com/en-us/pricing/details/artifact-signing/)
- [PyOxidizer obituary](https://gregoryszorc.com/blog/2024/03/17/my-shifting-open-source-priorities/)
- [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
- [sigstore for Python](https://www.python.org/downloads/metadata/sigstore/)
- [platformdirs](https://platformdirs.readthedocs.io/)
