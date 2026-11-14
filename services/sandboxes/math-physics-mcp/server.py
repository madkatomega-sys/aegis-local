"""Math and physics MCP worker: exact algebra with SymPy, units with pint, numerics with SciPy.

The point of this worker is that the model never does arithmetic, algebra, or unit conversion
in its weights. It writes expressions; this worker evaluates them.

Every computation runs in its own child process with a wall-clock limit (AEGIS_EVAL_TIMEOUT
seconds). A pathological integral is killed and reported as a failure; the server keeps serving.
The container's CPU, memory, and PID limits are the backstop.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

import mathcore

EVAL_TIMEOUT = float(os.environ.get("AEGIS_EVAL_TIMEOUT", "30"))
MAX_CONCURRENT = int(os.environ.get("AEGIS_EVAL_CONCURRENCY", "2"))

# forkserver: children start from a clean, single-threaded process that already has SymPy and
# pint imported, so each call is fast and never inherits the event loop's threads.
if "forkserver" in mp.get_all_start_methods():
    _CTX = mp.get_context("forkserver")
    _CTX.set_forkserver_preload(["mathcore"])
else:  # Windows, for local development
    _CTX = mp.get_context("spawn")

_slots = asyncio.Semaphore(MAX_CONCURRENT)

mcp = FastMCP("math-physics", host="0.0.0.0", port=8000, stateless_http=True, json_response=True)


def _child(conn: Any, fn: Callable[..., Any], args: tuple[Any, ...]) -> None:
    try:
        conn.send(("ok", fn(*args)))
    except mathcore.MathError as exc:
        conn.send(("error", str(exc)))
    except Exception as exc:  # report, never crash silently
        conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def run_limited(fn: Callable[..., Any], *args: Any, timeout: float = EVAL_TIMEOUT) -> Any:
    """Run fn(*args) in a child process; kill it if it takes longer than timeout seconds."""
    receiver, sender = _CTX.Pipe(duplex=False)
    proc = _CTX.Process(target=_child, args=(sender, fn, args), daemon=True)
    proc.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            raise ToolError(f"computation took longer than {timeout:g}s and was stopped; simplify the problem")
        try:
            status, value = receiver.recv()
        except EOFError as exc:
            raise ToolError("computation process exited without a result (likely the memory limit)") from exc
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        receiver.close()
    if status == "error":
        raise ToolError(value)
    return value


async def _compute(fn: Callable[..., Any], *args: Any) -> Any:
    async with _slots:
        return await asyncio.to_thread(run_limited, fn, *args)


@mcp.tool()
async def simplify(expression: str) -> dict[str, str]:
    """Simplify an expression exactly. Example: 'sin(x)^2 + cos(x)^2'."""
    return await _compute(mathcore.simplify, expression)


@mcp.tool()
async def solve(equations: list[str], unknowns: list[str]) -> dict[str, str]:
    """Solve one or more equations for the given unknowns. Example: ['x^2 - 4 = 0'], ['x']."""
    return await _compute(mathcore.solve, equations, unknowns)


@mcp.tool()
async def differentiate(expression: str, variable: str, order: int = 1) -> dict[str, str]:
    """Differentiate an expression with respect to a variable."""
    return await _compute(mathcore.differentiate, expression, variable, order)


@mcp.tool()
async def integrate(
    expression: str, variable: str, lower: str | None = None, upper: str | None = None
) -> dict[str, str]:
    """Integrate exactly. Give lower and upper for a definite integral ('oo' is infinity)."""
    return await _compute(mathcore.integrate, expression, variable, lower, upper)


@mcp.tool()
async def solve_ode(equation: str, function: str, variable: str) -> dict[str, str]:
    """Solve an ordinary differential equation. Example: "f(t).diff(t, 2) + f(t) = 0", "f", "t"."""
    return await _compute(mathcore.solve_ode, equation, function, variable)


@mcp.tool()
async def evaluate(expression: str, values: dict[str, float] | None = None, digits: int = 15) -> dict[str, str]:
    """Evaluate an expression numerically, optionally substituting values for symbols."""
    return await _compute(mathcore.evaluate, expression, values, digits)


@mcp.tool()
async def convert_units(quantity: str, target_units: str) -> dict[str, str]:
    """Convert a quantity with units. Example: '9.81 m/s^2 * 3 s', 'km/h'."""
    return await _compute(mathcore.convert_units, quantity, target_units)


@mcp.tool()
async def check_dimensions(left: str, right: str) -> dict[str, Any]:
    """Check that two quantities have the same physical dimensions. Example: '1 kg * m / s^2', '1 N'."""
    return await _compute(mathcore.check_dimensions, left, right)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
