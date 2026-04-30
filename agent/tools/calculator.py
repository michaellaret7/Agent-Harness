"""Safe arithmetic calculator backed by ast — never calls eval()."""
from __future__ import annotations

import ast
import math
import operator

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_FUNCS = {
    'abs': abs, 'round': round, 'min': min, 'max': max,
    'sqrt': math.sqrt, 'log': math.log, 'log2': math.log2, 'log10': math.log10,
    'exp': math.exp, 'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
    'asin': math.asin, 'acos': math.acos, 'atan': math.atan,
    'floor': math.floor, 'ceil': math.ceil,
}

_CONSTS = {'pi': math.pi, 'e': math.e, 'tau': math.tau}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError(f'unsupported expression: {ast.unparse(node)!r}')


def calculate(expression: str) -> str:
    tree = ast.parse(expression, mode='eval')
    result = _eval(tree.body)
    return str(result)


tool = {
    'name': 'calculate',
    'description': (
        'Evaluate an arithmetic expression. Supports +, -, *, /, //, %, **, '
        'parentheses, the constants pi/e/tau, and the functions abs, round, '
        'min, max, sqrt, log, log2, log10, exp, sin, cos, tan, asin, acos, '
        'atan, floor, ceil.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'expression': {
                'type': 'string',
                'description': 'Arithmetic expression, e.g. "(2 + 3) * 4" or "sqrt(2) * pi".',
            },
        },
        'required': ['expression'],
    },
    'fn': calculate,
}
