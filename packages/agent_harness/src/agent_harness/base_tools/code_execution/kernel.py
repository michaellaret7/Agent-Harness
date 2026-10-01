"""Persistent Python kernel that runs inside the sandbox subprocess.

Launched by `SubprocessSandbox` as a standalone script (never imported).
Reads one JSON request per line from stdin, runs the code in a single
long-lived namespace so variables survive across calls, and writes one JSON
reply per line back. The protocol runs on private copies of the original
stdin / stdout. File descriptors 0, 1 and 2 are repointed before user code
runs, so its prints, and the output of any child process it spawns, land in
the reply instead of corrupting the protocol.

Request:  {"code": "<python source>"}
Reply:    {"stdout": "...", "stderr": "...", "ok": true|false}

If the last statement is a bare expression its repr is echoed, matching
notebook / REPL behaviour so `df.head()` on its own line shows output.

This file is a leaf: stdlib only, no `agent_harness` imports, because it
runs in a scrubbed environment where the package may not be importable.
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
import tempfile
import traceback
from types import TracebackType
from typing import IO, Any

# Per stream. Head and tail are kept so both the setup and the result survive.
MAX_OUTPUT_BYTES = 20_000

#     ================================
# --> Helper funcs
#     ================================


def _run(code: str, namespace: dict[str, Any]) -> None:
    """Exec `code` in `namespace`, echoing the final expression's value like a REPL."""
    tree = ast.parse(code, filename='<sandbox>', mode='exec')

    last_expr: ast.Expr | None = None

    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_expr = tree.body.pop()  # type: ignore[assignment]

    if tree.body:
        exec(compile(tree, '<sandbox>', 'exec'), namespace)

    if last_expr is not None:
        expression = ast.Expression(body=last_expr.value)
        ast.fix_missing_locations(expression)

        value = eval(compile(expression, '<sandbox>', 'eval'), namespace)

        if value is not None:
            print(repr(value))


def _user_frames(exc: BaseException) -> TracebackType | None:
    """Drop the kernel's own frames from a traceback so the model sees only its code."""
    tb = exc.__traceback__

    while tb is not None and tb.tb_frame.f_code.co_filename != '<sandbox>':
        tb = tb.tb_next

    return tb


def _read_back(capture: IO[bytes]) -> str:
    """Return what was written to `capture`, keeping only the head and tail of oversized output.

    Truncating here, before the reply is built, keeps a runaway print loop
    from turning into a giant string in the kernel and on the wire.
    """
    size = os.fstat(capture.fileno()).st_size
    capture.seek(0)

    if size <= MAX_OUTPUT_BYTES:
        data = capture.read()

    else:
        half = MAX_OUTPUT_BYTES // 2
        head = capture.read(half)

        capture.seek(size - half)
        tail = capture.read(half)

        data = head + f'\n... [{size - MAX_OUTPUT_BYTES} bytes truncated] ...\n'.encode() + tail

    return data.decode('utf-8', errors='replace').replace('\r\n', '\n')


def _handle(code: str, namespace: dict[str, Any]) -> dict[str, Any]:
    """Run one request and capture everything it wrote or raised."""
    ok = True

    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        # Redirect at the descriptor level, not just sys.stdout, so output
        # from child processes and C extensions is captured too.
        os.dup2(out.fileno(), 1)
        os.dup2(err.fileno(), 2)

        # Fresh text streams per request: user code that closes or replaces
        # sys.stdout only affects this call, and closefd keeps fd 1 / 2 open.
        sys.stdout = io.TextIOWrapper(io.FileIO(1, 'w', closefd=False), encoding='utf-8', write_through=True)
        sys.stderr = io.TextIOWrapper(io.FileIO(2, 'w', closefd=False), encoding='utf-8', write_through=True)

        try:
            _run(code, namespace)

        except BaseException as exc:  # noqa: BLE001 — user code may raise anything, including SystemExit
            ok = False
            traceback.print_exception(type(exc), exc, _user_frames(exc))

        return {'stdout': _read_back(out), 'stderr': _read_back(err), 'ok': ok}


#     ================================
# --> Entry point
#     ================================


def main() -> None:
    # Keep private copies of the protocol pipes, then point stdin at nothing so
    # user code and its child processes cannot swallow the next request.
    protocol_in = os.fdopen(os.dup(0), 'r', encoding='utf-8')
    protocol_out = os.fdopen(os.dup(1), 'w', encoding='utf-8')
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)

    namespace: dict[str, Any] = {'__name__': '__main__'}

    for line in protocol_in:
        request = json.loads(line)

        reply = _handle(request['code'], namespace)

        protocol_out.write(json.dumps(reply) + '\n')
        protocol_out.flush()


if __name__ == '__main__':
    main()
