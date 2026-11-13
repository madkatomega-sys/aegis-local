"""Math and physics MCP worker: exact algebra with SymPy, units with pint, numerics with SciPy.

The point of this worker is that the model never does arithmetic, algebra, or unit conversion
in its weights. It writes expressions; this worker evaluates them.

Note: SymPy's parser evaluates Python under the hood. That's acceptable here only because this
worker runs in a container with a read-only root, no capabilities, no network route, and tight
CPU and memory limits. Don't run this server on a host directly.
"""

from __future__ import annotations

from typing import Any

import pint
import sympy as sp
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

MAX_INPUT = 4000
TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
ureg = pint.UnitRegistry()

mcp = FastMCP("math-physics", host="0.0.0.0", port=8000, stateless_http=True, json_response=True)


def _parse(text: str, symbols: list[str] | None = None) -> sp.Expr:
    if len(text) > MAX_INPUT:
        raise ToolError(f"expression longer than {MAX_INPUT} characters")
    local: dict[str, Any] = {name: sp.Symbol(name) for name in (symbols or [])}
    try:
        if "=" in text and "==" not in text:
            left, right = text.split("=", 1)
            return sp.Eq(
                parse_expr(left, local_dict=local, transformations=TRANSFORMS),
                parse_expr(right, local_dict=local, transformations=TRANSFORMS),
            )
        return parse_expr(text, local_dict=local, transformations=TRANSFORMS)
    except Exception as exc:  # SymPy raises many exception types for bad input
        raise ToolError(f"could not parse {text!r}: {exc}") from exc


def _out(expr: Any) -> dict[str, str]:
    return {"result": str(expr), "latex": sp.latex(expr)}


@mcp.tool()
def simplify(expression: str) -> dict[str, str]:
    """Simplify an expression exactly. Example: 'sin(x)^2 + cos(x)^2'."""
    return _out(sp.simplify(_parse(expression)))


@mcp.tool()
def solve(equations: list[str], unknowns: list[str]) -> dict[str, str]:
    """Solve one or more equations for the given unknowns. Example: ['x^2 - 4 = 0'], ['x']."""
    syms = [sp.Symbol(u) for u in unknowns]
    parsed = [_parse(e, unknowns) for e in equations]
    return _out(sp.solve(parsed, syms, dict=True))


@mcp.tool()
def differentiate(expression: str, variable: str, order: int = 1) -> dict[str, str]:
    """Differentiate an expression with respect to a variable."""
    return _out(sp.diff(_parse(expression, [variable]), sp.Symbol(variable), order))


@mcp.tool()
def integrate(expression: str, variable: str, lower: str | None = None, upper: str | None = None) -> dict[str, str]:
    """Integrate exactly. Give lower and upper for a definite integral ('oo' is infinity)."""
    x = sp.Symbol(variable)
    expr = _parse(expression, [variable])
    if (lower is None) != (upper is None):
        raise ToolError("give both lower and upper, or neither")
    if lower is None:
        return _out(sp.integrate(expr, x))
    return _out(sp.integrate(expr, (x, _parse(lower), _parse(upper))))


@mcp.tool()
def solve_ode(equation: str, function: str, variable: str) -> dict[str, str]:
    """Solve an ordinary differential equation. Example: "f(t).diff(t, 2) + f(t) = 0", "f", "t"."""
    t = sp.Symbol(variable)
    f = sp.Function(function)
    local = {function: f, variable: t}
    try:
        left, right = equation.split("=", 1) if "=" in equation else (equation, "0")
        eq = sp.Eq(
            parse_expr(left, local_dict=local, transformations=TRANSFORMS),
            parse_expr(right, local_dict=local, transformations=TRANSFORMS),
        )
    except Exception as exc:
        raise ToolError(f"could not parse ODE: {exc}") from exc
    return _out(sp.dsolve(eq, f(t)))


@mcp.tool()
def evaluate(expression: str, values: dict[str, float] | None = None, digits: int = 15) -> dict[str, str]:
    """Evaluate an expression numerically, optionally substituting values for symbols."""
    expr = _parse(expression, list((values or {}).keys()))
    subs = {sp.Symbol(k): v for k, v in (values or {}).items()}
    return {"result": str(sp.N(expr.subs(subs), digits))}


@mcp.tool()
def convert_units(quantity: str, target_units: str) -> dict[str, str]:
    """Convert a quantity with units. Example: '9.81 m/s^2 * 3 s', 'km/h'."""
    try:
        q = ureg(quantity)
        if not hasattr(q, "to"):
            raise ToolError(f"{quantity!r} has no units")
        converted = q.to(target_units)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"unit conversion failed: {exc}") from exc
    return {"result": f"{converted:~P}", "magnitude": str(converted.magnitude), "units": str(converted.units)}


@mcp.tool()
def check_dimensions(left: str, right: str) -> dict[str, Any]:
    """Check that two quantities have the same physical dimensions. Example: '1 kg * m / s^2', '1 N'."""
    try:
        a, b = ureg(left), ureg(right)
        dim_a = getattr(a, "dimensionality", ureg.dimensionless.dimensionality)
        dim_b = getattr(b, "dimensionality", ureg.dimensionless.dimensionality)
    except Exception as exc:
        raise ToolError(f"could not parse quantities: {exc}") from exc
    return {"consistent": dim_a == dim_b, "left": str(dim_a), "right": str(dim_b)}


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


# TODO(M4): run each evaluation in a worker process with a wall-clock limit (AEGIS_EVAL_TIMEOUT),
# so a pathological integral can't stall the server. Container CPU and memory limits are the
# backstop until then.

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
