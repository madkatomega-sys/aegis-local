"""Pure computation for the math and physics worker: SymPy for exact algebra, pint for units.

Nothing here knows about MCP. Each function takes plain strings and numbers and returns a
JSON-friendly dict, or raises MathError with a message that is safe to show the model. The
server runs every call in a separate process with a wall-clock limit, so these functions can be
slow or even hang without stalling the worker.

SymPy's parser evaluates Python under the hood. That's acceptable only because the worker runs in
a container with a read-only root, no capabilities, no network route, and tight CPU and memory
limits. Don't expose these functions on a host directly.
"""

from __future__ import annotations

from typing import Any

import pint
import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

MAX_INPUT = 4000
TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
ureg = pint.UnitRegistry()


class MathError(ValueError):
    """Bad input or a computation that can't be done. The message is shown to the model."""


def _parse(text: str, symbols: list[str] | None = None, local: dict[str, Any] | None = None) -> Any:
    if len(text) > MAX_INPUT:
        raise MathError(f"expression longer than {MAX_INPUT} characters")
    names: dict[str, Any] = {name: sp.Symbol(name) for name in (symbols or [])}
    names.update(local or {})
    try:
        if "=" in text and "==" not in text:
            left, right = text.split("=", 1)
            return sp.Eq(
                parse_expr(left, local_dict=names, transformations=TRANSFORMS),
                parse_expr(right, local_dict=names, transformations=TRANSFORMS),
            )
        return parse_expr(text, local_dict=names, transformations=TRANSFORMS)
    except Exception as exc:  # SymPy raises many exception types for bad input
        raise MathError(f"could not parse {text!r}: {exc}") from exc


def _out(expr: Any) -> dict[str, str]:
    return {"result": str(expr), "latex": sp.latex(expr)}


def simplify(expression: str) -> dict[str, str]:
    return _out(sp.simplify(_parse(expression)))


def solve(equations: list[str], unknowns: list[str]) -> dict[str, str]:
    if not equations or not unknowns:
        raise MathError("give at least one equation and one unknown")
    syms = [sp.Symbol(u) for u in unknowns]
    parsed = [_parse(e, unknowns) for e in equations]
    return _out(sp.solve(parsed, syms, dict=True))


def differentiate(expression: str, variable: str, order: int = 1) -> dict[str, str]:
    if order < 1:
        raise MathError("order must be at least 1")
    return _out(sp.diff(_parse(expression, [variable]), sp.Symbol(variable), order))


def integrate(expression: str, variable: str, lower: str | None = None, upper: str | None = None) -> dict[str, str]:
    if (lower is None) != (upper is None):
        raise MathError("give both lower and upper, or neither")
    x = sp.Symbol(variable)
    expr = _parse(expression, [variable])
    if lower is None:
        return _out(sp.integrate(expr, x))
    return _out(sp.integrate(expr, (x, _parse(lower), _parse(upper))))


def solve_ode(equation: str, function: str, variable: str) -> dict[str, str]:
    t = sp.Symbol(variable)
    f = sp.Function(function)
    text = equation if "=" in equation else f"{equation} = 0"
    eq = _parse(text, local={function: f, variable: t})
    return _out(sp.dsolve(eq, f(t)))


def evaluate(expression: str, values: dict[str, float] | None = None, digits: int = 15) -> dict[str, str]:
    if not 1 <= digits <= 1000:
        raise MathError("digits must be between 1 and 1000")
    expr = _parse(expression, list((values or {}).keys()))
    subs = {sp.Symbol(k): v for k, v in (values or {}).items()}
    return {"result": str(sp.N(expr.subs(subs), digits))}


def convert_units(quantity: str, target_units: str) -> dict[str, str]:
    try:
        q = ureg(quantity)
    except Exception as exc:
        raise MathError(f"could not parse {quantity!r}: {exc}") from exc
    if not hasattr(q, "to"):
        raise MathError(f"{quantity!r} has no units")
    try:
        converted = q.to(target_units)
    except Exception as exc:
        raise MathError(f"unit conversion failed: {exc}") from exc
    return {"result": f"{converted:~P}", "magnitude": str(converted.magnitude), "units": str(converted.units)}


def check_dimensions(left: str, right: str) -> dict[str, Any]:
    try:
        a, b = ureg(left), ureg(right)
    except Exception as exc:
        raise MathError(f"could not parse quantities: {exc}") from exc
    dim_a = getattr(a, "dimensionality", ureg.dimensionless.dimensionality)
    dim_b = getattr(b, "dimensionality", ureg.dimensionless.dimensionality)
    return {"consistent": dim_a == dim_b, "left": str(dim_a), "right": str(dim_b)}
