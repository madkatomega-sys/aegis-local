"""Code worker: a sandboxed Git worktree with allowlisted checks.

The user's repository is copied into a volume (`aegis workspace load`); this worker reads it,
applies unified diffs to it, and runs allowlisted checks. Nothing here can touch the host
checkout. Changes leave only as a diff the user exports and applies.

Commands never go through a shell. Each check is a fixed argument list, optionally followed by
one path inside the workspace.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

WORKSPACE = Path(os.environ.get("AEGIS_WORKSPACE", "/workspace/repo"))
TIMEOUT = int(os.environ.get("AEGIS_CHECK_TIMEOUT", "300"))
MAX_OUTPUT = 20_000
MAX_READ_LINES = 800

CHECKS: dict[str, list[str]] = {
    "pytest": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
    "ruff": ["ruff", "check", "--no-cache"],
    "mypy": ["mypy", "--no-incremental", "--cache-dir=/tmp/mypy"],
    "compileall": ["python", "-m", "compileall", "-q"],
}

mcp = FastMCP("code-worker", host="0.0.0.0", port=8000, stateless_http=True, json_response=True)


def _git_env() -> dict[str, str]:
    return {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"}


def _run(args: list[str], *, stdin: str | None = None, timeout: int = TIMEOUT) -> subprocess.CompletedProcess[str]:
    if not (WORKSPACE / ".git").exists():
        raise ToolError("workspace is empty; ask the user to run `python aegis.py workspace load <path>`")
    try:
        return subprocess.run(
            args,
            cwd=WORKSPACE,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_git_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{args[0]} timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise ToolError(f"{args[0]} is not installed in this worker") from exc


def _clip(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f"\n[... {len(text) - MAX_OUTPUT} characters cut]"


def _inside(relative: str) -> Path:
    candidate = (WORKSPACE / relative).resolve()
    root = WORKSPACE.resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolError(f"path {relative!r} is outside the workspace")
    return candidate


@mcp.tool()
def list_files(subdirectory: str = "") -> str:
    """List tracked and untracked (not ignored) files, optionally under a subdirectory."""
    args = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    if subdirectory:
        args += ["--", str(_inside(subdirectory).relative_to(WORKSPACE.resolve()))]
    return _clip(_run(args).stdout)


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Read a file from the workspace, with line numbers. At most 800 lines per call."""
    target = _inside(path)
    if not target.is_file():
        raise ToolError(f"{path} is not a file")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    end = min(len(lines), end_line or len(lines), start + MAX_READ_LINES - 1)
    return "\n".join(f"{n:>6}\t{lines[n - 1]}" for n in range(start, end + 1))


@mcp.tool()
def apply_patch(diff: str) -> str:
    """Apply a unified diff to the workspace. The patch is checked first and applied only if clean."""
    check = _run(["git", "apply", "--check", "--whitespace=nowarn", "-"], stdin=diff, timeout=60)
    if check.returncode != 0:
        raise ToolError("patch does not apply:\n" + _clip(check.stderr))
    applied = _run(["git", "apply", "--whitespace=nowarn", "-"], stdin=diff, timeout=60)
    if applied.returncode != 0:
        raise ToolError("patch failed:\n" + _clip(applied.stderr))
    return _clip(_run(["git", "diff", "--stat"]).stdout) or "applied"


@mcp.tool()
def run_check(check: str, path: str | None = None) -> str:
    """Run an allowlisted check (pytest, ruff, mypy, compileall), optionally on one path.
    A non-zero exit code is reported as a failure."""
    if check not in CHECKS:
        raise ToolError(f"unknown check {check!r}; available: {', '.join(sorted(CHECKS))}")
    args = list(CHECKS[check])
    if path:
        args.append(str(_inside(path).relative_to(WORKSPACE.resolve())))
    elif check in {"ruff", "mypy", "compileall"}:
        args.append(".")
    result = _run(args)
    output = _clip(f"$ {' '.join(args)}\nexit code {result.returncode}\n{result.stdout}\n{result.stderr}".strip())
    if result.returncode != 0:
        raise ToolError(output)
    return output


@mcp.tool()
def diff() -> str:
    """Show all changes in the workspace relative to the loaded commit."""
    return _clip(_run(["git", "diff", "HEAD"]).stdout) or "no changes"


@mcp.tool()
def reset_workspace() -> str:
    """Discard every change in the workspace and return to the loaded commit."""
    _run(["git", "reset", "--hard", "HEAD"], timeout=60)
    _run(["git", "clean", "-fd"], timeout=60)
    return "workspace reset"


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
