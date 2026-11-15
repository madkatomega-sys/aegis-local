import asyncio
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def code_worker(repo_root):
    """A fresh copy of the code worker's module (services/sandboxes/code-worker/server.py)."""
    path = repo_root / "services" / "sandboxes" / "code-worker" / "server.py"
    spec = importlib.util.spec_from_file_location("code_worker_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def code_worker_tools(code_worker) -> set[str]:
    """The tool names the code worker registers, read from its MCP server rather than its source."""
    return {tool.name for tool in asyncio.run(code_worker.mcp.list_tools())}
