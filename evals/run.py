#!/usr/bin/env python3
"""Aegis-Local evals: ask the builder agent to fix small broken projects, and score what it changed.

Each fixture in evals/fixtures/ is a tiny Python project whose tests fail. A run loads a fresh copy
into the sandbox, sends the builder agent one plain request through OpenCode, the guard and the
local models, and exports the sandbox's diff. The diff is scored by applying it to a clean copy of
the fixture and running pytest there; what the model said doesn't count.

    python evals/run.py list
    python evals/run.py run [--fixtures calc,chunks] [--arms default,no-replace] [--runs 10]
                            [--out DIR] [--timeout 900] [--opencode PATH]
    python evals/run.py report DIR

`run` needs the stack (`python aegis.py up`) and OpenCode. Results go to evals/results/<time>/,
one JSON line per run in runs.jsonl, with transcripts and patches beside it. Running again with the
same --out skips the runs already recorded, so an interrupted batch can be resumed.

Standard library only, like aegis.py.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from math import comb
from pathlib import Path
from typing import Any

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent
sys.path.insert(0, str(ROOT))
import aegis  # noqa: E402  (the launcher lives at the repository root)

FIXTURES = EVALS / "fixtures"
ARMS_FILE = EVALS / "arms.toml"
RESULTS = EVALS / "results"
NOT_FIXTURE_FILES = {"fixture.toml", "solution.patch"}
DEFAULT_PROMPT = "The tests in the sandbox workspace fail. Find the bug, fix it, and show me that the tests pass."
OUTCOMES = ("fixed", "edited, tests fail", "no edit applied", "never tried to edit")
EDIT_TOOLS = ("code__apply_patch", "code__replace_in_file")
# A fixed author and date, so a fixture's commit is the same on every run and every machine.
FIXTURE_GIT_ENV = {
    "GIT_AUTHOR_NAME": "aegis eval",
    "GIT_AUTHOR_EMAIL": "eval@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "aegis eval",
    "GIT_COMMITTER_EMAIL": "eval@example.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# -- fixtures --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fixture:
    name: str
    path: Path
    description: str
    prompt: str
    crlf: bool


def load_fixtures() -> dict[str, Fixture]:
    fixtures = {}
    for spec in sorted(FIXTURES.glob("*/fixture.toml")):
        data = tomllib.loads(spec.read_text(encoding="utf-8"))
        name = spec.parent.name
        fixtures[name] = Fixture(
            name, spec.parent, data["description"], data.get("prompt", DEFAULT_PROMPT), bool(data.get("crlf"))
        )
    return fixtures


def fixture_files(fixture: Fixture) -> list[Path]:
    """The project's own files: everything in the fixture folder except its description and solution."""
    return [
        path
        for path in sorted(fixture.path.rglob("*"))
        if path.is_file()
        and path.name not in NOT_FIXTURE_FILES
        and not {"__pycache__", ".pytest_cache"} & set(path.relative_to(fixture.path).parts)
    ]


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **FIXTURE_GIT_ENV}
    command = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", *args]
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=True)


def materialize(fixture: Fixture, dest: Path) -> Path:
    """Write the fixture to `dest` as a one-commit Git repository, with CRLF endings if it asks for them."""
    dest.mkdir(parents=True, exist_ok=False)
    for source in fixture_files(fixture):
        target = dest / source.relative_to(fixture.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        if fixture.crlf:
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        target.write_bytes(data)
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", f"fixture {fixture.name}")
    return dest


def solution_patch(fixture: Fixture) -> bytes:
    """The reference fix. The repository stores it with LF; a CRLF fixture's lines get CRLF to match."""
    data = (fixture.path / "solution.patch").read_bytes().replace(b"\r\n", b"\n")
    if not fixture.crlf:
        return data
    lines = data.split(b"\n")
    in_hunk = False
    for i, line in enumerate(lines):
        if line.startswith(b"@@"):
            in_hunk = True
        elif line.startswith(b"diff --git"):
            in_hunk = False
        elif in_hunk and line[:1] in (b" ", b"+", b"-"):
            lines[i] = line + b"\r"
    return b"\n".join(lines)


def score(fixture: Fixture, patch: bytes | None) -> tuple[bool, str]:
    """Apply `patch` to a clean copy of the fixture and run its tests: (passed, pytest's last line or why not)."""
    if not patch or not patch.strip():
        return False, "no patch"
    with tempfile.TemporaryDirectory() as tmp:
        work = materialize(fixture, Path(tmp) / fixture.name)
        (Path(tmp) / "change.patch").write_bytes(patch)
        applied = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(Path(tmp) / "change.patch")],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )
        if applied.returncode != 0:
            return False, "patch does not apply: " + applied.stderr.strip()[:160]
        try:
            tests = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "pytest timed out after 120 s"
        last = (tests.stdout.strip().splitlines() or ["(no output)"])[-1]
        return tests.returncode == 0, last


