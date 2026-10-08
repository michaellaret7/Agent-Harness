"""Subprocess-backed sandbox with a persistent Python kernel.

Isolation is intentionally light: a child interpreter with a scrubbed
environment, its own working directory, and a per-call wall-clock timeout.
It contains a runaway or buggy script, not a hostile one, because the kernel
still runs as the host user. For real containment, run the whole agent in a
container.

Model code sees the host environment's libraries, plus any extra `packages`
layered on top in a separate cached venv.

State persists across `exec` calls because the kernel process is long-lived.
A timeout kills the kernel and starts a fresh one, so state is lost; the
returned `ExecResult` says so in `stderr`.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent_harness.sinks import SESSION_ID

KERNEL_PATH = Path(__file__).parent / 'kernel.py'
VENV_CACHE = Path.home() / '.cache' / 'agent-harness' / 'sandbox-venvs'

# Only these host variables reach the kernel. Everything else, including
# `.env` credentials the application loaded, stays on the host side.
PASSTHROUGH_ENV = ('PATH', 'SYSTEMROOT', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LANG')

# When the host traces to Langfuse, these follow into the kernel so any Agent
# that model code builds there is traced too, under the host's session.
TRACING_ENV = ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_BASE_URL')

# How many sandboxes deep this process runs: 0 on the host, 1 in a host agent's
# kernel, 2 in the kernel of an agent built there, and so on. Agents may be built
# up to MAX_AGENT_DEPTH, so a sub-agent built in code can execute code itself but
# cannot build agents of its own. A guardrail against runaway recursion, not a
# security boundary: model code can still edit its own environment.
SANDBOX_DEPTH_ENV = 'AGENT_HARNESS_SANDBOX_DEPTH'
SANDBOX_DEPTH = int(os.environ.get(SANDBOX_DEPTH_ENV, '0'))
MAX_AGENT_DEPTH = 1

#     ================================
# --> Helper funcs
#     ================================


def _scrubbed_env(extra: dict[str, str]) -> dict[str, str]:
    """Build the kernel's environment: the allowlist, Langfuse tracing, Python settings, then caller extras."""
    env = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}

    if os.environ.get('LANGFUSE_PUBLIC_KEY'):
        env.update({key: os.environ[key] for key in TRACING_ENV if key in os.environ})
        env['LANGFUSE_SESSION_ID'] = SESSION_ID

    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONDONTWRITEBYTECODE'] = '1'

    env.update(extra)

    # Set after caller extras so `env=` cannot reset the depth
    env[SANDBOX_DEPTH_ENV] = str(SANDBOX_DEPTH + 1)

    return env


def _site_packages(python: str) -> Path:
    """Return the site-packages directory of the environment `python` belongs to."""
    found = subprocess.run(
        [python, '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'],
        capture_output=True, text=True, encoding='utf-8', check=True,
    )

    return Path(found.stdout.strip())


def _venv_python(venv: Path) -> Path:
    return venv / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def _build_venv(venv: Path, python: str, packages: tuple[str, ...]) -> None:
    """Create `venv` from `python`, install `packages`, and layer the host's libraries under them.

    Uses `uv` when it is on PATH (about a second with a warm cache), otherwise
    the stdlib `venv` module and pip.
    """
    venv_python = str(_venv_python(venv))

    if shutil.which('uv'):
        steps = [['uv', 'venv', str(venv), '--python', python], ['uv', 'pip', 'install', '--python', venv_python, *packages]]

    else:
        steps = [[python, '-m', 'venv', str(venv)], [venv_python, '-m', 'pip', 'install', *packages]]

    for step in steps:
        done = subprocess.run(step, capture_output=True, text=True, encoding='utf-8')

        if done.returncode != 0:
            raise RuntimeError(f'could not build sandbox venv for {list(packages)}:\n{done.stderr}')

    # addsitedir, not a bare path line, so the host's own .pth files (editable installs) are honoured too.
    host_libs = _site_packages(python)
    overlay = _site_packages(venv_python) / 'host_environment.pth'
    overlay.write_text(f'import site; site.addsitedir({str(host_libs)!r})\n', encoding='utf-8')


