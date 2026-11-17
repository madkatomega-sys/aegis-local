# Evals

The eval suite asks the builder agent to fix small broken projects through the real harness:
OpenCode, the guard, the sandbox and your local models. It then checks the result the same way
every time. A run counts as **fixed** only if the diff the sandbox exports, applied to a clean
copy of the project, makes its tests pass. What the model says about its work doesn't count.

It needs a GPU and a running stack, and a full run takes hours, so it isn't part of CI. CI does
run `tests/test_evals.py`. That checks every fixture fails as given and passes with its reference
fix, every arm builds a valid config, and the scoring and report logic.

## Running it

```bash
python aegis.py up
python evals/run.py list
python evals/run.py run --runs 10
python evals/run.py run --arms default,no-replace --fixtures calc,chunks --runs 10
python evals/run.py report evals/results/<time>
```

- `--runs N`: runs per fixture and arm. Ten per arm is the minimum for comparing arms; with fewer,
  the p-values in the report will rarely mean anything.
- `--out DIR`: the results folder. Running again with an existing folder resumes it and skips
  finished runs, so a restart or a sleep costs only the run in progress.
- `--opencode PATH`: the OpenCode executable to use. Its version is recorded, and 2.x is run with
  `--standalone`.
- `--timeout SECONDS`: when to stop a run (default 900). A timeout is reported separately from a
  wrong fix.

Runs are interleaved: every round runs each fixture under each arm, and which arm goes first
rotates from round to round, so no arm always gets the warmer or later slot.

## What a results folder holds

`evals/results/` is ignored by Git. Copy a run you want to keep into `docs/validation/`.

| File | Contents |
|---|---|
| `manifest.json` | Aegis-Local commit (and whether it had uncommitted changes), OpenCode version, hardware profile, the model file each tier was serving, fixtures, arms, runs and timeout |
| `runs.jsonl` | One line per run: outcome, `pytest`'s last line, seconds, whether it timed out, its range of guard ledger events, tool calls, breaker trips, and GPU memory and load if `nvidia-smi` is installed |
| `summary.md` | Outcomes per fixture and arm, with a two-sided Fisher exact test for each pair of arms |
| `transcripts/` | What OpenCode printed in each run |
| `patches/` | Each run's exported diff, exactly as Git wrote it |
| `configs/` | The OpenCode config each arm ran with |

Each run ends in one of four outcomes, which separate a wrong fix from an edit that never applied:

- **fixed**: the tests pass.
- **edited, tests fail**: at least one edit applied, but the tests still fail.
- **no edit applied**: the model tried edits and none applied.
- **never tried to edit**: no edit reached the guard.

## Fixtures

Each folder in `fixtures/` is one project:
- `fixture.toml` holds a description, and optionally a `prompt`, `crlf = true`, and
  `prepare = true`.
- `solution.patch` is the reference fix.
- Everything else is the project itself.

A run builds the project as a one-commit Git repository with a fixed author and date, so every run
starts from the same commit.

| Fixture | What it tests |
|---|---|
| `calc` | One wrong line. The fixture from the 0.1 walkthroughs and the `replace_in_file` A/B |
| `chunks` | Two edits in one function, one of them an inserted block at a different indentation |
| `median-crlf` | One wrong line in a file with CRLF line endings |
| `cart` | Two bugs in two files |
| `slug` | A missing module: the fix is a new file |
| `pace` | One wrong unit, in a project whose tests import a package (`pint`): the sandbox needs `workspace prepare` |

To add a fixture, create the folder with its tests and its bug, and write `solution.patch` against
it. `tests/test_evals.py` picks it up and checks that the tests fail without the patch and pass
with it.

A fixture whose tests need packages lists them in its own `requirements.txt` and sets
`prepare = true` in `fixture.toml`. The runner then runs `aegis.py workspace prepare` after each
load, and clears the prepared packages before a fixture that needs none. Scoring runs the tests
on the host, so add the packages to `requirements-dev.txt` too.

## Arms

`arms.toml` lists builder setups. Each is this checkout's `config/opencode.json` with changes for
the builder only: a different prompt, tools turned off, or both.

- `default` is the builder as configured.
- `no-replace` turns `replace_in_file` off.
- `neutral` and `neutral-no-replace` use a prompt that names no edit tool, with `replace_in_file`
  on and off, to measure the tool on its own.
- `release-0.1.0` is the builder as released in 0.1.0.

`tests/test_evals.py` fails when `config/agents/builder.md` changes and the neutral prompt doesn't
change with it.
