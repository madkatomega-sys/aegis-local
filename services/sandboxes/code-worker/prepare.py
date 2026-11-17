"""Install a workspace project's Python dependencies for the code worker. Run by `aegis workspace prepare`.

The code worker has no network, so it can't install anything. Preparing is split in two so that no
container has both the user's code and the internet:

    python prepare.py collect [--requirements FILE ...]
        In the code worker (no network, sees /workspace/repo): read the project's declared
        dependencies and print them as JSON.

    python prepare.py install [--allow-source-builds] < requirements.json
        In the workspace-prepare container (network, never sees the repository): build a virtual
        environment in /venv and install them. The code worker mounts /venv read-only and runs its
        checks with it.

By default only wheels are installed (pip --only-binary :all:), so no package's build script runs.
Only names from the package index are accepted: no pip options, URLs, or local paths, which could
point pip at another index or at files the install container can't see.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(os.environ.get("AEGIS_WORKSPACE", "/workspace/repo"))
VENV = Path(os.environ.get("AEGIS_VENV", "/venv"))
REQUIREMENT_FILES = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "dev-requirements.txt",
    "test-requirements.txt",
)
EXTRAS = ("dev", "test", "tests", "testing")
# A requirement from the package index, optionally followed by --hash options (PEP 508 name,
# extras, version specifiers and markers). Everything pip would read as an option, a URL, or a
# path is refused.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,\s-]+\])?")
HASH = re.compile(r"\s--hash=sha(256|384|512):[0-9a-f]+")
MARKER_FILE = "aegis-prepared.json"


class PrepareError(Exception):
    pass


def check_requirement(line: str) -> str:
    """`line` if it names a package from the index; PrepareError otherwise."""
    text = line.strip()
    spec = HASH.sub("", " " + text).strip()
    requirement, _, _marker = spec.partition(";")
    problem = None
    if requirement.startswith("-") or re.search(r"\s-", requirement):
        problem = "pip options aren't accepted"
    elif not NAME.match(requirement):
        problem = "not a package name"
    elif "://" in requirement or "@" in requirement:
        problem = "direct URLs and VCS links aren't accepted"
    elif re.search(r"[\\/]|\.(whl|zip|tar\.gz|tgz)\b", requirement):
        problem = "local paths and archive files aren't accepted"
    if problem:
        raise PrepareError(f"{text!r}: {problem}; list packages from the index, like 'requests>=2'")
    return text


def read_requirements_file(repo: Path, relative: str, seen: set[Path] | None = None) -> list[str]:
    """Requirement lines from a requirements file, following -r/--requirement to other files in the repo."""
    seen = seen if seen is not None else set()
    path = (repo / relative).resolve()
    if repo.resolve() not in path.parents:
        raise PrepareError(f"{relative} is outside the repository")
    if path in seen:
        return []
    seen.add(path)
    if not path.is_file():
        raise PrepareError(f"{relative} not found in the repository")
    lines: list[str] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = pending + raw
        pending = ""
        if raw.endswith("\\"):
            pending = raw[:-1] + " "
            continue
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        include = re.match(r"^(-r|--requirement)(\s+|=)(\S+)$", line)
        if include:
            target = (path.parent / include.group(3)).relative_to(repo.resolve())
            lines += read_requirements_file(repo, target.as_posix(), seen)
            continue
        lines.append(check_requirement(line))
    return lines


def pyproject_requirements(repo: Path) -> list[str]:
    """[project] dependencies, the dev/test extras, and the dev/test dependency groups (PEP 735)."""
    path = repo / "pyproject.toml"
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    found: list[str] = list(project.get("dependencies", []))
    extras = project.get("optional-dependencies", {})
    for name in EXTRAS:
        found += extras.get(name, [])
    groups = data.get("dependency-groups", {})

    def group(name: str, depth: int = 0) -> list[str]:
        if depth > 10:
            raise PrepareError("dependency-groups include each other in a loop")
        out: list[str] = []
        for item in groups.get(name, []):
            if isinstance(item, dict) and "include-group" in item:
                out += group(item["include-group"], depth + 1)
            elif isinstance(item, str):
                out.append(item)
        return out

    for name in EXTRAS:
        found += group(name)
    return [check_requirement(item) for item in found]


def collect(repo: Path, files: list[str] | None = None) -> dict[str, list[str]]:
    """Every declared dependency of the project, by the file it came from."""
    if not (repo / ".git").exists():
        raise PrepareError("the workspace is empty; run `python aegis.py workspace load <path>` first")
    sources: dict[str, list[str]] = {}
    if files:
        for name in files:
            sources[name] = read_requirements_file(repo, name)
        return sources
    for name in REQUIREMENT_FILES:
        if (repo / name).is_file():
            sources[name] = read_requirements_file(repo, name)
    if from_pyproject := pyproject_requirements(repo):
        sources["pyproject.toml"] = from_pyproject
    return sources


def install(requirements: list[str], allow_source_builds: bool, venv: Path = VENV) -> str:
    """Rebuild `venv` with `requirements` installed. Returns pip's summary line."""
    requirements = [check_requirement(line) for line in requirements]
    # --system-site-packages keeps the worker's own pytest, ruff and mypy available; packages
    # installed here take precedence over them.
    subprocess.run([sys.executable, "-m", "venv", "--clear", "--system-site-packages", str(venv)], check=True)
    python = venv / "bin" / "python"
    summary = "nothing to install"
    if requirements:
        listing = Path(tempfile.gettempdir()) / "aegis-requirements.txt"
        listing.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        command = [str(python), "-m", "pip", "install", "--no-input", "--disable-pip-version-check"]
        if not allow_source_builds:
            command += ["--only-binary", ":all:"]
        result = subprocess.run([*command, "-r", str(listing)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            sys.stderr.write(result.stdout[-4000:] + result.stderr[-4000:])
            hint = (
                ""
                if allow_source_builds
                else (
                    " If a package has no wheel for this platform, `--allow-source-builds` lets pip build it,"
                    " which runs that package's build script with network access."
                )
            )
            raise PrepareError("pip could not install the requirements; its output is above." + hint)
        summary = next((line for line in reversed(result.stdout.splitlines()) if line.startswith("Successfully")), "")
    # The project itself isn't installed (that would run its build backend); its source is put on
    # the path instead, as an editable install would. A missing directory is ignored by Python.
    site = next((venv / "lib").glob("python*/site-packages"))
    (site / "aegis-workspace.pth").write_text(f"{REPO}\n{REPO / 'src'}\n", encoding="utf-8")
    frozen = subprocess.run(
        [str(python), "-m", "pip", "freeze", "--local"], capture_output=True, text=True, check=False
    )
    marker = {
        "prepared": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "requirements": requirements,
        "allow_source_builds": allow_source_builds,
        "installed": frozen.stdout.split(),
    }
    (venv / MARKER_FILE).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prepare.py")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("collect")
    p.add_argument("--requirements", action="append", help="a requirements file in the repository (repeatable)")
    p = sub.add_parser("install")
    p.add_argument("--allow-source-builds", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            print(json.dumps(collect(REPO, args.requirements)))
        else:
            sources = json.load(sys.stdin)
            requirements = list(dict.fromkeys(line for lines in sources.values() for line in lines))
            print(install(requirements, args.allow_source_builds))
    except PrepareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
