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
    python aegis.py workspace prepare [--requirements FILE] [--allow-source-builds]
                                                install the loaded project's dependencies (wheels only)
    python aegis.py workspace export            write the sandbox's changes to exports/*.patch
    python aegis.py opencode [PROJECT]          start OpenCode wired to the harness
    python aegis.py opencode --web [PROJECT]    the same in OpenCode's browser interface (OpenCode 1.x)
    python aegis.py env                         print shell lines that export the client token
    python aegis.py models suggest              detect the GPU and RAM; suggest a profile and its models
    python aegis.py models get [--profile NAME] download the profile's models, check SHA-256, set .env
    python aegis.py models list | verify        list the pinned model files; check the ones in .env
"""

from __future__ import annotations

import argparse
import hashlib
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

VERSION = "0.2.0"
REGISTRY = "ghcr.io/madkatomega-sys/aegis-local"
ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".aegis"
RUN = STATE / "run"
LOGS = STATE / "logs"
TOKENS = STATE / "tokens.env"
IMAGES = STATE / "images.env"
PROFILES = ROOT / "config" / "profiles"
MODELS_FILE = ROOT / "config" / "models.toml"
MODELS_DIR = ROOT / "models"  # ignored by Git
HUGGING_FACE = "https://huggingface.co"
COMPOSE_FILE = ROOT / "docker-compose.yml"
ROUTER = "http://127.0.0.1:4000"
GUARD = "http://127.0.0.1:4100"
TIERS = ("tier0", "tier1")
BACKENDS = {"cuda", "hip", "rocm", "sycl", "vulkan", "metal", "cpu"}
# The mounted checkout belongs to a host user, not the worker's uid, so Git calls it "dubious
# ownership". Neither `git -c` nor GIT_CONFIG_* reach the upload-pack process a local clone spawns
# (Git strips them), so this writes the worker's global config, which lives in its private /tmp
# HOME and disappears with the container. The mount is read-only, so trusting it is safe.
SAFE_DIRECTORY = "git config --global safe.directory '*'"
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


def ensure_web_password() -> str:
    """The password for OpenCode's browser interface, kept beside the guard tokens and reused."""
    tokens = ensure_tokens()
    if tokens.get("OPENCODE_SERVER_PASSWORD"):
        return tokens["OPENCODE_SERVER_PASSWORD"]
    password = secrets.token_urlsafe(18)
    with TOKENS.open("a", encoding="utf-8") as sink:
        sink.write(f"OPENCODE_SERVER_PASSWORD={password}\n")
    return password


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


# -- models: detect the hardware, suggest a profile, fetch its model files --------------------


def load_catalog() -> dict[str, dict[str, Any]]:
    """config/models.toml: model files pinned by size and SHA-256."""
    return tomllib.loads(MODELS_FILE.read_text(encoding="utf-8"))["models"]


def gpu_vendor(name: str) -> str | None:
    lowered = name.lower()
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered or "rtx" in lowered:
        return "nvidia"
    if "amd" in lowered or "radeon" in lowered:
        return "amd"
    if "intel" in lowered and "arc" in lowered:
        return "intel"
    return None  # integrated Intel graphics and anything unknown: not a profile target


def parse_nvidia_smi(text: str) -> list[dict[str, Any]]:
    """`nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` lines: name, MiB."""
    gpus = []
    for line in text.strip().splitlines():
        name, _, memory = line.rpartition(",")
        if name.strip() and memory.strip().isdigit():
            gpus.append({"vendor": "nvidia", "name": name.strip(), "memory_gb": round(int(memory) / 1024, 1)})
    return gpus


def parse_adapters(text: str) -> list[dict[str, Any]]:
    """`name|bytes` lines (Windows display-adapter registry, Linux sysfs): any vendor, memory if known."""
    gpus = []
    for line in text.strip().splitlines():
        name, _, size = line.partition("|")
        vendor = gpu_vendor(name)
        if vendor:
            memory = round(int(size) / 2**30, 1) if size.strip().isdigit() and int(size) > 0 else None
            gpus.append({"vendor": vendor, "name": name.strip(), "memory_gb": memory})
    return gpus


