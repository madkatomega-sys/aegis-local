"""Hardware walkthrough for a release: the launcher, a real model, and every user-facing flow.

Runs on the machine being validated, from the repository root, with .env pointing at a
llama-server build and model files. Writes a transcript of every command and its output.

    python docs/validation/walkthrough.py --profile nvidia-12gb --label cuda \
        --out docs/validation/<release>-<machine>/E5-walkthrough-cuda.txt \
        --sample-repo <path to a small git repo with a failing test>

Standard library only, plus tests/smoke/smoke.py for the stack checks.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "smoke"))
sys.path.insert(0, str(ROOT))

import smoke  # noqa: E402

import aegis  # noqa: E402

PY = sys.executable
OUT: list[str] = []


def log(text: str = "") -> None:
    print(text, flush=True)
    OUT.append(text)


def run(title: str, cmd: list[str], env: dict[str, str] | None = None, check_ok: bool = True) -> str:
    log(f"\n## {title}\n$ {' '.join(cmd)}")
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, capture_output=True, text=True, check=False)
    output = (proc.stdout + proc.stderr).rstrip()
    log(output)
    log(f"[exit {proc.returncode}, {time.monotonic() - started:.1f}s]")
    if check_ok and proc.returncode != 0:
        smoke.FAILURES.append(title)
    return output


def tier_speeds() -> None:
    log("\n## Measured generation speed (from llama-server logs)")
    for tier in aegis.TIERS:
        path = aegis.LOGS / f"{tier}.log"
        # llama-server logs a "prompt eval time" and an "eval time" line per request; the latter is generation.
        gen = [
            float(m.group(1))
            for line in path.read_text("utf-8", "replace").splitlines()
            if "eval time" in line and "prompt" not in line
            for m in [re.search(r"([0-9.]+) tokens per second", line)]
            if m
        ]
        if gen:
            log(
                f"  {tier}: median {statistics.median(gen):.1f} tok/s over {len(gen)} requests "
                f"(min {min(gen):.1f}, max {max(gen):.1f})"
            )
        else:
            log(f"  {tier}: no generation lines found")
    used = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    log(f"  GPU memory in use with both tiers loaded: {used}")


def listening() -> None:
    log("\n## Listening sockets for the harness ports")
    out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, check=False).stdout
    ports = (":4000 ", ":4100 ", ":8080 ", ":8081 ")
    rows = [line.strip() for line in out.splitlines() if "LISTENING" in line and any(p in line + " " for p in ports)]
    for row in rows:
        log("  " + row)
    exposed = [r for r in rows if not r.split()[1].startswith(("127.0.0.1:", "[::1]:"))]
    smoke.check("every harness port listens on loopback only", bool(rows) and not exposed, "; ".join(exposed))

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.connect(("192.0.2.1", 9))  # no packet is sent; this only picks the LAN interface
    lan_ip = probe.getsockname()[0]
    probe.close()
    reachable = []
    for port in (4000, 4100, 8080, 8081):
        with socket.socket() as s:
            s.settimeout(2)
            if s.connect_ex((lan_ip, port)) == 0:
                reachable.append(port)
    smoke.check(f"nothing reachable on the LAN address {lan_ip}", not reachable, str(reachable))


def workspace_round_trip(guard: smoke.Guard, sample: Path) -> None:
    log("\n## Workspace round trip")
    run("Load the sample repository into the sandbox", [PY, "aegis.py", "workspace", "load", str(sample)])
    s = guard.session()
    ok, text = guard.call(s, "code__list_files")
    smoke.check("sandbox lists the loaded files", ok and "calc.py" in text, text)
    ok, text = guard.call(s, "code__run_check", check="pytest")
    smoke.check("the failing test fails in the sandbox", not ok and "exit code 1" in text, text[:400])
    log("    " + text.replace("\n", "\n    ")[:1200])
    fix = "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
    ok, text = guard.call(s, "code__apply_patch", diff=fix)
    smoke.check("the fix applies in the sandbox", ok, text)
    ok, text = guard.call(s, "code__run_check", check="pytest")
    smoke.check("the test passes after the fix", ok and "exit code 0" in text, text[:400])
    ok, text = guard.call(s, "code__run_check", check="ruff")
    smoke.check("ruff runs in the sandbox", "exit code" in text, text[:300])
    output = run("Export the sandbox's changes", [PY, "aegis.py", "workspace", "export"])
    match = re.search(r"wrote (.+\.patch)", output)
    if match:
        patch = Path(match.group(1).strip())
        log(patch.read_text(encoding="utf-8"))
        run(
            "The exported patch applies to the original checkout",
            ["git", "-C", str(sample), "apply", "--check", str(patch)],
        )
        patch.unlink()
    else:
        smoke.check("export wrote a patch", False, output)


def cli_flows(guard: smoke.Guard) -> None:
    log("\n## Approvals and breaker through the launcher")
    s = guard.session()
    guard.call(s, "github__get_issue", number=7)
    new_file = "--- /dev/null\n+++ b/NOTES.md\n@@ -0,0 +1 @@\n+reviewed\n"
    ok, text = guard.call(s, "code__apply_patch", diff=new_file)
    smoke.check("tainted session's patch is held", not ok and "Held for approval" in text, text)
    approval = re.search(r"approval \(id ([0-9a-f]+)\)", text)
    run("List what is waiting", [PY, "aegis.py", "pending"])
    if approval:
        run("Approve it", [PY, "aegis.py", "approve", approval.group(1)])
        ok, text = guard.call(s, "code__apply_patch", diff=new_file)
        smoke.check("the approved patch goes through once", ok, text)
    other = "--- /dev/null\n+++ b/OTHER.md\n@@ -0,0 +1 @@\n+no\n"
    ok, text = guard.call(s, "code__apply_patch", diff=other)
    denied = re.search(r"approval \(id ([0-9a-f]+)\)", text)
    if denied:
        run("Deny a different held call", [PY, "aegis.py", "deny", denied.group(1)])
        ok, text = guard.call(s, "code__apply_patch", diff=other)
        smoke.check("a denied call stays held", not ok and "Held for approval" in text, text)

    b = guard.session()
    guard.call(b, "math__solve", equations=["x +* 2 = 0"], unknowns=["x"])
    ok, text = guard.call(b, "math__solve", equations=["x +* 2 = 0"], unknowns=["x"])
    smoke.check("breaker trips on a repeated failing call", "Circuit breaker tripped" in text, text)
    status = run("Status shows the tripped session", [PY, "aegis.py", "status"])
    smoke.check("status reports the trip", '"rule": "retry"' in status or '"tripped": {' in status, "")
    run("Reset the breaker", [PY, "aegis.py", "reset", b])
    ok, text = guard.call(b, "math__simplify", expression="sin(x)^2 + cos(x)^2")
    smoke.check("the session works again after reset", ok, text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sample-repo", required=True)
    parser.add_argument("--schema-runs", type=int, default=100)
    parser.add_argument("--reasoner-schema-runs", type=int, default=100)
    args = parser.parse_args()
    smoke.LIVE = True

    cfg = aegis.settings()
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    log(f"# Walkthrough ({args.label}) on profile {args.profile}, commit {commit}")
    log(f"llama-server: {cfg.get('AEGIS_LLAMA_SERVER')}")
    log(f"tier0 model: {cfg.get('AEGIS_TIER0_MODEL')}\ntier1 model: {cfg.get('AEGIS_TIER1_MODEL')}")
    try:
        run("Doctor", [PY, "aegis.py", "doctor", "--profile", args.profile])
        run("Start the tiers and the stack", [PY, "aegis.py", "up", "--profile", args.profile, "--no-build"])
        run("Status", [PY, "aegis.py", "status"])

        tokens = aegis.read_env_file(aegis.TOKENS)
        compose = ["docker", "compose", "-f", "docker-compose.yml", "-f", "tests/smoke/compose.fixtures.yml"]
        profile = aegis.load_profile(args.profile)
        env = {
            **tokens,
            **{
                f"AEGIS_{t.upper()}_URL": f"http://host.docker.internal:{profile['tiers'][t]['port']}/v1"
                for t in aegis.TIERS
            },
        }
        run("Add the untrusted GitHub fixture (smoke overlay)", [*compose, "up", "-d", "--no-build", "--wait"], env=env)
        os.environ.update(env)

        log("\n## Smoke test against the live model")
        smoke.print = lambda *a, **k: log(" ".join(str(x) for x in a))  # route smoke output into the transcript
        guard = smoke.Guard(aegis.GUARD, tokens["AEGIS_CLIENT_TOKEN"], tokens["AEGIS_ADMIN_TOKEN"])
        smoke.check_router(aegis.ROUTER, True, args.schema_runs)
        if args.reasoner_schema_runs:
            smoke.check_reasoner_schema(aegis.ROUTER, args.reasoner_schema_runs)
        smoke.check_guard(guard)
        smoke.check_isolation(compose)

        tier_speeds()
        workspace_round_trip(guard, Path(args.sample_repo).resolve())
        cli_flows(guard)
        listening()
    finally:
        run(
            "Remove the stack and its volumes",
            ["docker", "compose", "-f", "docker-compose.yml", "-f", "tests/smoke/compose.fixtures.yml", "down", "-v"],
            check_ok=False,
        )
        run("Stop everything", [PY, "aegis.py", "down"])
        time.sleep(3)
        still = [p for p in (4000, 4100, 8080, 8081) if aegis.port_in_use("127.0.0.1", p)]
        smoke.check("all ports closed after down", not still, str(still))

    log(
        f"\n{len(smoke.FAILURES)} failure(s): {smoke.FAILURES}" if smoke.FAILURES else "\nall walkthrough checks passed"
    )
    Path(args.out).write_text("\n".join(OUT) + "\n", encoding="utf-8")
    return 1 if smoke.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
