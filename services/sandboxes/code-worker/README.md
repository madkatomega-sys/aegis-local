# code-worker

Sandboxed MCP worker that holds a copy of the user's repository in the `aegis-workspace` volume.

| Tool | What it does |
|---|---|
| `list_files`, `read_file`, `diff` | Read the workspace |
| `apply_patch` | Apply a unified diff with `git apply`, only if `--check` passes |
| `run_check` | Run `pytest`, `ruff`, `mypy`, or `compileall`; a non-zero exit is a failure |
| `reset_workspace` | Return to the loaded commit (always needs approval) |

Moving code in and out:

```bash
python aegis.py workspace load ../my-project     # copies the committed HEAD into the volume
python aegis.py workspace export                 # writes exports/aegis-<time>.patch
git -C ../my-project apply ../aegis-local/exports/aegis-<time>.patch
```

No shell is ever invoked. The container has no network route, so the project's dependencies
must be installed in a separate preparation step (planned for M4: a one-shot container with
network access that builds a virtual environment into the volume, after which the worker runs
offline).