# -- arms ------------------------------------------------------------------------------------


def load_arms() -> dict[str, dict[str, Any]]:
    return tomllib.loads(ARMS_FILE.read_text(encoding="utf-8"))


def build_config(arm: dict[str, Any], dest: Path) -> Path:
    """This checkout's OpenCode config with the arm's builder changes, written to `dest`. Returns opencode.json."""
    shutil.copytree(ROOT / "config" / "agents", dest / "agents")
    if arm.get("builder_prompt"):
        shutil.copyfile(EVALS / arm["builder_prompt"], dest / "agents" / "builder.md")
    config = json.loads((ROOT / "config" / "opencode.json").read_text(encoding="utf-8"))
    tools = config["agent"]["builder"].setdefault("tools", {})
    for tool in arm.get("disable_tools", []):
        tools[tool] = False
    path = dest / "opencode.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def schedule(fixtures: list[str], arms: list[str], runs: int) -> list[tuple[str, str, int]]:
    """Every (fixture, arm, run number), interleaved so no arm always goes first or runs last."""
    order = []
    for n in range(1, runs + 1):
        shift = (n - 1) % len(arms)
        for fixture in fixtures:
            order += [(fixture, arm, n) for arm in arms[shift:] + arms[:shift]]
    return order


def run_name(fixture: str, arm: str, n: int) -> str:
    return f"{fixture}-{arm}-{n:02d}"


# -- scoring helpers -------------------------------------------------------------------------


def outcome(fixed: bool, edits_ok: int, edit_calls: int) -> str:
    if fixed:
        return OUTCOMES[0]
    if edits_ok:
        return OUTCOMES[1]
    if edit_calls:
        return OUTCOMES[2]
    return OUTCOMES[3]


