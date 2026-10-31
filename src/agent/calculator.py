"""Safe arithmetic evaluator (ARag review §2.4.1 — "add a calculator tool").

The LLM emits an expression for any numeric computation; we evaluate it
deterministically instead of trusting the model's mental math. This is a
restricted AST walker: only numeric literals, named variables, the basic
operators, and a whitelist of functions are allowed — no attribute access, no
imports, no arbitrary calls. Anything else raises ``CalculatorError``.
"""

from __future__ import annotations

import ast
import operator

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class CalculatorError(Exception):
    pass


def _flatten(args) -> list[float]:
    out: list[float] = []
    for a in args:
        if isinstance(a, (list, tuple)):
            out.extend(float(x) for x in a)
        else:
            out.append(float(a))
    return out


def _avg(*args):
    xs = _flatten(args)
    return sum(xs) / len(xs) if xs else 0.0


_FUNCS = {
    "abs": lambda x: abs(x),
    "round": lambda x, n=0: round(x, int(n)),
    "min": lambda *a: min(_flatten(a)),
    "max": lambda *a: max(_flatten(a)),
    "sum": lambda *a: sum(_flatten(a)),
    "avg": _avg,
    "mean": _avg,
    # macro conveniences
    "change": lambda a, b: b - a,                       # absolute change
    "pct_change": lambda a, b: (b - a) / a * 100.0 if a else float("nan"),
    "ratio": lambda a, b: a / b if b else float("nan"),
}


def evaluate(expression: str, variables: dict[str, float] | None = None) -> float:
    """Evaluate ``expression`` with optional named ``variables`` -> float."""
    variables = variables or {}
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"invalid expression: {exc}") from exc
    return float(_eval(tree.body, variables))


def _eval(node, variables):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("only numeric constants are allowed")
        return node.value
    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]   # scalar or list (lists feed sum/min/max/avg)
        raise CalculatorError(f"unknown variable: {node.id}")
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"operator not allowed: {type(node.op).__name__}")
        return op(_eval(node.left, variables), _eval(node.right, variables))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY.get(type(node.op))
        if op is None:
            raise CalculatorError("unary operator not allowed")
        return op(_eval(node.operand, variables))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise CalculatorError("only whitelisted functions may be called")
        if node.keywords:
            raise CalculatorError("keyword arguments are not allowed")
        args = [_eval(a, variables) for a in node.args]
        return _FUNCS[node.func.id](*args)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, variables) for e in node.elts]
    raise CalculatorError(f"unsupported expression: {type(node).__name__}")
