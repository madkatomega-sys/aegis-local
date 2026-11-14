#!/usr/bin/env python3
"""Aegis-Local launcher for Windows 11 and Linux.

Standard library only, so a fresh machine needs nothing but Python 3.11+ to run it.

    python aegis.py doctor                      check the machine and configuration
    python aegis.py profiles                    list hardware profiles
    python aegis.py up [--profile NAME] [--pull]
                                                start llama-server tiers and the container stack
                                                (--pull: use the published, signed images)
    python aegis.py down                        stop everything
    python aegis.py status                      show tiers, guard sessions, pending approvals
    python aegis.py pending                     list calls held for approval
    python aegis.py approve ID | deny ID        decide a held call
    python aegis.py reset SESSION               clear a tripped circuit breaker
    python aegis.py workspace load PATH         copy a repository's HEAD into the sandbox
    python aegis.py workspace export            write the sandbox's changes to exports/*.patch
    python aegis.py opencode [PROJECT]          start OpenCode wired to the harness
    python aegis.py env                         print shell lines that export the client token
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
REGISTRY = "ghcr.io/madkatomega-sys/aegis-local"
ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".aegis"
RUN = STATE / "run"
LOGS = STATE / "logs"
TOKENS = STATE / "tokens.env"
IMAGES = STATE / "images.env"
PROFILES = ROOT / "config" / "profiles"
COMPOSE_FILE = ROOT / "docker-compose.yml"
ROUTER = "http://127.0.0.1:4000"
GUARD = "http://127.0.0.1:4100"
TIERS = ("tier0", "tier1")
BACKENDS = {"cuda", "hip", "rocm", "sycl", "vulkan", "metal", "cpu"}
IS_WINDOWS = os.name == "nt"


# -- small utilities -------------------------------------------------------------------------


def say(status: str, message: str) -> None:
    print(f"  [{status:^4}] {message}")


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def settings() -> dict[str, str]:
    """.env values, then generated tokens, then the real environment (which wins)."""
    merged = read_env_file(ROOT / ".env")
    merged.update({k: v for k, v in read_env_file(TOKENS).items() if v})
    merged.update({k: v for k, v in os.environ.items() if k.startswith(("AEGIS_", "GITHUB_PAT"))})
    return merged


def ensure_tokens() -> dict[str, str]:
    existing = read_env_file(TOKENS)
    if existing.get("AEGIS_CLIENT_TOKEN") and existing.get("AEGIS_ADMIN_TOKEN"):
        return existing
    STATE.mkdir(parents=True, exist_ok=True)
    tokens = {
        "AEGIS_CLIENT_TOKEN": secrets.token_urlsafe(32),
        "AEGIS_ADMIN_TOKEN": secrets.token_urlsafe(32),
    }
    TOKENS.write_text("".join(f"{k}={v}\n" for k, v in tokens.items()), encoding="utf-8")
    if not IS_WINDOWS:
        TOKENS.chmod(0o600)
    return tokens


def http_json(url: str, *, method: str = "GET", token: str | None = None, timeout: float = 5.0) -> Any:
    request = urllib.request.Request(url, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "null")


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def os_key() -> str:
    return "windows" if IS_WINDOWS else "linux"


# -- profiles --------------------------------------------------------------------------------


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES.glob("*.toml"))


def load_profile(name: str) -> dict[str, Any]:
    path = PROFILES / f"{name}.toml"
    if not path.is_file():
        die(f"unknown profile {name!r}; available: {', '.join(list_profiles())}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def validate_profile(profile: dict[str, Any]) -> list[str]:
    """Return a list of problems; empty means the profile is structurally valid."""
    problems = []
    for section in ("profile", "backend", "tiers"):
        if section not in profile:
            problems.append(f"missing [{section}]")
    if problems:
        return problems
    for key in ("windows", "linux"):
        backend = profile["backend"].get(key)
        if backend not in BACKENDS:
            problems.append(f"backend.{key} must be one of {sorted(BACKENDS)}, got {backend!r}")
    ports = set()
    for tier in TIERS:
        spec = profile["tiers"].get(tier)
        if spec is None:
            problems.append(f"missing [tiers.{tier}]")
            continue
        for field, kind in (("ctx", int), ("gpu_layers", int), ("port", int), ("model", str), ("extra_args", list)):
            if not isinstance(spec.get(field), kind):
                problems.append(f"tiers.{tier}.{field} must be {kind.__name__}")
        port = spec.get("port")
        if port in ports:
            problems.append(f"tiers.{tier}.port {port} is used twice")
        ports.add(port)
        if port in (4000, 4100):
            problems.append(f"tiers.{tier}.port {port} collides with the router or guard")
    return problems


def model_path(cfg: dict[str, str], profile: dict[str, Any], tier: str) -> str:
    return cfg.get(f"AEGIS_{tier.upper()}_MODEL") or profile["tiers"][tier].get("model", "")


# -- container runtime -----------------------------------------------------------------------


def detect_runtime(cfg: dict[str, str]) -> str | None:
    wanted = cfg.get("AEGIS_RUNTIME", "auto") or "auto"
    candidates = ["docker", "podman"] if wanted == "auto" else [wanted]
    for name in candidates:
        if shutil.which(name):
            probe = subprocess.run([name, "compose", "version"], capture_output=True, text=True, check=False)
            if probe.returncode == 0:
                return name
    return None


def image_settings(pull: bool) -> dict[str, str]:
    """Image names for Compose: published release images, or ones built from this checkout."""
    return {"AEGIS_REGISTRY": REGISTRY, "AEGIS_VERSION": VERSION} if pull else {}


def compose(runtime: str, *args: str, env: dict[str, str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run compose with the image choice recorded by the last `up`, so later commands use the same images."""
    cmd = [runtime, "compose", "-f", str(COMPOSE_FILE), *args]
    full = {**os.environ, **read_env_file(IMAGES), **env}
    return subprocess.run(cmd, env=full, cwd=ROOT, text=True, capture_output=capture, check=False)