def _ensure_venv(python: str, packages: tuple[str, ...]) -> str:
    """Return the interpreter of a cached venv layering `packages` over `python`'s own libraries.

    The venv holds `packages` and their dependencies. A `.pth` file then adds
    `python`'s site-packages after the venv's, so everything the host
    environment has stays importable and the venv's copy wins on a clash.
    Nothing is installed into the host environment.

    The venv is built under a private name and renamed into place once
    complete, so a crash never leaves a half-built venv at the cached path and
    two sandboxes building the same set at once cannot corrupt each other.
    """
    cache_key = '\n'.join([*sorted(packages), python])
    venv = VENV_CACHE / hashlib.sha256(cache_key.encode()).hexdigest()[:12]

    if venv.exists():
        return str(_venv_python(venv))

    VENV_CACHE.mkdir(parents=True, exist_ok=True)
    build = venv.with_name(f'{venv.name}.build-{uuid.uuid4().hex[:8]}')

    try:
        _build_venv(build, python, packages)

        # Rename into place. If another builder won the race its finished venv stays and ours is dropped.
        try:
            os.rename(build, venv)

        except OSError:
            _remove_tree(build)

    except BaseException:
        _remove_tree(build)
        raise

    return str(_venv_python(venv))


def _remove_tree(path: Path, attempts: int = 20) -> None:
    """rmtree with retries: Windows frees a killed process's cwd handle slightly after wait() returns."""
    for _ in range(attempts):
        shutil.rmtree(path, ignore_errors=True)

        if not path.exists():
            return

        time.sleep(0.1)


#     ================================
# --> Sandbox
#     ================================


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one `exec` call."""

    stdout: str
    stderr: str
    ok: bool


class SubprocessSandbox:
    """Persistent-kernel sandbox. Not a security boundary.

    The kernel starts lazily on the first `exec` and is killed at interpreter
    exit, so a bare `SubprocessSandbox()` is enough. The context manager form
    is available when you want cleanup at a definite point instead.

    Args:
        workspace: Directory the kernel runs in. Defaults to a fresh temp dir
            that is deleted on `stop()`; a caller-supplied path is left alone.
        python: Interpreter to launch. Defaults to the host's, so packages in
            the current venv are importable inside the sandbox.
        env: Extra variables exposed inside the kernel, on top of the scrubbed
            allowlist. Pass secrets explicitly here; nothing else from the host
            environment leaks through.
        packages: Extra pip requirements for model code, e.g. `['pandas']`,
            on top of everything `python`'s environment already has. They
            go into a separate cached venv; the host environment is not
            modified.
    """

    def __init__(
        self,
        workspace: Path | None = None,
        python: str = sys.executable,
        env: dict[str, str] | None = None,
        packages: list[str] | None = None,
    ) -> None:
        self._owns_workspace = workspace is None
        self.workspace = workspace or Path(tempfile.mkdtemp(prefix='sandbox_'))
        self.python = python
        self.env = env or {}
        self.packages = tuple(packages or ())
        self._proc: subprocess.Popen[str] | None = None

        atexit.register(self.stop)

    def __enter__(self) -> 'SubprocessSandbox':
        self.start()

        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> None:
        """Launch the kernel process. Idempotent while one is already running."""
        if self._proc is not None and self._proc.poll() is None:
            return

        python = _ensure_venv(self.python, self.packages) if self.packages else self.python

        self._proc = subprocess.Popen(
            [python, '-u', str(KERNEL_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.workspace,
            env=_scrubbed_env(self.env),
            text=True,
            encoding='utf-8',
        )

    def stop(self) -> None:
        """Kill the kernel and remove the workspace if this sandbox created it."""
        self._kill()

        if self._owns_workspace:
            _remove_tree(self.workspace)

    def reset(self) -> None:
        """Restart the kernel, dropping all in-memory state. Files are kept."""
        self._kill()
        self.start()

    def exec(self, code: str, timeout: float = 60.0) -> ExecResult:
        """Run `code` in the persistent kernel, waiting at most `timeout` seconds."""
        self.start()
        proc = self._proc
        assert proc is not None and proc.stdin and proc.stdout
        stdout = proc.stdout

        proc.stdin.write(json.dumps({'code': code}) + '\n')
        proc.stdin.flush()

        # readline has no native timeout on pipes, so wait on a thread instead.
        # On timeout the kernel is killed, which closes the pipe and frees the thread.
        reply: list[str] = []
        reader = threading.Thread(target=lambda: reply.append(stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout)

        if reader.is_alive():
            self.reset()

            return ExecResult('', f'timed out after {timeout:g}s; kernel restarted, state lost', ok=False)

        if not reply or not reply[0]:
            crash = proc.stderr.read() if proc.stderr else ''
            self.reset()

            return ExecResult('', f'kernel died; restarted, state lost\n{crash}', ok=False)

        data = json.loads(reply[0])

        return ExecResult(data['stdout'], data['stderr'], data['ok'])

    def _kill(self) -> None:
        if self._proc is None:
            return

        self._proc.kill()
        self._proc.wait()
        self._proc = None
