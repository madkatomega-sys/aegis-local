# `code__replace_in_file` with OpenCode on Windows 11 with an RTX 4070

This is the evidence for the find-and-replace edit tool. In the
[OpenCode validation](../opencode-2026-11-14-windows11-rtx4070/README.md), the local Qwen3-4B
model found the bug in every run but never wrote a unified diff that applied. This change gives
the builder `code__replace_in_file(path, old, new)` instead, reruns the same walkthrough, and then
compares this pull request with `main` in a controlled A/B.

**Result:**

- **Controlled A/B on OpenCode 1.19.5, 10 runs per arm on each of two bugs** ([R3](#r3-ab-this-pull-request-against-main)):
  with this pull request, the builder fixed a one-line bug in **10 of 10** runs, against **2 of 10**
  with `main`'s builder (two-sided Fisher exact p = 0.0007). It fixed a bug that needs two edits,
  one of them an inserted indented block, in **6 of 10** against **1 of 10** (p = 0.057). Every
  failure with this pull request was a wrong or half fix that applied, so the tests caught it; 16 of
  `main`'s 17 failures ended with no edit applied.
- **Walkthroughs, 3 runs per version:** 3 of 3 on 1.19.5 and 1 of 3 on 2.3.0. The two 2.3.0
  failures were the model's, and the tool and the guard handled both as designed: the tool's error
  said exactly what was wrong, the model resent the same call, and the `retry` breaker stopped it.
  The runs that failed changed nothing. There is no A/B on 2.3.0.

| | |
|---|---|
| Code under test | `main` at `0e4ce1c` plus the changes in this pull request |
| Date | 2026-11-14 (walkthroughs), 2026-11-14 and 2026-11-15 (A/B) |
| OpenCode | 1.19.5 and 2.3.0, as in the OpenCode validation |
| Machine, stack, model | Same as the [0.1.0 validation](../0.1.0-windows11-rtx4070/README.md): Windows 11 Pro 26300, RTX 4070 12 GB, Docker Desktop 29.9.3, llama.cpp b12361 CUDA, Qwen3-4B Q4_K_M on both tiers, profile `nvidia-12gb` |
| Sample project | the same Git repository: `calc.py` has `mean()` return `sum(values) / (len(values) + 1)`, and `tests/test_calc.py` asserts `mean([2, 4, 6]) == 4` |

Times in the transcripts are local (UTC−5); the guard's ledger is in UTC.

## Walkthroughs

Each run loads a fresh sandbox copy of the sample project and gives the builder agent the same
plain request as before: "The tests in the sandbox workspace fail. Find the bug, fix it, and show
me that the tests pass." Transcripts are in [R1-walkthroughs](R1-walkthroughs/); the audit rows
are in [R2](R2-audit-ledger.txt).

| Run | OpenCode | What the model did | Ended by | Events |
|---|---|---|---|---|
| v1 run 1 | 1.19.5 | Listed files, read the test and `calc.py`, replaced the buggy line, ran `pytest`: passed | Model, done | 59–63 |
| v1 run 2 | 1.19.5 | Ran `pytest` first (failed), read `calc.py`, replaced the line, `pytest` passed | Model, done | 69–73 |
| v1 run 3 | 1.19.5 | Same as run 2 | Model, done | 81–84 |
| v2 run 1 | 2.3.0 | Read both files, then wrote nine `execute` calls with broken JavaScript (for example `{…, "new": "…")` with no closing brace); OpenCode rejected them before they reached the guard. The tenth parsed: the replacement applied and `pytest` passed | Model, done | 64–68 |
| v2 run 2 | 2.3.0 | Wrote JavaScript that took the buggy line from `read_file`'s output, line number and all, and passed it as `old`. The tool answered "old starts each line with a line number from read_file; remove the numbers and the tab after them". The model resent the same call | `retry` breaker | 74–80 |
| v2 run 3 | 2.3.0 | Never read `calc.py`; sent imagined code with `old` and `new` identical. The tool answered "old and new are the same; nothing to change". The model resent the same call | `retry` breaker | 85–88 |

Each run that finished exported the same one-line patch:

```diff
-    return sum(values) / (len(values) + 1)
+    return sum(values) / len(values)
```

Policy, as the ledger records it: every `code__replace_in_file` call matched its own rule
(`code__replace_in_file`, mutating and a progress tool), not the read-only `code__*` catch-all.
No session was tainted, so no call was held for approval.

## What this shows and what it doesn't

- On 1.19.5, which calls tools directly, the edit tool closes the gap the diffs left: the model
  copies the line it read, and the edit applies.
- On 2.3.0, tools are called through JavaScript (`execute`, "code mode"), and the 4B model's
  JavaScript is the weak link. Two of its three failures (the broken syntax in run 1, and building
  `old` from the numbered `read_file` output in run 2) come from that layer.
- Three runs per version is too few for a success rate. The A/B in R3 is the controlled
  comparison for 1.19.5; the walkthroughs show what the calls and errors look like.
- The model ignored a precise error message in both 2.3.0 failures. The guard's `retry` rule is
  what stopped them.

## R3: A/B, this pull request against `main`

The walkthroughs compared this change with an earlier, separate validation. This section compares
the two directly: same machine, same model, same OpenCode 1.19.5, same request, runs interleaved.

| | Arm A: `main` | Arm B: this pull request |
|---|---|---|
| Builder prompt | `config/agents/builder.md` from `main` (diffs only) | this pull request's (prefers `replace_in_file`) |
| Builder's edit tools | `apply_patch` (`replace_in_file` turned off in the OpenCode config) | `replace_in_file` and `apply_patch` |
| Everything else | this pull request's `config/opencode.json`, worker, guard and router | the same |

Arm B changes two things at once, the tool and the prompt that tells the model to use it, so this
measures the pull request as a whole, not the tool alone. The worker in both arms is this pull
request's; the changes after `3b51171` only affect files with CRLF or mixed line endings, and both
fixtures are LF-only.

Two fixtures, each a one-commit Git repository ([calc](R3-ab/fixture-calc.txt),
[chunks](R3-ab/fixture-chunks.txt)):

- **calc**: the walkthrough bug, one line to change.
- **chunks**: `chunks(items, size)` splits too late (`> size` instead of `== size`) and drops the
  last short chunk. The fix changes one line and inserts `if current: result.append(current)`
  after the loop, at a different indentation.

Each run loads a fresh copy into the sandbox and sends the walkthrough request, with a 15-minute
limit per run. Ten rounds; in each round both fixtures run under both arms, A first in odd rounds
and B first in even ones. A run counts as fixed only if its exported patch, applied to a clean
clone of the fixture, makes `pytest` pass; what the model said doesn't count.

| Fixture | Arm | Fixed | Edited, tests still fail | No edit applied | Never tried to edit | Median time |
|---|---|---|---|---|---|---|
| calc | A: `main` | 2 | 0 | 6 | 2 | 92 s |
| calc | B: this PR | **10** | 0 | 0 | 0 | 49 s |
| chunks | A: `main` | 1 | 1 | 7 | 1 | 94 s |
| chunks | B: this PR | **6** | 4 | 0 | 0 | 138 s |

- **The failure that goes away is the edit not applying.** In arm A, 4 of 25 `apply_patch` calls
  applied; most of the rest were rejected diffs, and the `retry` breaker stopped 10 of the 20 runs.
  In arm B, 26 of 40 edit calls applied, and every run got at least one edit in.
- **The failures that remain are the model's diagnosis.** Three of arm B's four chunks failures
  changed `>` to `==` and missed the dropped last chunk; the fourth (run 5) made a wrong change.
  In run 3 the model also tried the second edit, but its `old` was the line it had already
  changed, so it didn't match, and the `retry` breaker stopped it on the identical resend.
- **calc p = 0.0007, chunks p = 0.057** (two-sided Fisher exact). Ten runs per arm on chunks
  doesn't rule out chance on its own; the direction matches calc and the per-call numbers above.
- No run timed out. Arm B is slower on chunks because it gets further: it runs `pytest` after an
  edit and sometimes edits again.

**Load during the runs.** From the fourth run on, each run sampled GPU memory, GPU load and
running processes every 5 seconds ([results](R3-ab/results.txt)). Firefox was open in 36 of those
37 runs, and Genshin Impact in the last one (chunks A run 10, which applied a wrong fix and failed
on its merits). GPU memory peaked at 6.8 GB of 12 GB. Other GPU work slows a run but doesn't
change what the model writes, and no run came near its time limit.

Files: [results.txt](R3-ab/results.txt) (every run, with its outcome, ledger range and load),
[transcripts](R3-ab/transcripts/), [patches](R3-ab/patches/) (every exported patch, including the
ones that fail), and [ledger.txt](R3-ab/ledger.txt) (events 98 to 336). Runs 1 of calc A, calc B
and chunks A ran on 2026-11-14; the machine then restarted, and the rest ran on 2026-11-15.

## Not covered

- Edits across several files, files with CRLF line endings, and tainted sessions (the approval
  hold). The unit tests cover these (`tests/test_code_worker.py`, `tests/test_policy.py`), not a
  live model.
- The tool without the prompt change, or the prompt without the tool (see R3).
- The interactive TUI, other OpenCode versions and models, an A/B on 2.3.0, and Linux.

## How these files were made

The same way as the [OpenCode validation](../opencode-2026-11-14-windows11-rtx4070/README.md#how-these-files-were-made):
PowerShell transcripts with the header and footer removed, the scratch directory replaced with
`<scratchpad>`, colour codes and PowerShell filler lines removed, and step 4 filled from the
ledger and marked "added from R2-audit-ledger.txt".

One correction: in v2 runs 2 and 3, step 3 of the original transcript showed the previous run's
patch. The script printed the newest file in `exports/` even when the run exported nothing. Those
two transcripts say so in step 3, and the patch is removed. `aegis.py workspace export` after the
last run reported "no changes in the sandbox", and the ledger shows no successful change in either
session. The script now prints only a patch the run created.

Where this README describes what a 2.3.0 call returned, it comes from OpenCode's own session
store; `opencode run` prints the calls but not their results.

R3 was made by a PowerShell harness that, for each run, loads the fixture, notes the ledger's
next event id, runs `opencode run --agent builder` with the arm's config, exports, and records the
time, timeout, ledger range and load samples. A separate script then applied each patch to a clean
clone and ran `pytest`. The transcripts are cleaned the same way as above; the patches are as
exported, with line endings normalised to LF. A smoke run of the harness before round 1 is not
included.
