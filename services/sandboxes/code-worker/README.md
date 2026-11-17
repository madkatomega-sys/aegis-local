# code-worker

Sandboxed MCP worker that holds a copy of the user's repository in the `aegis-workspace` volume.

| Tool | What it does |
|---|---|
| `list_files`, `read_file`, `diff` | Read the workspace |
| `replace_in_file` | Replace one exact piece of text in an existing file; the text must appear exactly once, or carry `read_file`'s line numbers, which then say which lines to replace. With Qwen3-4B on OpenCode 1.19.5 and the same prompt, these edits applied far more often than diffs did ([A/B](../../../docs/validation/replace-in-file-2026-11-14-windows11-rtx4070/README.md#r3-ab-four-builder-setups-on-opencode-1195)). Files under `.git` can't be edited |
| `apply_patch` | Apply a unified diff with `git apply --recount`, only if `--check` passes; a missing final newline is added. For creating or deleting files and multi-file changes |
| `run_check` | Run `pytest`, `ruff`, `mypy`, or `compileall`; a non-zero exit is a failure. Uses the prepared environment in `/venv` when there is one |
| `reset_workspace` | Return to the loaded commit (always needs approval) |

Moving code in and out:

```bash
python aegis.py workspace load ../my-project     # copies the committed HEAD into the volume
python aegis.py workspace prepare                # installs its dependencies, if its tests need any
python aegis.py workspace export                 # writes exports/aegis-<time>.patch
git -C ../my-project apply ../aegis-local/exports/aegis-<time>.patch
```

No shell is ever invoked, and the container has no network route.

## Project dependencies: `workspace prepare`

The worker can't install anything, so `python aegis.py workspace prepare` does it in two steps.
That way, no container has both the repository and the network:

1. **In this worker (no network):** `prepare.py collect` reads the declared dependencies:
   - `requirements.txt`, `requirements-dev.txt`, `requirements-test.txt`, `dev-requirements.txt`,
     `test-requirements.txt`, following `-r` includes inside the repository;
   - `pyproject.toml`: `[project] dependencies`, the `dev`/`test`/`tests`/`testing` extras, and
     dependency groups (PEP 735) of the same names.

   `--requirements FILE` names the files instead.
2. **In `workspace-prepare` (network, no repository):** `prepare.py install` receives only that
   list. It builds a virtual environment in the `aegis-venv` volume (`/venv`), with this image's
   own packages (pytest, ruff, mypy) still visible, and installs the list into it.

This worker mounts `/venv` read-only, and `run_check` puts it first on `PATH`. The project itself
isn't installed, because that would run its build backend. Its directory (and `src/`, if there
is one) is put on the import path instead.

What `prepare` accepts:
- **Wheels only**, so no package's build script runs. `--allow-source-builds` lifts this, and
  then pip runs the build scripts of packages without a wheel, with network access.
- **Packages from the index only**, with version specifiers, extras, markers, and `--hash`
  options. Any other pip option (`--index-url`, `-e`, `--find-links`, ...), URL, VCS link, or path
  is refused before anything is installed.
- **A fresh environment each time:** each run rebuilds `/venv`. Run it again after loading a
  project with different dependencies; with nothing declared, it empties `/venv`.

The packages it installs still run inside this worker when the tests import them. See the threat
model's known limitations.
