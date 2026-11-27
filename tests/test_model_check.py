"""docs/validation/model_check.py stops before downloading anything when a setup step fails."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def model_check(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("model_check", ROOT / "docs" / "validation" / "model_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    catalog = {
        "small": {"path": "small.gguf", "size": 10, "sha256": "0" * 64, "repo": "org/r", "name": "s", "license": "x"},
        "big": {
            "path": "big.gguf",
            "size": 3_000_000_000,
            "sha256": "1" * 64,
            "repo": "org/r",
            "name": "b",
            "license": "x",
        },
    }
    profile = {"profile": {"name": "p"}, "tiers": {"tier0": {"download": "small"}, "tier1": {"download": "big"}}}
    monkeypatch.setattr(module.aegis, "load_catalog", lambda: catalog)
    monkeypatch.setattr(module.aegis, "load_profile", lambda name: profile)
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append([str(c) for c in cmd])
        if cmd[:3] == ["git", "worktree", "add"]:
            Path(cmd[4]).mkdir(parents=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.OUT.clear()
    module.FAILURES.clear()
    return module, calls


def run_main(module, monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["model_check.py", *args])
    return module.main()


def helper_calls(calls):
    return [c for c in calls if "aegis.py" in c]


def test_a_failed_hard_link_stops_before_any_download(model_check, monkeypatch, tmp_path):
    module, calls = model_check
    have = tmp_path / "have"
    have.mkdir()
    (have / "big.gguf").write_bytes(b"x")

    def no_link(src, dst):
        raise OSError("Invalid switch")  # what a mangled mklink looked like in 0.3's run

    monkeypatch.setattr(module.os, "link", no_link)
    out = tmp_path / "out.txt"
    code = run_main(module, monkeypatch, "--profile", "p", "--max-gb", "1", "--dir", str(tmp_path / "m"),
                    "--link-from", str(have), "--out", str(out))  # fmt: skip
    assert code == 2
    assert helper_calls(calls) == []  # `models get` never ran
    assert "stopped before downloading anything" in out.read_text(encoding="utf-8")


def test_a_plan_bigger_than_max_gb_stops_before_any_download(model_check, monkeypatch, tmp_path):
    module, calls = model_check
    out = tmp_path / "out.txt"
    code = run_main(module, monkeypatch, "--profile", "p", "--max-gb", "1", "--dir", str(tmp_path / "m"),
                    "--out", str(out))  # fmt: skip
    assert code == 2
    assert helper_calls(calls) == []
    assert "within --max-gb 1" in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("contents", [None, "something.gguf"])
def test_an_existing_folder_is_refused_and_left_alone(model_check, monkeypatch, tmp_path, contents):
    module, calls = model_check
    used = tmp_path / "m"
    used.mkdir()
    if contents:
        (used / contents).write_bytes(b"x")
    code = run_main(module, monkeypatch, "--profile", "p", "--max-gb", "10", "--dir", str(used),
                    "--out", str(tmp_path / "out.txt"))  # fmt: skip
    assert code == 2
    assert not [c for c in calls if c[:2] == ["git", "worktree"]]  # no checkout was made or removed
    assert used.is_dir()  # the cleanup doesn't remove a folder it didn't create
    if contents:
        assert (used / contents).exists()


def test_files_behind_hard_links_survive_the_cleanup(model_check, monkeypatch, tmp_path):
    module, calls = model_check
    have = tmp_path / "have"
    have.mkdir()
    (have / "small.gguf").write_bytes(b"0123456789")  # the catalog's size for "small"
    out = tmp_path / "out.txt"
    # "big" isn't there and is over the limit, so setup stops after linking "small".
    code = run_main(module, monkeypatch, "--profile", "p", "--max-gb", "1", "--dir", str(tmp_path / "m"),
                    "--link-from", str(have), "--out", str(out))  # fmt: skip
    assert code == 2
    assert not (tmp_path / "m").exists()  # the folder this run made is gone
    assert (have / "small.gguf").read_bytes() == b"0123456789"  # the linked-to file is not
