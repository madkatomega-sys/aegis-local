"""The eval suite's own pieces (evals/run.py): fixtures, arms, scoring, and the report. No model needed."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("aegis_evals", ROOT / "evals" / "run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


def fixture_names():
    return sorted(path.parent.name for path in (ROOT / "evals" / "fixtures").glob("*/fixture.toml"))


def pytest_passes(path) -> bool:
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=path, capture_output=True, check=False
    )
    return run.returncode == 0


# -- fixtures --------------------------------------------------------------------------------


def test_there_are_fixtures():
    assert {"calc", "chunks", "cart", "median-crlf", "slug"} <= set(fixture_names())


@pytest.mark.parametrize("name", fixture_names())
def test_fixture_fails_as_given_and_passes_with_its_solution(ev, tmp_path, name):
    """A fixture that passes untouched, or that its own solution doesn't fix, would score nonsense."""
    fixture = ev.load_fixtures()[name]
    assert fixture.description
    assert not pytest_passes(ev.materialize(fixture, tmp_path / name))
    fixed, why = ev.score(fixture, ev.solution_patch(fixture))
    assert fixed, why


def test_crlf_fixture_is_committed_with_crlf(ev, tmp_path):
    fixture = ev.load_fixtures()["median-crlf"]
    work = ev.materialize(fixture, tmp_path / "m")
    committed = subprocess.run(["git", "show", "HEAD:stats.py"], cwd=work, capture_output=True, check=True).stdout
    assert committed.count(b"\r\n") == committed.count(b"\n") > 0


def test_fixture_commit_is_the_same_every_time(ev, tmp_path):
    fixture = ev.load_fixtures()["calc"]
    heads = []
    for name in ("one", "two"):
        work = ev.materialize(fixture, tmp_path / name)
        heads.append(subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout)
    assert heads[0] == heads[1]


def test_fixture_files_leave_out_the_description_and_solution(ev):
    names = {path.name for path in ev.fixture_files(ev.load_fixtures()["calc"])}
    assert names == {"calc.py", "pyproject.toml", "test_calc.py"}


@pytest.mark.parametrize(
    "patch, message", [(None, "no patch"), (b"", "no patch"), (b"not a diff\n", "patch does not apply")]
)
def test_score_without_a_usable_patch(ev, patch, message):
    fixed, why = ev.score(ev.load_fixtures()["calc"], patch)
    assert not fixed
    assert why.startswith(message)


def test_score_runs_the_tests_after_applying(ev):
    """A patch that applies but is wrong still fails."""
    wrong = ev.solution_patch(ev.load_fixtures()["calc"]).replace(b"/ len(values)\n", b"/ 2\n")
    fixed, why = ev.score(ev.load_fixtures()["calc"], wrong)
    assert not fixed
    assert "failed" in why


# -- arms ------------------------------------------------------------------------------------


def test_every_arm_builds_a_config(ev, repo_root, tmp_path):
    original = json.loads((repo_root / "config" / "opencode.json").read_text(encoding="utf-8"))
    for name, arm in ev.load_arms().items():
        path = ev.build_config(arm, tmp_path / name)
        config = json.loads(path.read_text(encoding="utf-8"))
        tools = config["agent"]["builder"]["tools"]
        for tool in arm.get("disable_tools", []):
            assert tools[tool] is False
        prompt = (tmp_path / name / "agents" / "builder.md").read_text(encoding="utf-8")
        expected = (
            ev.EVALS / arm["builder_prompt"] if "builder_prompt" in arm else repo_root / "config/agents/builder.md"
        )
        assert prompt == expected.read_text(encoding="utf-8")
        for agent in ("planner", "verifier", "essayist"):
            assert (tmp_path / name / "agents" / f"{agent}.md").read_bytes() == (
                repo_root / "config" / "agents" / f"{agent}.md"
            ).read_bytes()
        # Nothing but the builder's tool switches differs from this checkout's config.
        for config_ in (config, original):
            config_["agent"]["builder"]["tools"] = {
                k: v for k, v in config_["agent"]["builder"]["tools"].items() if k not in arm.get("disable_tools", [])
            }
        assert config == original


