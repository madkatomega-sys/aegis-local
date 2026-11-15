"""The code worker's tools, run against a throwaway Git repository instead of the container's volume."""

import importlib.util
import subprocess
import sys

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# The worker runs only in its Linux container. On Windows, text-mode stdin turns each "\n" of
# a diff into "\r\n", which git then refuses to apply.
linux_only = pytest.mark.skipif(sys.platform == "win32", reason="the code worker runs on Linux")


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


BUGGY = "    return sum(values) / (len(values) + 1)"
FIXED = "    return sum(values) / len(values)"


def calc(worker) -> bytes:
    return (worker.WORKSPACE / "calc.py").read_bytes()


@linux_only
def test_apply_patch_applies_a_clean_diff(worker):
    worker.apply_patch(FIX)
    assert calc(worker).decode().splitlines()[1] == FIXED


@linux_only
def test_apply_patch_accepts_a_diff_without_its_final_newline(worker):
    """Models often drop the last newline; git would call the patch corrupt."""
    worker.apply_patch(FIX.removesuffix("\n"))
    assert b"len(values)\n" in calc(worker)


@linux_only
def test_apply_patch_recounts_a_miscounted_hunk_header(worker):
    """A 4B model wrote `@@ -2,7 +2,7 @@` over a one-line change."""
    worker.apply_patch(FIX.replace("@@ -2 +2 @@", "@@ -2,7 +2,7 @@"))
    assert b"len(values)\n" in calc(worker)


@linux_only
def test_apply_patch_still_rejects_a_diff_that_does_not_match(worker):
    with pytest.raises(ToolError, match="does not apply"):
        worker.apply_patch(FIX.replace("(len(values) + 1)", "(len(values) + 2)"))
    assert b"(len(values) + 1)" in calc(worker)


def test_replace_in_file_replaces_the_one_match(worker):
    worker.replace_in_file("calc.py", BUGGY, FIXED)
    assert calc(worker) == f"def mean(values):\n{FIXED}\n".encode()


def test_replace_in_file_rejects_text_that_is_not_there(worker):
    with pytest.raises(ToolError, match="read the file again"):
        worker.replace_in_file("calc.py", "return total / count", "return 0")
    assert BUGGY.encode() in calc(worker)


def test_replace_in_file_rejects_an_ambiguous_match(worker):
    with pytest.raises(ToolError, match="appears 3 times"):
        worker.replace_in_file("calc.py", "values", "items")
    assert BUGGY.encode() in calc(worker)


def test_replace_in_file_names_line_numbers_copied_from_read_file(worker):
    copied = worker.read_file("calc.py", 2, 2)
    with pytest.raises(ToolError, match="line number"):
        worker.replace_in_file("calc.py", copied, FIXED)
    assert BUGGY.encode() in calc(worker)


def test_replace_in_file_points_at_lines_with_different_indentation(worker):
    """The hint only says where to look; the file is not changed."""
    two_spaces = f"def mean(values):\n  {BUGGY.strip()}"
    with pytest.raises(ToolError, match="lines 1 to 2 match it apart from indentation"):
        worker.replace_in_file("calc.py", two_spaces, f"def mean(values):\n  {FIXED.strip()}")
    assert BUGGY.encode() in calc(worker)


def test_replace_in_file_keeps_crlf_line_endings(worker):
    (worker.WORKSPACE / "calc.py").write_bytes(f"def mean(values):\r\n{BUGGY}\r\n".encode())
    worker.replace_in_file("calc.py", f"def mean(values):\n{BUGGY}\n", f"def mean(values):\n{FIXED}\n")
    assert calc(worker) == f"def mean(values):\r\n{FIXED}\r\n".encode()


@pytest.mark.parametrize("old, new", [("", "x"), (BUGGY, BUGGY)])
def test_replace_in_file_rejects_empty_and_unchanged_text(worker, old, new):
    with pytest.raises(ToolError):
        worker.replace_in_file("calc.py", old, new)


@pytest.mark.parametrize("path", [".git/config", "./.git/HEAD", "missing.py", "../outside.py", "/etc/hostname"])
def test_replace_in_file_edits_only_existing_files_inside_the_workspace(worker, path):
    (worker.WORKSPACE.parent / "outside.py").write_text("x = 1\n")
    with pytest.raises(ToolError):
        worker.replace_in_file(path, "x", "y")
    assert (worker.WORKSPACE.parent / "outside.py").read_text() == "x = 1\n"
