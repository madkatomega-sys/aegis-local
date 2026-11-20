# Validation of 0.3.0 on Windows 11 with an RTX 4070

This is the evidence that release 0.3.0 works end to end on real hardware. It reruns 0.2.0's
checks and adds two new ones for this release:
- **E8:** the browser interface, with per-chat guard sessions, titles, and the status display.
- **E9:** the model helper, against the real Hugging Face.

The transcripts are unedited, except that local paths are replaced with `<home>`, `<repo>` and
`<scratchpad>`, the machine's LAN address with `<LAN address>`, and the file owner in `ls`
listings with `<user> <group>`.

**Result: pass.**
- Lint is clean; 279 tests passed.
- All five images build.
- The stub-model smoke test passed 36 of 36 checks, and the live walkthrough 58 of 58.
- The eval suite fixed **12 of 12** runs.
- The browser check passed 18 of 18.
- The model helper downloaded both of the profile's files and checked them, and it caught a
  corrupted file.

The whole run started from fresh volumes, which 0.2.0 needed a second attempt to learn (see its
[first attempt](../0.2.0-windows11-rtx4070/README.md#the-first-attempt)). Nothing was rerun.

| | |
|---|---|
| Code under test | commit `958054d` (tree `5ab67016`) on the local `release/0.3.0` branch, no uncommitted changes (see [below](#code-under-test)) |
| Date | 2026-11-20 |
| Machine | Windows 11 Pro build 26300, Intel Core i9-9900K, 31.9 GB RAM |
| GPU | NVIDIA GeForce RTX 4070, 12 GB, driver 621.24 |
| Containers | Docker Desktop, Engine 29.9.3, Compose 5.7.0 (WSL2 backend) |
| Inference | llama.cpp b12361 (commit `951d465f5`), official CUDA build |
| Tier 0 | Qwen3-4B Q4_K_M on the CPU (sha256 `7485fe6f…34fdf5`) |
| Tier 1 | Qwen3.5-9B Q6_K on the GPU (sha256 `b2ccc477…d77773e6`) |
| Profile | `nvidia-12gb` |
| OpenCode | 1.19.5 (E7, E8) |

Full details: [E0-environment.txt](E0-environment.txt), which also has `models verify` matching
both model files to the catalog.

## What was run

| Step | What it checks | Result | Evidence |
|---|---|---|---|
| E1 | Lint (`ruff check`, `ruff format --check`) and the unit and HTTP-level test suite, on the Windows host | Lint clean; 279 passed, 7 skipped (Linux-only) | [E1](E1-lint-and-tests.txt) |
| E2 | All five images build; Python version and user in each; the code worker image's `/app`, `/venv` and `/cache` | 5 built, all Python 3.15.2 as uid 10001 | [E2](E2-image-builds.txt) |
| E3 | The full stack with a stub model, with the overlays and smoke test CI runs | 36 of 36 checks | [E3](E3-stack-smoke-stub-model.txt) |
| E5 | The walkthrough with the live models, as in [0.2.0](../0.2.0-windows11-rtx4070/README.md#what-was-run), with 25 schema requests per tier | 58 of 58 checks | [E5](E5-walkthrough-cuda.txt) |
| E7 | The [eval suite](../../../evals/README.md): the builder agent, through OpenCode, fixes each of the six fixtures twice | 12 of 12 fixed | [E7](E7-evals.txt), [runs](E7-evals/) |
| E8 | The browser interface, with [`web_check.py`](../web_check.py) (below) | 18 of 18 checks | [E8](E8-browser.txt) |
| E9 | The model helper against the real Hugging Face, from a clean checkout (below) | Downloads verified; a corrupted file refused | [E9](E9-model-helper.txt) |

E4 and E6 (the Vulkan build) were last run for [0.1.0](../0.1.0-windows11-rtx4070/README.md).

## Measurements

From E5, read from the llama-server logs:

| | Tier 1: Qwen3.5-9B Q6_K, GPU | Tier 0: Qwen3-4B Q4_K_M, CPU |
|---|---|---|
| Generation | median 54.2 tokens/s | median 10.5 tokens/s |
| Time for 25 schema requests | 121 s | 486 s |
| GPU memory | 8.85 GB for the whole card, including the Windows desktop | none |

The speeds are medians over each tier's whole log, which covers more than this run's requests.
They match 0.2.0's figures for the walkthrough's own requests (54.4 and 11.6 tokens/s).

## Eval suite (E7)

| Fixture | Fixed | Median time |
|---|---|---|
| `calc`: one wrong line | 2/2 | 24 s |
| `median-crlf`: one wrong line, CRLF file | 2/2 | 27 s |
| `cart`: bugs in two files | 2/2 | 46.5 s |
| `chunks`: two edits in one function | 2/2 | 42.5 s |
| `slug`: a missing module | 2/2 | 27 s |
| `pace`: tests import `pint`, so `workspace prepare` runs first | 2/2 | 23 s |

Two runs per fixture show that each kind of task works on the release; they aren't success rates.

## The browser interface (E8)

[`web_check.py`](../web_check.py) starts `python aegis.py opencode --web` against the live stack
and drives it through the HTTP API the browser page uses. Then it checks:

- **Sign-in and reach.** The server answers only on `127.0.0.1`, not on the LAN address. Requests
  without the password, or with a wrong one, get 401. With it, the agents are listed, and Aegis is
  the only model provider.
- **Two Builder chats in one server.** The first found the sample project's bug, edited it, and
  ran the tests, which passed (17 s). The second, a new chat, ran the tests (6 s).
- **The guard ledger.** All 8 calls carry a chat tag. Each chat's calls are under its own guard
  session, and both chats share one MCP connection. Nothing was denied, held, or tripped. Each
  chat's status shows "budget 79" after its first call, which shows the budgets are separate.
- **Titles.** "Sandbox tests failure resolution" and "Sandbox tests execution status check": short
  titles, not tier 0's reasoning as before this release.
- **Status.** The status plugin raised one toast in each chat, "Aegis: budget 79 | clean".
- **Stopping.** The port is closed after the server stops.

**A cross-site request (recorded, not judged).** The check also sent requests the way a web page
in another tab of a signed-in browser could:
- A preflight from another origin got no `Access-Control-Allow-Origin`, so such a page can't read
  replies.
- A "simple" `POST /session` (`text/plain`, which needs no preflight) that carried the password
  was acted on: it created a session.

The page couldn't learn the session's id, which it would need to send a message. The
[threat model](../../THREAT-MODEL.md#known-limitations) lists this as a known limitation of
OpenCode 1.19.5's server.

The launcher's own startup lines are missing from the transcript, because stopping the process
tree on Windows lost its buffered output. That also means the password line never reached the
transcript. OpenCode's banner is there.

## The model helper (E9)

Run from a separate checkout of `958054d` (a Git worktree with no `.env`), into a new, empty
folder, so the main checkout's `.env` was not touched. A hash check at the end confirms that.

1. `models suggest` found the RTX 4070 (12.0 GB) and 31.9 GB of RAM, and suggested `nvidia-12gb`
   with its two measured files.
2. `models get`, answered "n": nothing was downloaded.
3. `models get`, answered "y": both files were downloaded from Hugging Face (9.9 GB in 114 s),
   their sizes and SHA-256s checked, and `.env` created with the profile and both paths.
   `models verify` matched both.
4. A second `models get` found both files, hash-checked them, and downloaded nothing.
5. One byte of the 4B file was changed. `models get` refused it ("has the right size but not the
   right SHA-256") and stopped, and `models verify` reported it as not matching.

**Deviation from the plan.** The plan was to download only the 4B file (2.50 GB) and hard-link the
existing 9B file into the folder, so it would be found and checked rather than downloaded. The
link command failed: Git Bash rewrote its `/H` switch, which the transcript's first lines show.
The script didn't stop on that failure, so step 3 downloaded the 9B file (7.36 GB) as well. Both
files were deleted after the run.

## Code under test

`958054d` is the release commit on the local `release/0.3.0` branch. Its parent is `main` at
`dac09d5`, and it carries:
- the version bump to 0.3.0 and the dated changelog;
- the README and guide moved to the release tag and the model helper;
- `.env.example` with no preset profile, a `doctor` warning when none is set, and `models get`
  fetching a file once when both tiers use it;
- the threat model review and the plan's open items;
- `docs/validation/web_check.py`;
- a placeholder for this page.

The release pull request adds this evidence and the 0.3.0 row in `docs/PLATFORMS.md` on top of
it; nothing else changes. The pull request is squash-merged, so `958054d` won't exist in the
repository's history after the merge. The released tree is `5ab67016` plus those documentation
files.

## Not covered

- **Signing in from a real browser.** E8 drives the server through the same HTTP API the page uses,
  with the same password, but not through a browser's sign-in prompt.
- **The Vulkan build.** 0.1.0 ran it; nothing in inference changed since.
- **The model helper on AMD, Intel, or Linux.** Only its parsing is tested for those.
- **The OpenCode 2.x desktop app.** It hangs with this stack; see
  [PLAN.md](../../PLAN.md#9-milestones-and-acceptance-criteria).
- **Native Linux on real hardware, and the GitHub upstream with a real token.** Both are still
  deferred and open.
