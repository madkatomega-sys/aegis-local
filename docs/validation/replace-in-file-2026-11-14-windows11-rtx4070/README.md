# `code__replace_in_file` with OpenCode on Windows 11 with an RTX 4070

This is the evidence for the find-and-replace edit tool. In the
[OpenCode validation](../opencode-2026-11-14-windows11-rtx4070/README.md), the local Qwen3-4B
model found the bug in every run but never wrote a unified diff that applied. This change gives
the builder `code__replace_in_file(path, old, new)` instead, and reruns the same walkthrough.

**Result:**

- **OpenCode 1.19.5: 3 of 3 runs fixed the bug** with one `replace_in_file` call and proved it with
  a passing `pytest`. Before this change, 0 of 6 runs on 1.19.5 got there.
- **OpenCode 2.3.0: 1 of 3.** The two failures were the model's, and the tool and the guard
  handled both as designed: the tool's error said exactly what was wrong, the model resent the
  same call, and the `retry` breaker stopped it. The runs that failed changed nothing.

| | |
|---|---|
| Code under test | `main` at `0e4ce1c` plus the changes in this pull request |
| Date | 2026-11-14 |
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
- Three runs per version is a small sample, enough to show the change in kind on 1.19.5 (0 of 6
  before, 3 of 3 after), not a success rate.
- The model ignored a precise error message in both 2.3.0 failures. The guard's `retry` rule is
  what stopped them.

## Not covered

- Edits that span several lines or files, files with CRLF line endings, and tainted sessions (the
  approval hold). The unit tests cover these (`tests/test_code_worker.py`, `tests/test_policy.py`),
  not a live model.
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
