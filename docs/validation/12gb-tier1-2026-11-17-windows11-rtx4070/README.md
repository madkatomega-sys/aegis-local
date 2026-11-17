# Tier 1 on the 12 GB profile: Qwen3.5-9B against Qwen3-4B

Every validation up to 0.2 ran Qwen3-4B on both tiers. That's smaller than the `nvidia-12gb`
profile asks for: its tier 1 is a 9–14B model, and the first candidate is Qwen3.5-9B Q6_K. This is
the [eval suite](../../../evals/README.md) run with each model on tier 1, on the same machine,
the same day, with everything else unchanged.

**Result:** with Qwen3.5-9B on tier 1, the builder fixed **14 of 15** runs, against **10 of 19**
with Qwen3-4B (two-sided Fisher exact p = 0.02). The difference is in the two harder fixtures,
which the 4B never fixed:

| Fixture | Qwen3.5-9B | Qwen3-4B | p |
|---|---|---|---|
| `chunks`: two edits in one function, one an inserted block | **6/6** | 0/4 | 0.005 |
| `slug`: a missing module, so the fix is a new file | **5/6** | 0/3 | 0.048 |
| `calc`: one wrong line | 1/1 | 4/4 | |
| `median-crlf`: one wrong line, CRLF file | 1/1 | 4/4 | |
| `cart`: bugs in two files | 1/1 | 2/4 | |
| All | **14/15** | 10/19 | 0.02 |

The 9B was also quicker: a median of 44 s per run against 79 s, because it needed fewer attempts.

| | |
|---|---|
| Code under test | `main` at `594e967` (no uncommitted changes, as each `manifest.json` records) |
| Date | 2026-11-17 |
| Machine | Windows 11 Pro 26300, RTX 4070 12 GB, i9-9900K, 32 GB RAM, Docker Desktop |
| Inference | llama.cpp b12361 CUDA, profile `nvidia-12gb` (tier 1 on the GPU at 32k context with a q8_0 KV cache, tier 0 on the CPU at 16k) |
| Tier 1 | Qwen3.5-9B-Q6_K.gguf from `lmstudio-community/Qwen3.5-9B-GGUF` (SHA-256 `b2ccc477…d77773e6`, Apache-2.0), or Qwen3-4B-Q4_K_M.gguf |
| Tier 0 | Qwen3-4B-Q4_K_M.gguf in both, not the profile's Q8_0 candidate |
| OpenCode | 1.19.5, builder agent as configured (`default` arm) |
| Request | "The tests in the sandbox workspace fail. Find the bug, fix it, and show me that the tests pass." (`slug` has its own; see its `fixture.toml`) |

## How it was run

`python evals/run.py run` with the `default` arm, which scores each run by applying its exported
diff to a clean copy of the fixture and running `pytest` there.

1. **[R1](R1-tier1-4b/), Qwen3-4B:** a 10-round batch, stopped after 19 runs to save time: 4 runs
   of each fixture, 3 of `slug`.
2. Tier 1 was then restarted on Qwen3.5-9B; tier 0 kept running.
3. **[R2](R2-tier1-9b/), Qwen3.5-9B:** one run of each fixture.
4. **[R3](R3-tier1-9b-chunks-slug/), Qwen3.5-9B:** five more runs each of `chunks` and `slug`, the
   fixtures where the two models differed.

Each folder holds the eval's own output: `manifest.json` (commit, OpenCode version, the model each
tier was serving), `runs.jsonl` (one line per run), `summary.md`, the OpenCode config the builder
ran with, every transcript, and every exported patch exactly as Git wrote it. A `.gitattributes`
here keeps the CRLF patches CRLF. The only change to the transcripts is that local paths would be
replaced with `<repo>` and `<home>`; none appeared.

## What the failures were

- **Qwen3-4B on `chunks` (0/4):** every run applied an edit, and `pytest` failed after it. Two runs
  changed only the comparison and missed the dropped last chunk, the half fix seen in the
  [replace_in_file A/B](../replace-in-file-2026-11-14-windows11-rtx4070/README.md#r3-ab-four-builder-setups-on-opencode-1195).
  The other two also added the missing `result.append(current)`, but inside the loop at the wrong
  indentation, which broke the file.
- **Qwen3-4B on `slug` (0/3):** two runs created `slug.py` with code that replaced characters one by
  one and failed all three tests. The third wrote the module to a file named `slug`, without
  `.py`, so the import still failed.
- **Qwen3-4B on `cart` (2/4):** one run changed only `cart.py`, rounding there and still ignoring
  the quantity; in the other, no edit applied.
- **Qwen3.5-9B on `slug`, R2 (1 failure):** it created `slug.py`, but collapsed repeated hyphens
  with `"-".join(slug.split("-"))`, which keeps them, so `"Hello, World!"` became `hello--world`.
  It then reran the same failing check until the `retry` breaker stopped it. All five `slug` runs
  in R3 used a regular expression and passed.

## Resources

Taken from the llama-server logs and `nvidia-smi` during the runs:

| | Qwen3.5-9B Q6_K (tier 1, GPU) | Qwen3-4B Q4_K_M (tier 0, CPU) |
|---|---|---|
| Generation | about 51 tokens/s | about 8 tokens/s |
| Prompt processing | about 860 tokens/s | about 21 tokens/s |
| GPU memory | 8.8 GB peak for the whole card, including the Windows desktop; about 7.3 GB for tier 1 | none |

These numbers are in the profile's `[measured]` block. With the 4B on tier 1, the card peaked at
6.6 GB, so the 9B costs about 2.2 GB more and leaves about 3.5 GB free. That is enough for
Firefox, but probably not for a game alongside it.

## What this shows and what it doesn't

- On this machine, the profile's recommended tier 1 model fixes the harder fixtures that the 4B
  never fixed. That is the main reason to use it, and it's why the 0.2 docs should stop presenting
  the 4B as enough for coding.
- **The samples are small.** `chunks` and `slug` have six 9B runs each; the other three fixtures
  have one. These runs show a clear difference on the two hard fixtures, not a success rate for
  each fixture.
- **The batches ran one after the other**, not interleaved, because changing tier 1's model means
  restarting it. The 4B ran first, within an hour of the 9B. The card's peak stayed flat within each
  batch (6.6 GB with the 4B, 8.8 GB with the 9B), with no sign of other GPU work joining partway.
- **The profile stays `tested = false`.** By the rule in
  [MODEL-LOADOUTS.md](../../MODEL-LOADOUTS.md#contributing-a-profile), that waits for a second person
  to reproduce these numbers. Tier 0 also ran a smaller model than the profile names.
- Not covered: Qwen2.5-Coder-14B (the profile's second candidate), OpenCode 2.3.0, other GPUs, and
  Linux.
