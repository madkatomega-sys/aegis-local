# math-physics-mcp

Sandboxed MCP worker for exact math and physics. The model writes expressions; SymPy and pint
compute the answers. Agents are told never to do arithmetic, algebra, or unit conversion
themselves.

| Tool | Backed by |
|---|---|
| `simplify`, `solve`, `differentiate`, `integrate`, `solve_ode` | SymPy (exact, symbolic) |
| `evaluate` | SymPy numeric evaluation at chosen precision |
| `convert_units`, `check_dimensions` | pint |

Every result includes a LaTeX rendering where it makes sense.

SymPy's expression parser evaluates Python. That's why this worker only runs inside its
container: read-only root, no capabilities, no network route, CPU and memory limits.

Planned (M4): per-call wall-clock limits, SciPy ODE and root-finding tools, plots rendered to
PNG for conservation-law checks.