def detect_gpus() -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    if shutil.which("nvidia-smi"):
        query = ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
        gpus += parse_nvidia_smi(subprocess.run(query, capture_output=True, text=True, check=False).stdout)
    if IS_WINDOWS:
        # The driver records each adapter's dedicated memory in the registry; WMI's AdapterRAM caps at 4 GB.
        script = (
            "Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\"
            "{4d36e968-e325-11ce-bfc1-08002be10318}\\0*' -ErrorAction SilentlyContinue | "
            "ForEach-Object { \"$($_.DriverDesc)|$($_.'HardwareInformation.qwMemorySize')\" }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, check=False
        ).stdout
    else:
        lines = []
        for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
            vram = card / "device" / "mem_info_vram_total"  # amdgpu
            label = card / "device" / "label"
            name = label.read_text().strip() if label.is_file() else ""
            if not name and shutil.which("lspci"):
                slot = (card / "device").resolve().name
                name = subprocess.run(["lspci", "-s", slot], capture_output=True, text=True, check=False).stdout
            lines.append(f"{name.strip()}|{vram.read_text().strip() if vram.is_file() else ''}")
        out = "\n".join(lines)
    seen = {g["name"] for g in gpus}  # nvidia-smi's reading wins over the registry's for the same card
    return gpus + [g for g in parse_adapters(out) if g["name"] not in seen]


def system_ram_gb() -> float:
    if IS_WINDOWS:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (n, ctypes.c_ulonglong) for n in ("total", "avail", "page_total", "page_avail", "virt_total",
                                                   "virt_avail", "ext")
            ]  # fmt: skip

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
        return round(status.total / 2**30, 1)
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return round(int(line.split()[1]) / 2**20, 1)
    return 0.0


def suggest_profile(gpus: list[dict[str, Any]]) -> tuple[str, str]:
    """The profile for the strongest supported GPU, and why. Memory thresholds sit between profile sizes."""
    ranked = sorted(gpus, key=lambda g: g["memory_gb"] or 0, reverse=True)
    for gpu in ranked:
        memory, vendor, name = gpu["memory_gb"], gpu["vendor"], gpu["name"]
        if memory is None:
            return "cpu-only", f"found {name}, but not how much memory it has; pick its profile by hand"
        if vendor == "nvidia":
            for limit, profile in ((7, None), (10, "nvidia-8gb"), (14, "nvidia-12gb"), (20, "nvidia-16gb")):
                if memory < limit:
                    if profile is None:
                        return "cpu-only", f"{name} has {memory} GB, less than the smallest GPU profile"
                    return profile, f"{name}, {memory} GB"
            return "nvidia-24gb-plus", f"{name}, {memory} GB"
        if vendor == "amd":
            if memory >= 48:
                return "amd-unified-128gb", f"{name}, {memory} GB available to the GPU"
            if memory >= 20:
                return "amd-24gb", f"{name}, {memory} GB"
            if memory >= 14:
                return "amd-16gb", f"{name}, {memory} GB"
        if vendor == "intel":
            if memory >= 14:
                return "intel-arc-16gb", f"{name}, {memory} GB"
            if memory >= 10:
                return "intel-arc-12gb", f"{name}, {memory} GB"
    if ranked:
        return "cpu-only", "no supported GPU with enough memory for a GPU profile"
    return "cpu-only", "no supported GPU found"