def summarize_ledger(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calls the guard allowed, breaker trips, and successful edits, from the run's ledger rows."""
    allowed = [row for row in rows if row["decision"] == "allow"]
    return {
        "calls": dict(Counter(row["tool"].removeprefix("code__") for row in allowed)),
        "trips": [row["rule"] for row in rows if row["decision"] == "trip"],
        "edits_ok": sum(1 for row in allowed if row["tool"] in EDIT_TOOLS and row["ok"] == 1),
        "edit_calls": sum(1 for row in allowed if row["tool"] in EDIT_TOOLS),
    }


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact test for the 2x2 table [[a, b], [c, d]]."""
    n, row, col = a + b + c + d, a + b, a + c

    def p(x: int) -> float:
        return comb(row, x) * comb(n - row, col - x) / comb(n, col)

    observed = p(a)
    return min(
        1.0, sum(p(x) for x in range(max(0, col - (n - row)), min(row, col) + 1) if p(x) <= observed * (1 + 1e-9))
    )


def report(records: list[dict[str, Any]]) -> str:
    """A plain-text summary: outcomes per fixture and arm, then pairwise comparisons of the arms."""
    fixtures = list(dict.fromkeys(r["fixture"] for r in records))
    arms = list(dict.fromkeys(r["arm"] for r in records))
    columns = ["Fixture", "Arm", "Fixed", *(o.capitalize() for o in OUTCOMES[1:]), "Timed out", "Median time"]
    lines = ["| " + " | ".join(columns) + " |", "|---" * len(columns) + "|"]
    for fixture, arm in itertools.product(fixtures, arms):
        group = [r for r in records if r["fixture"] == fixture and r["arm"] == arm]
        if not group:
            continue
        counts = Counter(r["outcome"] for r in group)
        lines.append(
            f"| {fixture} | {arm} | {counts[OUTCOMES[0]]}/{len(group)} | "
            + " | ".join(str(counts[o]) for o in OUTCOMES[1:])
            + f" | {sum(r['timed_out'] for r in group)} | {statistics.median(r['seconds'] for r in group):g} s |"
        )
    if len(arms) > 1:
        lines += ["", "Two-sided Fisher exact p for each pair of arms (fixed against not fixed):", ""]
        for first, second in itertools.combinations(arms, 2):
            for fixture in [*fixtures, None]:
                a_runs = [r for r in records if r["arm"] == first and fixture in (None, r["fixture"])]
                b_runs = [r for r in records if r["arm"] == second and fixture in (None, r["fixture"])]
                if not a_runs or not b_runs:
                    continue
                a, c = sum(r["fixed"] for r in a_runs), sum(r["fixed"] for r in b_runs)
                p = fisher_exact(a, len(a_runs) - a, c, len(b_runs) - c)
                lines.append(
                    f"- {first} against {second}, {fixture or 'all fixtures'}: "
                    f"{a}/{len(a_runs)} against {c}/{len(b_runs)}, p = {p:.2g}"
                )
    return "\n".join(lines) + "\n"


def read_records(out: Path) -> list[dict[str, Any]]:
    path = out / "runs.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# -- the live stack --------------------------------------------------------------------------


class Stack:
    """The pieces of the running harness a run talks to: the sandbox, the guard's ledger, OpenCode."""

    def __init__(self, opencode: str | None) -> None:
        self.cfg = aegis.settings()
        self.runtime = aegis.detect_runtime(self.cfg) or sys.exit("no container runtime found; start the stack first")
        self.env = aegis.read_env_file(aegis.TOKENS)
        self.token = aegis.ensure_tokens()["AEGIS_CLIENT_TOKEN"]
        self.opencode = opencode or shutil.which("opencode") or sys.exit("opencode not found; pass --opencode")
        version = subprocess.run([self.opencode, "--version"], capture_output=True, text=True, check=False)
        self.opencode_version = version.stdout.strip() or "unknown"
        # OpenCode 2.x runs a background server unless told not to; a scripted run wants neither.
        self.standalone = (
            self.opencode_version.split(".")[0].isdigit() and int(self.opencode_version.split(".")[0]) >= 2
        )

    def load(self, source: Path) -> None:
        if aegis.workspace_load(self.runtime, self.env, source) != 0:
            raise RuntimeError(f"could not load {source} into the sandbox")

    def diff(self) -> bytes:
        return aegis.workspace_diff(self.runtime, self.env)

    def ledger(self, sql: str, params: tuple[int, ...] = ()) -> Any:
        """Rows from the guard's audit ledger, read inside its container (the ledger never leaves it)."""
        code = (
            "import sqlite3, json, sys; "
            "db = sqlite3.connect('/data/ledger.sqlite'); "
            "print(json.dumps(db.execute(sys.argv[1], [int(a) for a in sys.argv[2:]]).fetchall()))"
        )
        args = ("exec", "-T", "mcp-guard-proxy", "python", "-c", code, sql, *map(str, params))
        run = aegis.compose(self.runtime, *args, env=self.env, capture=True)
        if run.returncode != 0:
            raise RuntimeError("could not read the guard's ledger: " + run.stderr.strip())
        return json.loads(run.stdout)

    def last_event(self) -> int:
        return int(self.ledger("select coalesce(max(id), 0) from events")[0][0])

    def events(self, first: int, last: int) -> list[dict[str, Any]]:
        sql = "select id, tool, decision, rule, ok from events where id between ? and ? order by id"
        rows = self.ledger(sql, (first, last))
        return [dict(zip(("id", "tool", "decision", "rule", "ok"), row, strict=True)) for row in rows]

    def models(self) -> dict[str, str]:
        """Which model file each tier serves, as llama-server reports it."""
        profile = aegis.load_profile(self.cfg.get("AEGIS_PROFILE") or "cpu-only")
        host = aegis.llama_bind_host(self.cfg, self.runtime)
        found = {}
        for tier in aegis.TIERS:
            port = profile["tiers"][tier]["port"]
            try:
                found[tier] = Path(aegis.http_json(f"http://{host}:{port}/props")["model_path"]).name
            except (urllib.error.URLError, ConnectionError, TimeoutError, KeyError, ValueError):
                found[tier] = "unknown (llama-server did not answer /props)"
        return found

    def opencode_env(self, config: Path) -> dict[str, str]:
        # A private OpenCode home per version, so personal settings and plugins don't leak into a run.
        home = aegis.STATE / "eval-opencode" / self.opencode_version
        env = {**os.environ, "OPENCODE_CONFIG": str(config), "AEGIS_CLIENT_TOKEN": self.token}
        for name in ("CONFIG", "DATA", "STATE", "CACHE"):
            env[f"XDG_{name}_HOME"] = str(home / name.lower())
        return env


class GpuSampler:
    """GPU memory and load every 5 s while a run is going, if nvidia-smi is installed."""

    def __init__(self) -> None:
        self.samples: list[tuple[int, int]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True) if shutil.which("nvidia-smi") else None

    def _loop(self) -> None:
        query = ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"]
        while not self._stop.is_set():
            out = subprocess.run(query, capture_output=True, text=True, check=False).stdout.splitlines()
            if out:
                memory, load = (int(value) for value in out[0].split(","))
                self.samples.append((memory, load))
            self._stop.wait(5)

    def __enter__(self) -> GpuSampler:
        if self._thread:
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)

    def summary(self) -> dict[str, int] | None:
        if not self.samples:
            return None
        return {
            "samples": len(self.samples),
            "max_memory_mb": max(memory for memory, _ in self.samples),
            "mean_load_percent": round(statistics.mean(load for _, load in self.samples)),
        }