def llama_bind_host(cfg: dict[str, str], runtime: str | None) -> str:
    if cfg.get("AEGIS_LLAMA_HOST"):
        return cfg["AEGIS_LLAMA_HOST"]
    if IS_WINDOWS or platform.system() == "Darwin":
        return "127.0.0.1"
    # Linux: containers reach the host through the bridge gateway, not loopback. Binding there
    # keeps llama-server reachable from the host and containers but not from the LAN.
    if runtime == "docker":
        probe = subprocess.run(
            ["docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        gateway = probe.stdout.strip()
        if probe.returncode == 0 and gateway:
            return gateway
    print("warning: could not determine the container bridge address; binding llama-server to 127.0.0.1.")
    print("         Containers may not reach it. Set AEGIS_LLAMA_HOST in .env (see docs/PLATFORMS.md).")
    return "127.0.0.1"


# -- llama-server processes ------------------------------------------------------------------


def llama_binary(cfg: dict[str, str]) -> str | None:
    explicit = cfg.get("AEGIS_LLAMA_SERVER")
    if explicit:
        return explicit if Path(explicit).is_file() else None
    return shutil.which("llama-server")


def pid_alive(pid: int) -> bool:
    if IS_WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def start_tier(binary: str, tier: str, spec: dict[str, Any], model: str, host: str) -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    args = [
        binary,
        "-m",
        model,
        "-c",
        str(spec["ctx"]),
        "-ngl",
        str(spec["gpu_layers"]),
        "--host",
        host,
        "--port",
        str(spec["port"]),
        *[str(a) for a in spec.get("extra_args", [])],
    ]
    log = (LOGS / f"{tier}.log").open("ab")
    kwargs: dict[str, Any] = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x8)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)
    (RUN / f"{tier}.pid").write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def wait_healthy(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(2)
    return False


def stop_tiers() -> None:
    for pid_file in sorted(RUN.glob("*.pid")) if RUN.is_dir() else []:
        pid = int(pid_file.read_text(encoding="utf-8").strip() or 0)
        if pid and pid_alive(pid):
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
            else:
                os.kill(pid, signal.SIGTERM)
            print(f"stopped {pid_file.stem} (pid {pid})")
        pid_file.unlink(missing_ok=True)


# -- commands --------------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = settings()
    failures = 0
    print(f"Aegis-Local doctor ({platform.system()} {platform.release()}, Python {platform.python_version()})")

    ok = sys.version_info >= (3, 11)
    say("ok" if ok else "FAIL", f"Python {platform.python_version()} (3.11+ required)")
    failures += not ok

    say(
        "ok" if (ROOT / ".env").is_file() else "warn",
        ".env present" if (ROOT / ".env").is_file() else "no .env; copy .env.example to .env",
    )

    runtime = detect_runtime(cfg)
    say("ok" if runtime else "FAIL", f"container runtime: {runtime or 'docker or podman with compose not found'}")
    failures += runtime is None

    binary = llama_binary(cfg)
    say("ok" if binary else "FAIL", f"llama-server: {binary or 'not found (set AEGIS_LLAMA_SERVER or add it to PATH)'}")
    failures += binary is None

    name = args.profile or cfg.get("AEGIS_PROFILE") or "cpu-only"
    profile = load_profile(name)
    problems = validate_profile(profile)
    say(
        "ok" if not problems else "FAIL",
        f"profile {name}: backend {profile.get('backend', {}).get(os_key(), '?')} on {os_key()}",
    )
    for problem in problems:
        say("", problem)
    failures += bool(problems)

    if not problems:
        for tier in TIERS:
            model = model_path(cfg, profile, tier)
            exists = bool(model) and Path(model).is_file()
            label = model or f"not set (AEGIS_{tier.upper()}_MODEL)"
            say("ok" if exists else "FAIL", f"{tier} model: {label}")
            failures += not exists
            port = profile["tiers"][tier]["port"]
            if port_in_use("127.0.0.1", port):
                say("warn", f"port {port} for {tier} is already in use")

    for port, what in ((4000, "router"), (4100, "guard")):
        if port_in_use("127.0.0.1", port):
            say("warn", f"port {port} ({what}) is in use (fine if the stack is already up)")

    say(
        "ok" if TOKENS.is_file() else "info",
        "guard tokens generated" if TOKENS.is_file() else "guard tokens will be generated on first `up`",
    )
    print("ready" if failures == 0 else f"{failures} problem(s) found")
    return 0 if failures == 0 else 1


def cmd_profiles(args: argparse.Namespace) -> int:
    bad = 0
    print(f"{'profile':<20} {'vendor':<8} {'vram':>5}  {'backend (' + os_key() + ')':<18} tested")
    for name in list_profiles():
        profile = load_profile(name)
        problems = validate_profile(profile)
        bad += bool(problems)
        meta = profile.get("profile", {})
        backend = profile.get("backend", {}).get(os_key(), "?")
        flag = "yes" if meta.get("tested") else "no"
        print(f"{name:<20} {meta.get('vendor', '?'):<8} {meta.get('vram_gb', '?'):>5}  {backend:<18} {flag}")
        for problem in problems:
            print(f"    invalid: {problem}")
    return 1 if bad else 0


def cmd_up(args: argparse.Namespace) -> int:
    cfg = settings()
    tokens = ensure_tokens()
    runtime = detect_runtime(cfg)
    if runtime is None:
        die("no container runtime with compose support found (Docker or Podman)")
    name = args.profile or cfg.get("AEGIS_PROFILE") or "cpu-only"
    profile = load_profile(name)
    if problems := validate_profile(profile):
        die(f"profile {name} is invalid: " + "; ".join(problems))

    host = llama_bind_host(cfg, runtime)
    env = {**tokens}
    for key in ("AEGIS_VAULT_PATH", "AEGIS_GITHUB_ENABLED", "GITHUB_PAT"):
        if cfg.get(key):
            env[key] = cfg[key]
    for tier in TIERS:
        env[f"AEGIS_{tier.upper()}_URL"] = f"http://host.docker.internal:{profile['tiers'][tier]['port']}/v1"

    if not args.no_inference:
        binary = llama_binary(cfg)
        if binary is None:
            die("llama-server not found; set AEGIS_LLAMA_SERVER in .env")
        for tier in TIERS:
            spec = profile["tiers"][tier]
            model = model_path(cfg, profile, tier)
            if not model or not Path(model).is_file():
                die(f"{tier} model file not found: {model or '(unset)'}; set AEGIS_{tier.upper()}_MODEL in .env")
            if port_in_use(host, spec["port"]):
                print(f"{tier}: something is already listening on {host}:{spec['port']}; leaving it")
                continue
            pid = start_tier(binary, tier, spec, model, host)
            print(f"{tier}: starting llama-server (pid {pid}) on {host}:{spec['port']}; log in .aegis/logs/{tier}.log")
        for tier in TIERS:
            port = profile["tiers"][tier]["port"]
            if not wait_healthy(host, port, args.timeout):
                die(f"{tier} did not become healthy within {args.timeout:.0f}s; see .aegis/logs/{tier}.log")
            print(f"{tier}: healthy")

    images = image_settings(args.pull)
    STATE.mkdir(parents=True, exist_ok=True)
    IMAGES.write_text("".join(f"{k}={v}\n" for k, v in images.items()), encoding="utf-8")
    env.update(images)
    if args.pull:
        print(f"using published images {REGISTRY}/*:{VERSION} (verify them with cosign; see SECURITY.md)")
        if compose(runtime, "pull", "--ignore-buildable", env=env).returncode != 0:
            die("could not pull the release images")
        up_args = ["up", "-d", "--no-build"]
    else:
        up_args = ["up", "-d"] + ([] if args.no_build else ["--build"])
    if compose(runtime, *up_args, env=env).returncode != 0:
        die("container stack failed to start")
    print(f"router: {ROUTER}/v1    guard: {GUARD}/mcp")
    print("next: python aegis.py opencode   (or run `python aegis.py env` and start opencode yourself)")
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    cfg = settings()
    runtime = detect_runtime(cfg)
    if runtime:
        compose(runtime, "down", env=read_env_file(TOKENS))
    stop_tiers()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = settings()
    for pid_file in sorted(RUN.glob("*.pid")) if RUN.is_dir() else []:
        pid = int(pid_file.read_text(encoding="utf-8").strip() or 0)
        print(f"{pid_file.stem}: pid {pid} {'running' if pid_alive(pid) else 'not running'}")
    for label, url, token in (
        ("router", f"{ROUTER}/status", None),
        ("guard", f"{GUARD}/status", cfg.get("AEGIS_ADMIN_TOKEN")),
    ):
        try:
            print(f"{label}: " + json.dumps(http_json(url, token=token), indent=2))
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            print(f"{label}: unreachable ({exc})")
    return 0


def _admin(path: str, method: str = "GET") -> Any:
    token = settings().get("AEGIS_ADMIN_TOKEN")
    if not token:
        die("no admin token; has the stack been started with `python aegis.py up`?")
    try:
        return http_json(f"{GUARD}{path}", method=method, token=token)
    except urllib.error.HTTPError as exc:
        die(f"guard returned HTTP {exc.code} for {path}")
    except urllib.error.URLError as exc:
        die(f"guard unreachable: {exc}")


def cmd_pending(args: argparse.Namespace) -> int:
    pending = _admin("/admin/approvals")["pending"]
    if not pending:
        print("nothing is waiting for approval")
    for item in pending:
        print(f"[{item['id']}] {item['tool']}  (session {item['session'][:8]})")
        print(f"    why held: {item['reason']}")
        if item["taint_sources"]:
            print(f"    session read untrusted content from: {', '.join(item['taint_sources'])}")
        print("    arguments: " + json.dumps(item["arguments"], indent=2).replace("\n", "\n    "))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    verdict = "approve" if args.command == "approve" else "deny"
    result = _admin(f"/admin/approvals/{args.id}/{verdict}", method="POST")
    print(f"{args.id}: {verdict}d" if result.get("updated") else f"{args.id}: not pending")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    _admin(f"/admin/sessions/{args.session}/reset", method="POST")
    print(f"session {args.session}: breaker reset (taint is kept)")
    return 0


def cmd_workspace(args: argparse.Namespace) -> int:
    cfg = settings()
    runtime = detect_runtime(cfg) or die("no container runtime found")
    env = read_env_file(TOKENS)
    if args.action == "load":
        source = Path(args.path).resolve()
        if not (source / ".git").exists():
            die(f"{source} is not a Git repository")
        script = "rm -rf /workspace/repo && git -c safe.directory='*' clone --quiet --no-hardlinks /src /workspace/repo"
        result = compose(
            runtime,
            "run",
            "--rm",
            "--no-deps",
            "-v",
            f"{source.as_posix()}:/src:ro",
            "--entrypoint",
            "sh",
            "code-worker",
            "-c",
            script,
            env=env,
        )
        if result.returncode == 0:
            print(f"loaded the committed HEAD of {source} into the sandbox (uncommitted changes are not copied)")
        return result.returncode
    script = "cd /workspace/repo && git add -A --intent-to-add >/dev/null 2>&1; git diff HEAD --binary"
    result = compose(
        runtime, "run", "--rm", "--no-deps", "--entrypoint", "sh", "code-worker", "-c", script, env=env, capture=True
    )
    if result.returncode != 0:
        die(result.stderr.strip() or "export failed")
    if not result.stdout.strip():
        print("no changes in the sandbox")
        return 0
    out_dir = ROOT / "exports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"aegis-{time.strftime('%Y%m%d-%H%M%S')}.patch"
    out.write_text(result.stdout, encoding="utf-8", newline="\n")
    print(f"wrote {out}\nreview it, then: git -C <your repo> apply {out}")
    return 0


def cmd_opencode(args: argparse.Namespace) -> int:
    binary = shutil.which("opencode") or die("opencode not found; install it with `npm install -g opencode-ai`")
    token = ensure_tokens()["AEGIS_CLIENT_TOKEN"]
    env = {**os.environ, "OPENCODE_CONFIG": str(ROOT / "config" / "opencode.json"), "AEGIS_CLIENT_TOKEN": token}
    return subprocess.call([binary, *([args.project] if args.project else [])], env=env)


def cmd_env(args: argparse.Namespace) -> int:
    token = ensure_tokens()["AEGIS_CLIENT_TOKEN"]
    config = ROOT / "config" / "opencode.json"
    print("# PowerShell")
    print(f'$env:AEGIS_CLIENT_TOKEN = "{token}"; $env:OPENCODE_CONFIG = "{config}"')
    print("# bash / zsh")
    print(f"export AEGIS_CLIENT_TOKEN='{token}' OPENCODE_CONFIG='{config.as_posix()}'")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aegis", description="Aegis-Local launcher")
    parser.add_argument("--version", action="version", version=f"aegis-local {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check the machine and configuration")
    p.add_argument("--profile")
    p.set_defaults(func=cmd_doctor)

    sub.add_parser("profiles", help="list hardware profiles").set_defaults(func=cmd_profiles)

    p = sub.add_parser("up", help="start inference tiers and the container stack")
    p.add_argument("--profile")
    p.add_argument("--no-inference", action="store_true", help="don't start llama-server (use one already running)")
    p.add_argument("--no-build", action="store_true", help="don't rebuild container images")
    p.add_argument("--pull", action="store_true", help=f"use the published {VERSION} images instead of building")
    p.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for each tier to load")
    p.set_defaults(func=cmd_up)

    sub.add_parser("down", help="stop everything").set_defaults(func=cmd_down)
    sub.add_parser("status", help="show tiers, sessions, and approvals").set_defaults(func=cmd_status)
    sub.add_parser("pending", help="list calls held for approval").set_defaults(func=cmd_pending)
    for verb in ("approve", "deny"):
        p = sub.add_parser(verb, help=f"{verb} a held call")
        p.add_argument("id")
        p.set_defaults(func=cmd_decide)

    p = sub.add_parser("reset", help="clear a tripped circuit breaker for a session")
    p.add_argument("session")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("workspace", help="move code into and out of the sandbox")
    ws = p.add_subparsers(dest="action", required=True)
    load = ws.add_parser("load", help="copy a repository's committed HEAD into the sandbox")
    load.add_argument("path")
    ws.add_parser("export", help="write sandbox changes to exports/*.patch")
    p.set_defaults(func=cmd_workspace)

    p = sub.add_parser("opencode", help="start OpenCode wired to the harness")
    p.add_argument("project", nargs="?")
    p.set_defaults(func=cmd_opencode)

    sub.add_parser("env", help="print shell lines exporting the client token").set_defaults(func=cmd_env)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
