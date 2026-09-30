"""Subprocess-backed sandbox with a persistent Python kernel.

Isolation is intentionally light: a child interpreter with a scrubbed
environment, its own working directory, and a per-call wall-clock timeout.
It contains a runaway or buggy script, not a hostile one. Stronger backends
(container, hosted) can replace this class later behind the same surface.

State persists across `exec` calls because the kernel process is long-lived.
A timeout kills the kernel and starts a fresh one, so state is lost; the
returned `ExecResult` says so via `timed_out`.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

KERNEL_PATH = Path(__file__).parent / 'kernel.py'

# Only these host variables reach the kernel. Everything else, including
# `.env` credentials the application loaded, stays on the host side.
PASSTHROUGH_ENV = ('PATH', 'SYSTEMROOT', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LANG')

#     ================================
# --> Helper funcs
#     ================================


def _scrubbed_env(extra: dict[str, str]) -> dict[str, str]:
    """Build the kernel's environment: the allowlist, Python settings, then caller extras."""
    env = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}

    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONDONTWRITEBYTECODE'] = '1'

    env.update(extra)

    return env


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
    timed_out: bool = False


class SubprocessSandbox:
    """Persistent-kernel sandbox.

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
    """

    def __init__(
        self,
        workspace: Path | None = None,
        python: str = sys.executable,
        env: dict[str, str] | None = None,
    ) -> None:
        self._owns_workspace = workspace is None
        self.workspace = workspace or Path(tempfile.mkdtemp(prefix='sandbox_'))
        self.python = python
        self.env = env or {}
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

        self._proc = subprocess.Popen(
            [self.python, '-u', str(KERNEL_PATH)],
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

            return ExecResult('', f'timed out after {timeout:g}s; kernel restarted, state lost', ok=False, timed_out=True)

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