def model_plan(profile: dict[str, Any], catalog: dict[str, dict[str, Any]], overrides: dict[str, str]) -> dict:
    """tier -> catalog entry id, from the profile's `download` keys and any --tier0/--tier1 overrides."""
    plan = {}
    for tier in TIERS:
        choice = overrides.get(tier) or profile["tiers"][tier].get("download")
        if not choice:
            die(f"profile {profile['profile']['name']} names no download for {tier}; pass --{tier} <id>")
        if choice not in catalog:
            die(f"{choice!r} is not in config/models.toml (see `python aegis.py models list`)")
        plan[tier] = choice
    return plan


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(entry: dict[str, Any], dest: Path, base_url: str = HUGGING_FACE) -> None:
    """Fetch one catalog file to `dest`, resuming a partial download, and refuse it unless size and SHA-256 match."""
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    have = 0
    if part.exists():
        with part.open("rb") as source:
            for chunk in iter(lambda: source.read(1 << 22), b""):
                digest.update(chunk)
                have += len(chunk)
    url = f"{base_url}/{entry['repo']}/resolve/main/{entry['path']}"
    headers = {"User-Agent": f"aegis-local/{VERSION}"}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    started, shown = time.monotonic(), 0.0
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (https URL built from the catalog)
        if have and response.status != 206:  # the server ignored the range: start over
            digest, have = hashlib.sha256(), 0
        with part.open("ab" if have else "wb") as sink:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                sink.write(chunk)
                digest.update(chunk)
                have += len(chunk)
                now = time.monotonic()
                if now - shown >= 2 or have == entry["size"]:
                    shown = now
                    rate = have / max(now - started, 0.001) / 2**20
                    print(
                        f"\r    {have / 1e9:6.2f} of {entry['size'] / 1e9:.2f} GB  {rate:6.1f} MB/s",
                        end="",
                        flush=True,
                    )
    print()
    if have != entry["size"] or digest.hexdigest() != entry["sha256"]:
        part.unlink(missing_ok=True)
        die(f"{entry['path']}: the download doesn't match config/models.toml (size or SHA-256); nothing was kept")
    part.replace(dest)


def set_env_values(path: Path, values: dict[str, str]) -> None:
    """Set KEY=value lines in a .env file, keeping every other line (and comment) as it is."""
    text = path.read_bytes().decode("utf-8") if path.is_file() else ""
    newline = "\r\n" if "\r\n" in text else "\n"  # keep the file's own line endings
    lines = text.splitlines()
    pending = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in pending and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{key}={value}" for key, value in pending.items()]
    path.write_bytes((newline.join(lines) + newline).encode("utf-8"))


