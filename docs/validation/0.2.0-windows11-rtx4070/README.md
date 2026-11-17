# Validation of 0.2.0 on Windows 11 with an RTX 4070

This is the evidence that release 0.2.0 works end to end on real hardware: native `llama-server`
tiers, the container stack on Docker Desktop, every guard defense exercised against a live model,
the new `workspace prepare` isolation, and the builder agent fixing broken projects through the
whole harness. The transcripts are unedited except that local paths are replaced with `<home>`,
`<repo>` and `<scratchpad>`, and the machine's LAN address with `<LAN address>`.

**Result: pass.**
- Lint is clean; 216 tests passed.
- All five images build.
- The stub-model smoke test passed 36 of 36 checks, and the live walkthrough 58 of 58.
- The eval suite fixed **12 of 12** runs.

The smoke test and walkthrough needed a second attempt. The first ran with a sandbox workspace
left over from earlier eval runs, and the check that expects a fresh stack's empty workspace
failed in both. See [below](#the-first-attempt).

| | |
|---|---|
| Code under test | commit `a7cea22` (tree `cbd5ceff`) on the local `release/0.2.0` branch, no uncommitted changes (see [below](#code-under-test)) |
| Date | 2026-11-17 |
| Machine | Windows 11 Pro build 26300, Intel Core i9-9900K, 31.9 GB RAM |
| GPU | NVIDIA GeForce RTX 4070, 12 GB, driver 610.88 |
| Containers | Docker Desktop, Engine 29.9.3, Compose 5.7.0 (WSL2 backend) |
| Inference | llama.cpp b12361 (commit `951d465f5`), official CUDA build |
| Tier 0 | Qwen3-4B Q4_K_M on the CPU (sha256 `7485fe6f…34fdf5`) |
| Tier 1 | Qwen3.5-9B Q6_K on the GPU (sha256 `b2ccc477…d77773e6`), the profile's first candidate, as measured in [the 12 GB tier 1 evidence](../12gb-tier1-2026-11-17-windows11-rtx4070/README.md) |
| Profile | `nvidia-12gb` |
| OpenCode | 1.19.5 (E7) |

Full details: [E0-environment.txt](E0-environment.txt).

## What was run

| Step | What it checks | Result | Evidence |
|---|---|---|---|
| E1 | Lint (`ruff check`, `ruff format --check`) and the unit and HTTP-level test suite, on the Windows host | Lint clean; 216 passed, 7 skipped (Linux-only) | [E1](E1-lint-and-tests.txt) |
| E2 | All five images build; Python version and user in each; the code worker image's `prepare.py`, `/venv` and `/cache` | 5 built, all Python 3.15.2 as uid 10001; `/venv` and `/cache` owned by that user | [E2](E2-image-builds.txt) |
| E3 | The full stack with a stub model, with the overlays and smoke test CI runs, now including `workspace prepare` | 36 of 36 checks | [E3](E3-stack-smoke-stub-model.txt) |
| E5 | The walkthrough with the live models (below), with 25 schema requests per tier | 58 of 58 checks | [E5](E5-walkthrough-cuda.txt) |
| E7 | The [eval suite](../../../evals/README.md): the builder agent, through OpenCode, fixes each of the six fixtures twice; scored by applying its patch to a clean copy and running the tests | 12 of 12 fixed | [E7](E7-evals.txt), [runs](E7-evals/), [ledger](E7-ledger.txt) |

The walkthrough (E5) drives the release the way a user would, through `aegis.py` and the guard's
MCP endpoint, as in [0.1.0](../0.1.0-windows11-rtx4070/README.md#what-was-run):

1. `doctor`, `up`, `status`.
2. The smoke test against the live models:
   - routing;
   - 25 extractions on tier 0 and 25 schema-constrained answers on tier 1, all valid JSON;
   - guard authentication and tool filtering;
   - exact math and the math time limit;
   - the writing auditor.
3. The prompt-injection hold: the fixture GitHub server's poisoned issue taints the session, and
   the next mutating call is held for approval.
4. Sandbox isolation for every worker, and the new `workspace prepare` checks:
   - the install container can't reach the router, guard, or worker, can't see the repository,
     holds no tokens, and has a read-only root;
   - it refuses pip options;
   - it installs a real wheel, which the worker imports but can't change.
5. The workspace round trip: `load`, a failing test, a fix, a passing test, `ruff`, `export`, and
   `git apply --check` of the exported patch against the original checkout.
6. Approvals and the breaker through the launcher: `pending`, `approve`, `deny`, `status`, `reset`.
7. Every harness port listens on loopback only, and none answers on the LAN address.
8. `down` closes every port.

Changes from 0.1.0's walkthrough:
- **25 schema requests per tier instead of 100**, as 0.1.0's Vulkan run (E6) did. Tier 0 runs on
  the CPU at about 11 tokens/s, so 100 extractions would take about 30 minutes.
- **The `workspace prepare` checks** are new.

## Measurements

From E5, read from the llama-server logs for the walkthrough's own requests:

| | Tier 1: Qwen3.5-9B Q6_K, GPU | Tier 0: Qwen3-4B Q4_K_M, CPU |
|---|---|---|
| Generation | median 54.4 tokens/s over 26 requests | median 11.6 tokens/s over 28 requests |
| Time for 25 schema requests | 110 s | 440 s |
| GPU memory | 8.8 GB for the whole card, including the Windows desktop | none |

The speed lines inside E5 itself cover each tier's whole log, which also holds earlier runs with
the 4B model on tier 1, so they differ from these.

## Eval suite (E7)

| Fixture | Fixed | Median time |
|---|---|---|
| `calc`: one wrong line | 2/2 | 30.5 s |
| `median-crlf`: one wrong line, CRLF file | 2/2 | 26 s |
| `cart`: bugs in two files | 2/2 | 41.5 s |
| `chunks`: two edits in one function | 2/2 | 56.5 s |
| `slug`: a missing module | 2/2 | 41 s |
| `pace`: tests import `pint`, so `workspace prepare` runs first | 2/2 | 28 s |

Two runs per fixture show that each kind of task works on the release; they aren't success rates.
For rates, see the [12 GB tier 1 evidence](../12gb-tier1-2026-11-17-windows11-rtx4070/README.md),
where the same model fixed 14 of 15. E7's runs ran on the same commit as E1 to E5, after them,
before the fresh re-run of E3 and E5. Its guard ledger rows are in [E7-ledger.txt](E7-ledger.txt).

## The first attempt

The first run of E3 and E5 failed one check each, the same one: `answers (empty workspace)`.
After the stack starts, the smoke test asks the code worker to list files and expects the "workspace is
empty" answer that a new stack gives. This machine's `aegis-workspace` volume still held `calc.py`
from earlier eval runs, because volumes outlive `docker compose down`. CI always starts from
fresh volumes, so it never sees this. Every other check passed (35 in E3, 57 in E5).

For the second attempt, the stack's volumes were removed (`down -v`), and E3 and E5 were run
again on the same commit. They passed in full. Both first-attempt transcripts are in
[first-attempt/](first-attempt/).

Before the volumes were removed, the guard's audit ledger was copied out of its volume (it is not
part of this evidence).

Two lines of E0 and E2 were also filled in afterwards, each marked "added" in the transcript.
A shell function in the validation script had shadowed `cmd.exe` (the Windows version), and Git
Bash had rewritten container paths in the `ls` of `/app`, `/venv` and `/cache`.

## Code under test

`a7cea22` is the release commit on the local `release/0.2.0` branch. Its parent is `main` at
`9b26fff`, and it carries:
- the version bump to 0.2.0;
- the dated changelog;
- the threat model review;
- `workspace prepare` checks added to `docs/validation/walkthrough.py`;
- a placeholder for this page.

The release pull request adds this evidence and the 0.2.0 row in `docs/PLATFORMS.md` on top of
it; nothing else changes. The pull request is squash-merged, so `a7cea22` won't exist in the
repository's history after the merge. The released tree is `cbd5ceff` plus those documentation
files.

## Not covered

- **The Vulkan build.** 0.1.0 ran it; 0.2.0 changed nothing in inference.
- **OpenCode 2.3.0 in the eval suite.** It was covered in the
  [replace_in_file evidence](../replace-in-file-2026-11-14-windows11-rtx4070/README.md#r4-opencode-230-and-the-line-number-change).
- **Native Linux on real hardware, and the GitHub upstream with a real token.** Both are deferred
  and open; see [PLAN.md](../../PLAN.md#9-milestones-and-acceptance-criteria).
