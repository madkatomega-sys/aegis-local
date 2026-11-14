"""The code worker's tools, run against a throwaway Git repository instead of the container's volume."""

import importlib.util
import subprocess
import sys

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# The worker runs only in its Linux container. On Windows, text-mode stdin turns each "\n" of
# the diff into "\r\n", which git then refuses to apply.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the code worker runs on Linux")


@pytest.fixture
def worker(repo_root, tmp_path, monkeypatch):
    path = repo_root / "services" / "sandboxes" / "code-worker" / "server.py"
    spec = importlib.util.spec_from_file_location("code_worker_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "calc.py").write_bytes(b"def mean(values):\n    return sum(values) / (len(values) + 1)\n")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    monkeypatch.setattr(module, "WORKSPACE", workspace)
    return module


FIX = (
    "diff --git a/calc.py b/calc.py\n"
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -2 +2 @@\n"
    "-    return sum(values) / (len(values) + 1)\n"
    "+    return sum(values) / len(values)\n"
)


def test_apply_patch_applies_a_clean_diff(worker):
    worker.apply_patch(FIX)
    assert (worker.WORKSPACE / "calc.py").read_text().splitlines()[1] == "    return sum(values) / len(values)"


def test_apply_patch_accepts_a_diff_without_its_final_newline(worker):
    """Models often drop the last newline; git would call the patch corrupt."""
    worker.apply_patch(FIX.removesuffix("\n"))
    assert "len(values)\n" in (worker.WORKSPACE / "calc.py").read_text()


def test_apply_patch_recounts_a_miscounted_hunk_header(worker):
    """A 4B model wrote `@@ -2,7 +2,7 @@` over a one-line change."""
    worker.apply_patch(FIX.replace("@@ -2 +2 @@", "@@ -2,7 +2,7 @@"))
    assert "len(values)\n" in (worker.WORKSPACE / "calc.py").read_text()


def test_apply_patch_still_rejects_a_diff_that_does_not_match(worker):
    with pytest.raises(ToolError, match="does not apply"):
        worker.apply_patch(FIX.replace("(len(values) + 1)", "(len(values) + 2)"))
    assert "(len(values) + 1)" in (worker.WORKSPACE / "calc.py").read_text()
