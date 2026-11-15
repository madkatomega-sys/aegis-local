# `code__replace_in_file` with OpenCode on Windows 11 with an RTX 4070

This is the evidence for the find-and-replace edit tool. In the
[OpenCode validation](../opencode-2026-11-14-windows11-rtx4070/README.md), the local Qwen3-4B
model found the bug in every run but never wrote a unified diff that applied. This change gives
the builder `code__replace_in_file(path, old, new)` instead, reruns the same walkthrough, and then
compares four builder setups in a controlled A/B.

**Result:**

- **The tool works on its own** ([R3](#r3-ab-four-builder-setups-on-opencode-1195)). On OpenCode
  1.19.5, with the same neutral prompt that names no edit tool, turning `replace_in_file` on took
  the builder from **1 of 10 to 9 of 10** on a one-line bug (two-sided Fisher exact p = 0.001), and
  from **1 of 20 to 12 of 20** across both bugs (p = 0.0004).
- **This pull request against `main`**, tool and prompt together: **10 of 10 against 2 of 10** on
  the one-line bug (p = 0.0007), and **6 of 10 against 1 of 10** on a bug that needs two edits, one
  of them an inserted indented block (p = 0.057). Every failure with this pull request was a wrong
  or half fix that applied, so the tests caught it; 16 of `main`'s 17 failures ended with no edit
  applied.
- **The prompt's own share is not shown.** With the tool on, this pull request's prompt did better
  than the neutral one on the two-edit bug (6 against 3 of 10), but ten runs per arm can't tell
  that from chance (p = 0.37).
- **OpenCode 2.3.0** ([R4](#r4-opencode-230-and-the-line-number-change)): this pull request's
  builder fixed the one-line bug in **6 of 10** runs before the line-number change in this pull
  request and **6 of 10** after it. No run in either set sent `old` in the form the change
  handles, so it neither helped nor hurt here; 2.3.0's failures come from its JavaScript layer and
  the model's diagnosis.
- **Walkthroughs, 3 runs per version:** 3 of 3 on 1.19.5 and 1 of 3 on 2.3.0, described below.

| | |
|---|---|
| Code under test | `main` at `0e4ce1c` plus the changes in this pull request |
| Date | 2026-11-14 (walkthroughs), 2026-11-14 and 2026-11-15 (R3 and R4) |
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
- Three runs per version is too few for a success rate. R3 and R4 are the controlled comparisons;
  the walkthroughs show what the calls and errors look like.
- The model ignored a precise error message in both 2.3.0 failures. The guard's `retry` rule is
  what stopped them.
- Run 2's second attempt did try to remove the number, with the regular expression `/^\d+\t/`.
  `read_file` pads its numbers with spaces (`"     2\t"`), so the expression matched nothing and
  the call went out unchanged. That is what the line-number change in this pull request handles
  (see R4).

## R3: A/B, four builder setups on OpenCode 1.19.5

The walkthroughs compared this change with an earlier, separate validation. This section compares
builder setups directly: same machine, same model, same OpenCode 1.19.5, same request, runs
interleaved.

| Arm | Builder prompt | Builder's edit tools |
|---|---|---|
| A: `main` | `config/agents/builder.md` from `main` (diffs only) | `apply_patch` |
| B: this pull request | this pull request's (prefers `replace_in_file`) | `replace_in_file` and `apply_patch` |
| C: neutral, tool on | this pull request's, with the edit rules replaced by one that names no tool (below) | `replace_in_file` and `apply_patch` |
| D: neutral, tool off | the same as C | `apply_patch` |

Everything else is this pull request's: `config/opencode.json` (with `replace_in_file` turned off
for the builder in arms A and D), the guard and the router. The neutral rule in C and D reads
"Change files with the workspace's edit tools; each tool's description says what it expects. If an
edit fails, read its error, read the file again, and retry with corrected input."

- **A against B** is the pull request as a whole: tool and prompt change together.
- **C against D** is the tool alone: same prompt, tool on or off.
- **B against C** is the prompt, with the tool on.

The worker is this pull request's in every arm, as of `8e06df1`; the changes before it after
`3b51171` only affect files with CRLF or mixed line endings, and both fixtures are LF-only. Arms A
and B ran first (2026-11-14 and 2026-11-15), then C and D (2026-11-15).

Two fixtures, each a one-commit Git repository ([calc](R3-ab/fixture-calc.txt),
[chunks](R3-ab/fixture-chunks.txt)):

- **calc**: the walkthrough bug, one line to change.
- **chunks**: `chunks(items, size)` splits too late (`> size` instead of `== size`) and drops the
  last short chunk. The fix changes one line and inserts `if current: result.append(current)`
  after the loop, at a different indentation.

Each run loads a fresh copy into the sandbox and sends the walkthrough request, with a 15-minute
limit per run. Ten rounds of A and B, then ten rounds of C and D; in each round both fixtures run
under both arms, alternating which arm goes first. A run counts as fixed only if its exported
patch, applied to a clean clone of the fixture, makes `pytest` pass; what the model said doesn't
count.

| Fixture | Arm | Fixed | Edited, tests still fail | No edit applied | Never tried to edit | Median time |
|---|---|---|---|---|---|---|
| calc | A: `main` | 2 | 0 | 6 | 2 | 85 s |
| calc | B: this PR | **10** | 0 | 0 | 0 | 48 s |
| calc | C: neutral, tool on | **9** | 0 | 0 | 1 | 55 s |
| calc | D: neutral, tool off | 1 | 0 | 9 | 0 | 66 s |
| chunks | A: `main` | 1 | 1 | 7 | 1 | 93 s |
| chunks | B: this PR | **6** | 4 | 0 | 0 | 138 s |
| chunks | C: neutral, tool on | 3 | 4 | 1 | 2 | 128 s |
| chunks | D: neutral, tool off | 0 | 0 | 7 | 3 | 111 s |

| Comparison | calc | chunks | both | Fisher exact, two-sided |
|---|---|---|---|---|
| Tool alone (C against D) | 9 against 1 | 3 against 0 | 12 against 1 of 20 | calc 0.001, chunks 0.21, both 0.0004 |
| Pull request (B against A) | 10 against 2 | 6 against 1 | 16 against 3 of 20 | calc 0.0007, chunks 0.057, both < 0.0001 |
| Prompt, tool on (B against C) | 10 against 9 | 6 against 3 | 16 against 12 of 20 | calc 1.0, chunks 0.37, both 0.30 |

- **The failure that goes away is the edit not applying.** Without the tool (arms A and D), 5 of
  46 `apply_patch` calls applied, and 35 of 40 runs ended with no edit applied or none tried. With
  it (B and C), 46 of 69 edit calls applied.
- **The failures that remain are the model's diagnosis.** Three of arm B's four chunks failures
  changed `>` to `==` and missed the dropped last chunk; the fourth (run 5) made a wrong change.
  In run 3 the model also tried the second edit, but its `old` was the line it had already
  changed, so it didn't match, and the `retry` breaker stopped it on the identical resend. Arm C's
  chunks failures look the same: four applied a wrong or half fix, two never tried an edit, and in
  one the edit never applied.
- **What the prompt adds is unclear.** B beat C on chunks, 6 to 3, and C had three runs that never
  tried an edit or got none in, against none in B. That fits a prompt that says how to copy `old`
  helping on multi-line edits, but at ten runs per arm it is not distinguishable from chance.
- **Two neutral-prompt numbers sit on zero or one** (D: 1 and 0), so the chunks comparison for the
  tool alone (3 against 0) has little power on its own; across both fixtures it is clear.
- No run timed out. B is slower on chunks because it gets further: it runs `pytest` after an edit
  and sometimes edits again.

**Load during the runs.** From the fourth run on, each run sampled GPU memory, GPU load and
running processes every 5 seconds ([results](R3-ab/results.txt)). Firefox was open in all but one
of the sampled runs. Genshin Impact was running in one run of A and B (chunks A run 10, which
applied a wrong fix and failed on its merits) and in every run of C and D. GPU memory peaked at
6.8 GB of 12 GB in A and B, and at 9.4 GB in C and D. Other GPU work slows a run but doesn't change
what the model writes, and no run in R3 came near its time limit. C and D ran with more load than
A and B, so compare C with D, and A with B, rather than across the two batches.

Files: [results.txt](R3-ab/results.txt) (every run, with its outcome, ledger range and load),
[transcripts](R3-ab/transcripts/), [patches](R3-ab/patches/) (every exported patch, including the
ones that fail), and [ledger.txt](R3-ab/ledger.txt) (events 98 to 655, R3 and R4). Runs 1 of calc
A, calc B and chunks A ran on 2026-11-14; the machine then restarted, and the rest ran on
2026-11-15.

## R4: OpenCode 2.3.0 and the line-number change

On 2.3.0 the model calls tools from JavaScript, and in walkthrough run 2 it built `old` from
`read_file`'s numbered output. This pull request now accepts that: if `old` doesn't match as sent
but every line starts with `read_file`'s line number (padded as shown, or trimmed), the numbers say
which lines to replace, and the text after them must be exactly those lines. Numbers that point at
the wrong lines, or text that differs, change nothing.

To measure it, arm B ran ten times on 2.3.0 with the calc fixture on the worker at `8e06df1`
(before), then ten times after the worker was rebuilt with the change (after), with Genshin Impact
and Firefox running throughout.

| | Fixed | No edit applied | Never tried to edit | Median time |
|---|---|---|---|---|
| Before (`v2pre-`) | 6 | 2 | 2 | 75 s |
| After (`v2post-`) | 6 | 2 | 2 | 60 s |

- **No run in either set sent `old` in the form the change handles**, so the change neither helped
  nor hurt here. Two runs before it built `old` from `read_file`'s numbered text (runs 9 and 10),
  but each was also a wrong change: run 10 replaced a line with itself, which the change would
  refuse as "the same", and run 9 edited a function the model had imagined (`mean(numbers)`).
- **The failures are 2.3.0's JavaScript and the model's diagnosis.** After the change, run 1 sent
  four `replace_in_file` calls with broken JavaScript (a missing `}`) that never reached the guard,
  then diffs that didn't apply, until the `retry` breaker stopped it; runs 2 and 4
  never tried an edit; run 7 cut `old` out of the numbered output from the middle of a line and
  edited an imagined function. Before it, run 5 sent broken JavaScript until it hit the 15-minute
  limit, the only timeout in R3 and R4; the guard's breaker never saw those calls.
- The 1 of 3 in the walkthroughs was a small sample; on this fixture 2.3.0 fixes 6 of 10 with or
  without the change.

The change is still in the pull request because it removes one way a code-mode call can fail and
is covered by unit tests, including the cases where it must not apply (TSV data, a bare number,
numbers that name the wrong lines); this run doesn't show it improving 2.3.0's success rate.

## Not covered

- Edits across several files, files with CRLF line endings, and tainted sessions (the approval
  hold). The unit tests cover these (`tests/test_code_worker.py`, `tests/test_policy.py`), not a
  live model.
- This pull request's prompt without the tool: the prompt names `replace_in_file`, so it isn't a
  meaningful setup.
- The two-edit fixture on 2.3.0, and arms A, C and D on 2.3.0.
- The interactive TUI, other OpenCode versions and models, and Linux.

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

R3 and R4 were made by a PowerShell harness that, for each run, loads the fixture, notes the
ledger's next event id, runs `opencode run --agent builder` (with `--standalone` on 2.3.0) with
the arm's config, exports, and records the
time, timeout, ledger range and load samples. A separate script then applied each patch to a clean
clone and ran `pytest`. The transcripts are cleaned the same way as above; the patches are as
exported, with line endings normalised to LF. A smoke run of the harness before round 1 is not
included.