def kill_tree(process: subprocess.Popen[bytes]) -> None:
    if aegis.IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        os.killpg(process.pid, signal.SIGKILL)  # type: ignore[attr-defined]  # POSIX only
    process.wait(timeout=30)


def run_one(
    stack: Stack, out: Path, fixture: Fixture, arm: str, config: Path, n: int, timeout: float
) -> dict[str, Any]:
    name = run_name(fixture.name, arm, n)
    work = out / "work" / name
    if work.exists():
        shutil.rmtree(work)
    materialize(fixture, work)
    stack.load(work)
    first = stack.last_event() + 1
    command = [stack.opencode, "run", "--agent", "builder", *(["--standalone"] if stack.standalone else [])]
    started = datetime.now(UTC)
    clock = time.monotonic()
    raw = out / "work" / f"{name}.out"
    with GpuSampler() as gpu, raw.open("wb") as sink:
        process = subprocess.Popen(
            [*command, "--", fixture.prompt],
            cwd=work,
            env=stack.opencode_env(config),
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=not aegis.IS_WINDOWS,
        )
        try:
            process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            kill_tree(process)
            timed_out = True
    seconds = round(time.monotonic() - clock)
    patch = stack.diff()
    last = stack.last_event()
    ledger = summarize_ledger(stack.events(first, last))
    fixed, pytest_line = score(fixture, patch)
    record = {
        "name": name,
        "fixture": fixture.name,
        "arm": arm,
        "n": n,
        "opencode": stack.opencode_version,
        "started": started.isoformat(timespec="seconds"),
        "seconds": seconds,
        "timed_out": timed_out,
        "ledger_first": first,
        "ledger_last": last,
        "patch": f"{name}.patch" if patch.strip() else None,
        "fixed": fixed,
        "pytest": pytest_line,
        "outcome": outcome(fixed, ledger["edits_ok"], ledger["edit_calls"]),
        **ledger,
        "gpu": gpu.summary(),
    }
    if record["patch"]:
        (out / "patches" / record["patch"]).write_bytes(patch)
    transcript = ANSI.sub("", raw.read_bytes().decode("utf-8", errors="replace"))
    header = f"=== {name}  OpenCode {stack.opencode_version}  started {record['started']}\nprompt: {fixture.prompt}\n\n"
    footer = f"\n=== {seconds} s, timed out: {timed_out}, ledger events {first}-{last}, {record['outcome']}\n"
    (out / "transcripts" / f"{name}.txt").write_text(header + transcript + footer, encoding="utf-8", newline="\n")
    return record