def cmd_models(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    if args.action == "list":
        print(f"{'id':26} {'size':>9}  {'license':11} measured  repository / file")
        for model_id, entry in catalog.items():
            mark = "yes" if entry.get("measured") else "-"
            print(f"{model_id:26} {entry['size'] / 1e9:6.2f} GB  {entry['license']:11} {mark:9} "
                  f"{entry['repo']}/{entry['path']}")  # fmt: skip
        return 0

    cfg = settings()
    if args.action == "verify":
        by_file = {entry["path"]: (model_id, entry) for model_id, entry in catalog.items()}
        bad = 0
        for tier in TIERS:
            configured = cfg.get(f"AEGIS_{tier.upper()}_MODEL", "")
            path = Path(configured) if configured else None
            if path is None or not path.is_file():
                print(f"{tier}: no model file at {configured or '(unset in .env)'}")
                bad += 1
                continue
            known = by_file.get(path.name)
            if known is None:
                print(f"{tier}: {path.name} isn't in config/models.toml, so there is no hash to check it against")
                continue
            ok = path.stat().st_size == known[1]["size"] and sha256_of(path) == known[1]["sha256"]
            print(f"{tier}: {path.name} {'matches' if ok else 'DOES NOT MATCH'} {known[0]}")
            bad += 0 if ok else 1
        return 1 if bad else 0

    gpus, ram = detect_gpus(), system_ram_gb()
    suggested, reason = suggest_profile(gpus)
    if args.action == "suggest":
        for gpu in gpus or [{"name": "none found", "memory_gb": None}]:
            memory = f"{gpu['memory_gb']} GB" if gpu.get("memory_gb") is not None else "memory unknown"
            print(f"GPU: {gpu['name']} ({memory})")
        print(f"RAM: {ram} GB")
        print(f"suggested profile: {suggested} ({reason})")
        profile = load_profile(suggested)
        for tier, model_id in model_plan(profile, catalog, {}).items():
            entry = catalog[model_id]
            print(f"  {tier}: {entry['name']}, {entry['size'] / 1e9:.2f} GB, {entry['license']}"
                  f"{', measured' if entry.get('measured') else ''}")  # fmt: skip
        current = cfg.get("AEGIS_PROFILE")
        if current and current != suggested:
            print(f"(.env currently sets AEGIS_PROFILE={current})")
        print(f"next: python aegis.py models get --profile {suggested}")
        return 0

    # get
    name = args.profile or cfg.get("AEGIS_PROFILE") or suggested
    profile = load_profile(name)
    plan = model_plan(profile, catalog, {"tier0": args.tier0, "tier1": args.tier1})
    dest_dir = Path(args.dir).expanduser().resolve() if args.dir else MODELS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    todo = []
    print(f"profile {name}; files go to {dest_dir}")
    for tier, model_id in plan.items():
        entry = catalog[model_id]
        dest = dest_dir / entry["path"]
        present = dest.is_file() and dest.stat().st_size == entry["size"]
        note = "already there (will be checked)" if present else f"{entry['size'] / 1e9:.2f} GB to download"
        print(f"  {tier}: {entry['name']} from huggingface.co/{entry['repo']} ({entry['license']}), {note}")
        todo.append((tier, entry, dest, present))
    needed = sum(entry["size"] for _, entry, _, present in todo if not present)
    free = shutil.disk_usage(dest_dir).free
    if needed > free:
        die(f"needs {needed / 1e9:.1f} GB, but {dest_dir} has {free / 1e9:.1f} GB free")
    if needed and not args.yes:
        try:
            answer = input(f"download {needed / 1e9:.1f} GB? [y/N] ").strip().lower()
        except EOFError:  # no terminal to ask on: treat as "no"
            answer = ""
        if answer not in {"y", "yes"}:
            print("nothing downloaded")
            return 1
    for _tier, entry, dest, present in todo:
        if present:
            print(f"  checking {dest.name}")
            if sha256_of(dest) == entry["sha256"]:
                continue
            die(f"{dest} has the right size but not the right SHA-256; move it aside and run again")
        print(f"  downloading {dest.name}")
        download_model(entry, dest)
    env_file = ROOT / ".env"
    if not env_file.is_file() and (ROOT / ".env.example").is_file():
        shutil.copyfile(ROOT / ".env.example", env_file)
    values = {f"AEGIS_{tier.upper()}_MODEL": dest.as_posix() for tier, _, dest, _ in todo}
    values["AEGIS_PROFILE"] = name
    set_env_values(env_file, values)
    print("verified, and .env now points at them:")
    for key, value in values.items():
        print(f"  {key}={value}")
    print("next: python aegis.py doctor")
    return 0


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


def compose(
    runtime: str,
    *args: str,
    env: dict[str, str],
    capture: bool = False,
    text: bool = True,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run compose with the image choice recorded by the last `up`, so later commands use the same images.

    With `text=False` captured output stays bytes, so line endings survive (text mode turns CRLF into LF)."""
    cmd = [runtime, "compose", "-f", str(COMPOSE_FILE), *args]
    full = {**os.environ, **read_env_file(IMAGES), **env}
    return subprocess.run(cmd, env=full, cwd=ROOT, text=text, capture_output=capture, input=stdin, check=False)


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
    # --wait returns once every container's health check passes, so the stack is usable on return.
    if compose(runtime, *up_args, "--wait", "--wait-timeout", "180", env=env).returncode != 0:
        die("container stack failed to start or become healthy; see `docker compose logs`")
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
    done = "approved" if verdict == "approve" else "denied"
    print(f"{args.id}: {done}" if result.get("updated") else f"{args.id}: not pending")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    _admin(f"/admin/sessions/{args.session}/reset", method="POST")
    print(f"session {args.session}: breaker reset (taint is kept)")
    return 0


def workspace_load(runtime: str, env: dict[str, str], source: Path) -> int:
    """Replace the sandbox's workspace with a clone of `source`'s committed HEAD. Returns compose's exit code."""
    script = f"{SAFE_DIRECTORY} && rm -rf /workspace/repo && git clone --quiet --no-hardlinks /src /workspace/repo"
    # Compose reads a relative path as a named volume, so the mount needs an absolute one.
    mount = f"{source.resolve().as_posix()}:/src:ro"
    run = ("run", "--rm", "--no-deps", "-v", mount, "--entrypoint", "sh", "code-worker", "-c", script)
    return compose(runtime, *run, env=env).returncode


def workspace_diff(runtime: str, env: dict[str, str]) -> bytes:
    """The sandbox's changes against the loaded commit, as Git wrote them (CRLF lines stay CRLF)."""
    script = (
        f"{SAFE_DIRECTORY} && cd /workspace/repo && git add -A --intent-to-add >/dev/null 2>&1; git diff HEAD --binary"
    )
    run = ("run", "--rm", "--no-deps", "--entrypoint", "sh", "code-worker", "-c", script)
    result = compose(runtime, *run, env=env, capture=True, text=False)
    if result.returncode != 0:
        die(result.stderr.decode("utf-8", errors="replace").strip() or "export failed")
    return result.stdout


def workspace_prepare(
    runtime: str, env: dict[str, str], requirement_files: list[str], allow_source_builds: bool
) -> int:
    """Install the loaded project's declared dependencies where the code worker's checks can use them.

    Two containers, so none has both the repository and the network: the code worker (no network)
    reads the dependency list out of the repository, then workspace-prepare (network, no
    repository) installs that list into the aegis-venv volume, which the worker mounts read-only."""
    collect = ["run", "--rm", "--no-deps", "--entrypoint", "python", "code-worker", "/app/prepare.py", "collect"]
    collect += [f"--requirements={name}" for name in requirement_files]
    found = compose(runtime, *collect, env=env, capture=True)
    if found.returncode != 0:
        print(found.stderr.strip().splitlines()[-1] if found.stderr.strip() else "could not read the workspace")
        return found.returncode
    sources: dict[str, list[str]] = json.loads(found.stdout.strip().splitlines()[-1])
    if not any(sources.values()):
        print("no dependencies declared (looked at requirements*.txt and pyproject.toml); clearing any old ones")
    for name, lines in sources.items():
        print(f"{name}: {', '.join(lines) or '(none)'}")
    mode = "allowing source builds" if allow_source_builds else "wheels only"
    print(f"installing into the sandbox's environment ({mode})")
    install = ["run", "--rm", "--no-deps", "-T", "workspace-prepare", "install"]
    install += ["--allow-source-builds"] if allow_source_builds else []
    return compose(runtime, *install, env=env, stdin=json.dumps(sources)).returncode


def cmd_workspace(args: argparse.Namespace) -> int:
    cfg = settings()
    runtime = detect_runtime(cfg) or die("no container runtime found")
    env = read_env_file(TOKENS)
    if args.action == "load":
        source = Path(args.path).resolve()
        if not (source / ".git").exists():
            die(f"{source} is not a Git repository")
        code = workspace_load(runtime, env, source)
        if code == 0:
            print(f"loaded the committed HEAD of {source} into the sandbox (uncommitted changes are not copied)")
            print("if its tests need packages, run: python aegis.py workspace prepare")
        return code
    if args.action == "prepare":
        return workspace_prepare(runtime, env, args.requirements or [], args.allow_source_builds)
    diff = workspace_diff(runtime, env)
    if not diff.strip():
        print("no changes in the sandbox")
        return 0
    out_dir = ROOT / "exports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"aegis-{time.strftime('%Y%m%d-%H%M%S')}.patch"
    out.write_bytes(diff)
    print(f"wrote {out}\nreview it, then: git -C <your repo> apply {out}")
    return 0


def opencode_env(token: str) -> dict[str, str]:
    """What OpenCode needs to use the harness: the config, the plugin folder, and the client token.

    OPENCODE_CONFIG_DIR loads the plugins in .opencode/plugins, including aegis-chat.ts, which tags
    each guard call with its chat so chats in one OpenCode server don't share guard state."""
    return {
        "OPENCODE_CONFIG": str(ROOT / "config" / "opencode.json"),
        "OPENCODE_CONFIG_DIR": str(ROOT / ".opencode"),
        "AEGIS_CLIENT_TOKEN": token,
    }


def opencode_version(binary: str) -> str:
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False)
    return result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""


def cmd_opencode(args: argparse.Namespace) -> int:
    binary = shutil.which("opencode") or die("opencode not found; install it with `npm install -g opencode-ai@1.19.5`")
    env = {**os.environ, **opencode_env(ensure_tokens()["AEGIS_CLIENT_TOKEN"])}
    if not args.web:
        return subprocess.call([binary, *([args.project] if args.project else [])], env=env)

    # The browser interface is `opencode web`, an OpenCode 1.x command. OpenCode 2.x serves its GUI
    # from a shared background service, which hangs on every turn with this stack (see docs/GUIDE.md).
    version = opencode_version(binary)
    if not version.split(".")[0].isdigit() or int(version.split(".")[0]) != 1:
        die(
            f"--web needs OpenCode 1.x (checked on 1.19.5); found {version or 'an unknown version'}. "
            "Install it with `npm install -g opencode-ai@1.19.5`, "
            "or run `python aegis.py opencode` for the terminal UI."
        )
    project = Path(args.project).resolve() if args.project else Path.cwd()
    if not project.is_dir():
        die(f"{project} is not a folder")
    password = ensure_web_password()
    env["OPENCODE_SERVER_PASSWORD"] = password
    # A private OpenCode home, so the browser interface uses only this harness's config and keeps
    # its sessions apart from any other OpenCode setup on the machine.
    home = STATE / "opencode-web"
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        env[f"XDG_{name}_HOME"] = str(home / name.lower())
    print(f"OpenCode {version} browser interface: http://127.0.0.1:{args.port}/")
    print(f"sign in as user `opencode` with password {password}   (also in .aegis/tokens.env)")
    print(f"project folder: {project}   stop with Ctrl+C")
    command = [binary, "web", "--hostname", "127.0.0.1", "--port", str(args.port)]
    return subprocess.call(command, env=env, cwd=project)


def cmd_env(args: argparse.Namespace) -> int:
    env = opencode_env(ensure_tokens()["AEGIS_CLIENT_TOKEN"])
    print("# PowerShell")
    print("; ".join(f'$env:{k} = "{v}"' for k, v in env.items()))
    print("# bash / zsh")
    posix = {k: (Path(v).as_posix() if k != "AEGIS_CLIENT_TOKEN" else v) for k, v in env.items()}
    print("export " + " ".join(f"{k}='{v}'" for k, v in posix.items()))
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
    prepare = ws.add_parser("prepare", help="install the loaded project's dependencies for the sandbox's checks")
    prepare.add_argument(
        "--requirements",
        action="append",
        metavar="FILE",
        help="a requirements file in the repository (repeatable; default: requirements*.txt and pyproject.toml)",
    )
    prepare.add_argument(
        "--allow-source-builds",
        action="store_true",
        help="let pip build packages that have no wheel (runs their build scripts, with network access)",
    )
    p.set_defaults(func=cmd_workspace)

    p = sub.add_parser("opencode", help="start OpenCode wired to the harness")
    p.add_argument("project", nargs="?")
    p.add_argument("--web", action="store_true", help="open OpenCode's browser interface on 127.0.0.1 (OpenCode 1.x)")
    p.add_argument("--port", type=int, default=4096, help="port for --web (default 4096)")
    p.set_defaults(func=cmd_opencode)

    sub.add_parser("env", help="print shell lines exporting the client token").set_defaults(func=cmd_env)

    p = sub.add_parser("models", help="suggest a profile for this machine and fetch its model files")
    models = p.add_subparsers(dest="action", required=True)
    models.add_parser("suggest", help="detect the GPU and RAM, and suggest a profile and its models")
    models.add_parser("list", help="list the model files in config/models.toml")
    get = models.add_parser("get", help="download the profile's model files, check them, and set .env")
    get.add_argument("--profile", help="profile to fetch for (default: .env's, else the suggested one)")
    get.add_argument("--dir", help=f"where to put the files (default: {MODELS_DIR.name}/ in this checkout)")
    get.add_argument("--tier0", help="a model id from `models list`, instead of the profile's")
    get.add_argument("--tier1", help="a model id from `models list`, instead of the profile's")
    get.add_argument("--yes", action="store_true", help="don't ask before downloading")
    models.add_parser("verify", help="check .env's model files against their SHA-256 in config/models.toml")
    p.set_defaults(func=cmd_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
