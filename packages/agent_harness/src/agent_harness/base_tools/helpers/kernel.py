"""Persistent Python kernel that runs inside the sandbox subprocess.

Launched by `SubprocessSandbox` as a standalone script (never imported).
Reads one JSON request per line from stdin, runs the code in a single
long-lived namespace so variables survive across calls, and writes one JSON
reply per line to the real stdout. User code sees a redirected stdout /
stderr, so its prints land in the reply instead of corrupting the protocol.

Request:  {"code": "<python source>"}
Reply:    {"stdout": "...", "stderr": "...", "ok": true|false}

If the last statement is a bare expression its repr is echoed, matching
notebook / REPL behaviour so `df.head()` on its own line shows output.

This file is a leaf: stdlib only, no `agent_harness` imports, because it
runs in a scrubbed environment where the package may not be importable.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import traceback
from typing import Any

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


def _handle(code: str, namespace: dict[str, Any]) -> dict[str, Any]:
    """Run one request and capture everything it wrote or raised."""
    out = io.StringIO()
    err = io.StringIO()
    ok = True

    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            _run(code, namespace)

        except BaseException:  # noqa: BLE001 — user code may raise anything, including SystemExit
            ok = False
            traceback.print_exc()

    return {'stdout': out.getvalue(), 'stderr': err.getvalue(), 'ok': ok}


#     ================================
# --> Entry point
#     ================================


def main() -> None:
    protocol_out = sys.stdout
    namespace: dict[str, Any] = {'__name__': '__main__'}

    for line in sys.stdin:
        request = json.loads(line)

        reply = _handle(request['code'], namespace)

        protocol_out.write(json.dumps(reply) + '\n')
        protocol_out.flush()


if __name__ == '__main__':
    main()