# -- commands --------------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    print("fixtures:")
    for fixture in load_fixtures().values():
        print(f"  {fixture.name:14} {fixture.description}")
    print("arms:")
    for name, arm in load_arms().items():
        print(f"  {name:20} {arm.get('description', '')}")
    return 0


def pick(requested: str | None, available: dict[str, Any], kind: str, default: list[str]) -> list[str]:
    names = [name.strip() for name in requested.split(",")] if requested else default
    unknown = [name for name in names if name not in available]
    if unknown:
        sys.exit(f"unknown {kind}: {', '.join(unknown)} (see `python evals/run.py list`)")
    return names


def cmd_run(args: argparse.Namespace) -> int:
    fixtures, arms = load_fixtures(), load_arms()
    fixture_names = pick(args.fixtures, fixtures, "fixture", list(fixtures))
    arm_names = pick(args.arms, arms, "arm", ["default"])
    out = (Path(args.out) if args.out else RESULTS / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")).resolve()
    for sub in ("work", "patches", "transcripts", "configs"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    stack = Stack(args.opencode)
    configs = {}
    for arm in arm_names:
        if not (out / "configs" / arm).exists():
            build_config(arms[arm], out / "configs" / arm)
        configs[arm] = out / "configs" / arm / "opencode.json"
    manifest = out / "manifest.json"
    if not manifest.exists():
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False)
        info = {
            "started": datetime.now(UTC).isoformat(timespec="seconds"),
            "aegis_commit": head.stdout.strip(),
            "uncommitted_changes": bool(dirty.stdout.strip()),
            "opencode": stack.opencode_version,
            "profile": stack.cfg.get("AEGIS_PROFILE") or "cpu-only",
            "models": stack.models(),
            "fixtures": fixture_names,
            "arms": {name: arms[name] for name in arm_names},
            "runs": args.runs,
            "timeout_seconds": args.timeout,
        }
        manifest.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    done = {r["name"] for r in read_records(out)}
    plan = [step for step in schedule(fixture_names, arm_names, args.runs) if run_name(*step) not in done]
    print(f"{len(plan)} runs to go in {out} ({len(done)} already recorded)")
    for fixture, arm, n in plan:
        record = run_one(stack, out, fixtures[fixture], arm, configs[arm], n, args.timeout)
        with (out / "runs.jsonl").open("a", encoding="utf-8") as sink:
            sink.write(json.dumps(record) + "\n")
        print(f"{record['name']:28} {record['outcome']:20} {record['seconds']:>4} s  {record['pytest']}", flush=True)
    summary = report(read_records(out))
    (out / "summary.md").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    records = read_records(Path(args.dir))
    if not records:
        sys.exit(f"no runs recorded in {args.dir}")
    print(report(records), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evals/run.py", description="Aegis-Local evals")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list fixtures and arms").set_defaults(func=cmd_list)
    p = sub.add_parser("run", help="run the builder on fixtures and score the results")
    p.add_argument("--fixtures", help="comma-separated fixture names (default: all)")
    p.add_argument("--arms", help="comma-separated arm names from evals/arms.toml (default: default)")
    p.add_argument("--runs", type=int, default=1, help="runs per fixture and arm (default: 1)")
    p.add_argument("--out", help="results folder; an existing one is resumed (default: evals/results/<time>)")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds before a run is stopped (default: 900)")
    p.add_argument("--opencode", help="path to the OpenCode executable (default: opencode on PATH)")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("report", help="summarize a results folder")
    p.add_argument("dir")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