def test_neutral_prompt_is_the_builder_prompt_without_its_edit_rules(ev, repo_root):
    """If config/agents/builder.md changes, the neutral arm must change with it, or the A/B drifts."""
    builder = (repo_root / "config" / "agents" / "builder.md").read_text(encoding="utf-8")
    prefix = builder[: builder.index("- To change an existing file")]
    suffix = builder[builder.index("- After each change") :]
    neutral = (ev.EVALS / "arms" / "builder-neutral.md").read_text(encoding="utf-8")
    assert neutral.startswith(prefix)
    assert neutral.endswith(suffix)
    rules = neutral[len(prefix) : len(neutral) - len(suffix)]
    assert "edit tools" in rules
    assert "replace_in_file" not in rules
    assert "apply_patch" not in rules


def test_release_prompt_offers_only_diffs(ev):
    prompt = (ev.EVALS / "arms" / "builder-0.1.0.md").read_text(encoding="utf-8")
    assert "code__apply_patch" in prompt
    assert "replace_in_file" not in prompt


def test_schedule_interleaves_the_arms(ev):
    order = ev.schedule(["calc", "cart"], ["a", "b"], 2)
    assert len(order) == 8
    assert order[:4] == [("calc", "a", 1), ("calc", "b", 1), ("cart", "a", 1), ("cart", "b", 1)]
    assert order[4:6] == [("calc", "b", 2), ("calc", "a", 2)]


# -- scoring and report ----------------------------------------------------------------------


def test_summarize_ledger(ev):
    rows = [
        {"id": 1, "tool": "code__read_file", "decision": "allow", "rule": None, "ok": 1},
        {"id": 2, "tool": "code__replace_in_file", "decision": "allow", "rule": None, "ok": 0},
        {"id": 3, "tool": "code__replace_in_file", "decision": "trip", "rule": "retry", "ok": None},
        {"id": 4, "tool": "code__apply_patch", "decision": "allow", "rule": None, "ok": 1},
    ]
    summary = ev.summarize_ledger(rows)
    assert summary == {
        "calls": {"read_file": 1, "replace_in_file": 1, "apply_patch": 1},
        "trips": ["retry"],
        "edits_ok": 1,
        "edit_calls": 2,
    }


@pytest.mark.parametrize(
    "fixed, edits_ok, edit_calls, expected",
    [
        (True, 1, 1, "fixed"),
        (False, 1, 3, "edited, tests fail"),
        (False, 0, 2, "no edit applied"),
        (False, 0, 0, "never tried to edit"),
    ],
)
def test_outcome(ev, fixed, edits_ok, edit_calls, expected):
    assert ev.outcome(fixed, edits_ok, edit_calls) == expected


@pytest.mark.parametrize(
    "table, expected", [((9, 1, 1, 9), 0.0011), ((10, 0, 2, 8), 0.0007), ((6, 4, 3, 7), 0.37), ((5, 5, 5, 5), 1.0)]
)
def test_fisher_exact_matches_the_published_values(ev, table, expected):
    assert ev.fisher_exact(*table) == pytest.approx(expected, abs=0.0001 if expected < 0.01 else 0.01)


def test_report(ev):
    def record(arm, fixed, seconds):
        return {"fixture": "calc", "arm": arm, "fixed": fixed, "timed_out": False, "seconds": seconds,
                "outcome": "fixed" if fixed else "no edit applied"}  # fmt: skip

    records = [record("a", True, 10), record("a", False, 30), record("b", False, 20), record("b", False, 40)]
    text = ev.report(records)
    assert "| calc | a | 1/2 | 0 | 1 | 0 | 0 | 20 s |" in text
    assert "| calc | b | 0/2 | 0 | 2 | 0 | 0 | 30 s |" in text
    assert "a against b, calc: 1/2 against 0/2, p = 1" in text
