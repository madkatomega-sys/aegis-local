"""Model-helper check for a release: `aegis.py models` against the real Hugging Face.

Runs the helper from a separate checkout of the code under test (a Git worktree with no `.env`),
so this checkout's `.env` is never touched, into a new, empty folder. Files already on this
machine can be hard-linked into that folder with `--link-from`, so they are found and hash-checked
instead of downloaded. Every download goes through `models get --max-gb`, so the check can't fetch
more than was approved. Any setup step that fails stops the check before anything is downloaded.

    python docs/validation/model_check.py --profile nvidia-12gb --max-gb 2.6 \
        --dir <new folder> --link-from <folder with the models you already have> --out <transcript>

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import aegis  # noqa: E402

PY = sys.executable
OUT: list[str] = []
FAILURES: list[str] = []


class SetupFailed(Exception):
    """A step the check depends on failed; nothing has been downloaded."""


def log(text: str = "") -> None:
    print(text, flush=True)
    OUT.append(text)


def check(name: str, ok: bool, detail: str = "") -> None:
    log(f"  [{'ok' if ok else 'FAIL':^4}] {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def setup(name: str, ok: bool, detail: str = "") -> None:
    """Like check, but a failure stops the run before any download."""
    log(f"  [{'ok' if ok else 'STOP':^4}] {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        raise SetupFailed(f"{name}: {detail}" if detail else name)


def run(cwd: Path, *args: str, stdin: str | None = None) -> tuple[int, str]:
    log(f"\n$ python aegis.py {' '.join(args)}" + (f"   (answering {stdin.strip()!r})" if stdin else ""))
    started = time.monotonic()
    proc = subprocess.run([PY, "aegis.py", *args], cwd=cwd, input=stdin, capture_output=True, text=True, check=False)
    # The download's progress lines are \r-separated; keep only the last state of each line.
    output = "\n".join(line.rsplit("\r", 1)[-1] for line in (proc.stdout + proc.stderr).splitlines())
    log(output.rstrip())
    log(f"[exit {proc.returncode}, {time.monotonic() - started:.0f} s]")
    return proc.returncode, output


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "(none)"


def listing(folder: Path) -> dict[str, int]:
    return {p.name: p.stat().st_size for p in sorted(folder.iterdir()) if p.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--max-gb", type=float, required=True, help="the most this check may download")
    parser.add_argument("--dir", required=True, help="a new or empty folder for the files")
    parser.add_argument("--link-from", help="a folder whose matching model files are hard-linked in")
    parser.add_argument("--out", required=True)
    parser.add_argument("--keep", action="store_true", help="keep the folder and the checkout afterwards")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    log(f"# Model helper check, commit {commit.stdout.strip()}, profile {args.profile}, at most {args.max_gb:g} GB")
    dest = Path(args.dir).resolve()
    checkout = dest.parent / (dest.name + "-checkout")
    env_before = file_sha256(ROOT / ".env")
    catalog = aegis.load_catalog()
    plan = aegis.model_plan(aegis.load_profile(args.profile), catalog, {})
    stopped = False
    made: list[Path] = []  # only what this run created gets removed
    try:
        log("\n## Setup")
        # Both folders must be new: the cleanup removes them, so it must never be handed one of yours.
        setup("the models folder doesn't exist yet", not dest.exists(), str(dest))
        setup("no checkout left from an earlier run", not checkout.exists(), str(checkout))
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
        setup("no uncommitted changes, so the checkout is the code under test", not dirty.strip(), dirty.strip())
        dest.mkdir(parents=True)
        made.append(dest)
        added = subprocess.run(
            ["git", "worktree", "add", "--detach", str(checkout), "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
        setup("a separate checkout of the code under test", added.returncode == 0, added.stderr.strip())
        made.append(checkout)
        setup("the checkout has no .env", not (checkout / ".env").exists())
        if args.link_from:
            source = Path(args.link_from).resolve()
            for model_id in sorted(set(plan.values())):
                entry = catalog[model_id]
                have = source / entry["path"]
                if not have.is_file():
                    log(f"  [ -- ] {entry['path']} isn't in {source}; it will be downloaded")
                    continue
                try:
                    os.link(have, dest / entry["path"])
                    linked = os.stat(dest / entry["path"])
                    same = os.path.samefile(have, dest / entry["path"]) and linked.st_size == entry["size"]
                    setup(f"hard link to the existing {entry['path']}", same, "not the same file, or the wrong size")
                except OSError as exc:
                    setup(f"hard link to the existing {entry['path']}", False, str(exc))
        missing = sum(catalog[m]["size"] for m in set(plan.values()) if not (dest / catalog[m]["path"]).is_file())
        log(f"  files to download: {missing / 1e9:.2f} GB")
        setup(f"that is within --max-gb {args.max_gb:g}", missing <= args.max_gb * 1e9, f"{missing / 1e9:.2f} GB")

        log("\n## The helper")
        code, out = run(checkout, "models", "suggest")
        check("suggest names a profile", code == 0 and "suggested profile:" in out)
        before = listing(dest)
        if missing:  # with nothing to download, `get` has nothing to ask about or refuse
            code, out = run(checkout, "models", "get", "--profile", args.profile, "--dir", str(dest), stdin="n\n")
            check(
                "answering 'n' downloads nothing", code == 1 and listing(dest) == before and "nothing downloaded" in out
            )
            code, out = run(
                checkout, "models", "get", "--profile", args.profile, "--dir", str(dest),
                "--max-gb", f"{missing / 2e9:.3f}", "--yes",
            )  # fmt: skip
            check(
                "a --max-gb below the plan is refused before any download",
                code != 0 and listing(dest) == before and "more than --max-gb" in out,
            )
        else:
            log("  [ -- ] every file is already here, so there is no download to decline or refuse")
        code, out = run(
            checkout, "models", "get", "--profile", args.profile, "--dir", str(dest), "--max-gb", f"{args.max_gb:g}",
            "--yes",
        )  # fmt: skip
        check("get downloads and verifies the profile's files", code == 0 and "verified" in out)
        for model_id in set(plan.values()):
            entry = catalog[model_id]
            check(f"{entry['path']} has the catalog's size", listing(dest).get(entry["path"]) == entry["size"])
        env = aegis.read_env_file(checkout / ".env")
        check("the checkout's .env names the profile", env.get("AEGIS_PROFILE") == args.profile)
        check(
            "the checkout's .env points at both files",
            all(env.get(f"AEGIS_{t.upper()}_MODEL") == (dest / catalog[m]["path"]).as_posix() for t, m in plan.items()),
        )
        code, out = run(checkout, "models", "verify")
        check("verify matches both files", code == 0 and "DOES NOT MATCH" not in out)
        after = listing(dest)
        code, out = run(
            checkout, "models", "get", "--profile", args.profile, "--dir", str(dest), "--max-gb", "0", "--yes"
        )
        check("a second run downloads nothing", code == 0 and listing(dest) == after and "downloading" not in out)

        log("\n## A corrupted file")
        victim = dest / catalog[plan["tier0"]]["path"]
        spare = dest / (victim.name + ".good")
        shutil.copyfile(victim, spare)  # a copy, so a hard-linked original is never changed
        victim.unlink()
        shutil.copyfile(spare, victim)
        with victim.open("r+b") as f:
            f.seek(-1, os.SEEK_END)
            last = f.read(1)
            f.seek(-1, os.SEEK_END)
            f.write(bytes([last[0] ^ 0xFF]))
        log(f"  changed the last byte of a copy of {victim.name}")
        code, out = run(
            checkout, "models", "get", "--profile", args.profile, "--dir", str(dest), "--max-gb", "0", "--yes"
        )
        check("get refuses it", code != 0 and "not the right SHA-256" in out)
        check("and leaves the file where it is for the user", victim.is_file())
        code, out = run(checkout, "models", "verify")
        check("verify reports it", code != 0 and "DOES NOT MATCH" in out)
    except SetupFailed as exc:
        log(f"\nstopped before downloading anything: {exc}")
        stopped = True
    finally:
        log("\n## Afterwards")
        check("this checkout's .env is unchanged", file_sha256(ROOT / ".env") == env_before)
        if not args.keep:
            if checkout in made:
                subprocess.run(["git", "worktree", "remove", "--force", str(checkout)], cwd=ROOT, capture_output=True)
                subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)
                log("  removed the checkout")
            if dest in made:
                shutil.rmtree(dest, ignore_errors=True)  # hard links go; the files they point at stay
                log(f"  removed {dest.name}")
    if stopped:
        log("\nsetup failed; nothing was downloaded")
    else:
        log(f"\n{len(FAILURES)} failure(s): {FAILURES}" if FAILURES else "\nall model helper checks passed")
    Path(args.out).write_text("\n".join(OUT) + "\n", encoding="utf-8")
    return 2 if stopped else 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
