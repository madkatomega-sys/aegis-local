# The 8 GB and CPU-only profiles, measured on an RTX 4070

Until now only `nvidia-12gb` had numbers. This is the [eval suite](../../../evals/README.md) run on
`nvidia-8gb` and on `cpu-only`, each with its own catalog files (`download = ...` in the profile),
on the same machine the 12 GB profile was measured on.

**Result:** the builder fixed **17 of 18** runs on `nvidia-8gb` and **12 of 12** on `cpu-only`.
On the CPU the runs took about four times as long: a median of 267 s against 62 s.

| Fixture | `nvidia-8gb` | `cpu-only` |
|---|---|---|
| `calc`: one wrong line | 3/3 | 2/2 |
| `cart`: bugs in two files | 3/3 | 2/2 |
| `chunks`: two edits in one function, one an inserted block | 3/3 | 2/2 |
| `median-crlf`: one wrong line, CRLF file | 3/3 | 2/2 |
| `pace`: a units bug | 3/3 | 2/2 |
| `slug`: a missing module, so the fix is a new file | 2/3 | 2/2 |
| All | **17/18** | **12/12** |
| Median time per run | 62 s | 267 s |

| | |
|---|---|
| Code under test | `main` at `3bb1f00` (no uncommitted changes, as each `manifest.json` records) |
| Date | 2026-11-27 |
| Machine | Windows 11 Pro 26300, RTX 4070 12 GB, i9-9900K, 32 GB RAM, Docker Desktop |
| Inference | llama.cpp b12361 CUDA, at each profile's settings (16k context on both tiers, q8_0 KV cache on tier 1) |
| `nvidia-8gb` | tier 1 Qwen3.5-9B-Q4_K_M.gguf on the GPU (SHA-256 `cd76ec20…f15f2a13`); tier 0 Qwen3.5-4B-Q6_K.gguf on the CPU (`12a6d013…f9b7d1d9`) |
| `cpu-only` | tier 1 Qwen3.5-9B-Q4_K_M.gguf and tier 0 Qwen3.5-4B-Q4_K_M.gguf (`25082a7d…dfa1418c`), both on the CPU, with `CUDA_VISIBLE_DEVICES=-1` so llama-server couldn't see the GPU |
| Files | all from `lmstudio-community` (Apache-2.0), fetched with `python aegis.py models get --profile <name> --max-gb N` from a separate checkout, so `.env` was never changed |
| OpenCode | 1.19.5, builder agent as configured (`default` arm) |

## How it was run

For each profile, a measurement script:

1. stopped the tiers and read the GPU memory in use with none running (the desktop baseline);
2. ran `python aegis.py up --profile <name> --no-build` with `AEGIS_TIER0_MODEL` and
   `AEGIS_TIER1_MODEL` set in its environment to the profile's catalog files;
3. ran `python evals/run.py run --runs N` while sampling `nvidia-smi` every 2 s;
4. read the generation and prompt speeds from the llama-server log lines written during the runs.

`nvidia-8gb` had 3 runs of each fixture with a 900 s limit per run (24 minutes in all), and
`cpu-only` had 2 runs with a 2400 s limit (66 minutes). No run timed out.

Each profile's folder holds `measure.txt`, the script's record, and `evals/`, the eval's own
output: `manifest.json`, `runs.jsonl` (one line per run), `summary.md`, the OpenCode config the
builder ran with, every transcript, and every exported patch exactly as Git wrote it. Local paths
would be replaced with `<repo>`, `<home>` and `<scratchpad>`. In `measure.txt`, each list of GPU
processes is reduced to whether llama-server was on it, because the list names every program open
on the desktop. The docker progress lines were dropped.

**The `profile` field in each `manifest.json` says `nvidia-12gb`.** The eval reads the profile name
from `.env`, which was left alone, not from the `--profile` the stack was started with. The
`models` field next to it comes from llama-server itself and names the right files.

## The one failure

**`nvidia-8gb`, `slug-default-02`:** the code it wrote was right, with a regular expression for the
separators, but it created the module as `slugs.py` (its own comment at the top says `slug.py`).
The tests import `slug`, so `pytest` stopped with an import error. It then went on patching the
file's contents rather than its name, 14 `apply_patch` calls in 233 s, until the `retry` breaker
had stopped it four times. The other two `slug` runs created `slug.py` and passed.

## Resources

From the llama-server logs and `nvidia-smi`, during the runs:

| | `nvidia-8gb` tier 1 (9B Q4_K_M, GPU) | `nvidia-8gb` tier 0 (4B Q6_K, CPU) | `cpu-only` tier 1 (9B Q4_K_M, CPU) | `cpu-only` tier 0 (4B Q4_K_M, CPU) |
|---|---|---|---|---|
| Generation, median | 49.3 tokens/s (158 requests) | 5.0 tokens/s (18) | 4.3 tokens/s (74) | 7.2 tokens/s (12) |
| Prompt processing, median | 523 tokens/s | 13 tokens/s | 25 tokens/s | 19 tokens/s |

**GPU memory, `nvidia-8gb`:**

| | MiB |
|---|---|
| Desktop with no tiers running | 3311 |
| Both tiers loaded, idle | 9177 |
| Peak during the runs | 9582 |
| The tiers alone (peak minus baseline) | about 6270 (6.1 GiB) |

So the profile's files need about 6.1 GiB, which leaves about 1.9 GiB of an 8 GB card for
everything else. This desktop had a lot open (two browsers, Steam, a music player, Docker Desktop)
and used 3.2 GiB on its own, more than that. On a real 8 GB card, close the heavy programs or
lower tier 1's context; the 9B wouldn't have fit next to this desktop.

**`cpu-only`:** llama-server never showed up among the GPU's processes. The card's use went from
2993 MiB to a peak of 3642 MiB during the runs, and that came from the desktop's own programs.

These numbers are in each profile's `[measured]` block: generation speed for each tier, and for
`nvidia-8gb` the 6.1 GiB the tiers took.

## What this shows and what it doesn't

- On this machine, both profiles' catalog files work with the harness. The 8 GB profile's tier 1
  (the 9B at Q4_K_M) fixed the harder `chunks` and `slug` fixtures, like the 12 GB profile's 9B at
  Q6_K did ([evidence](../12gb-tier1-2026-11-17-windows11-rtx4070/README.md)).
- **An 8 GB card was not tested.** This is a 12 GB card, so the 8 GB fit is shown only by the
  tiers' own memory, not by running on one.
- **The samples are small, and smaller than planned.** The plan asked for `--runs 5` on
  `cpu-only`; these are 3 and 2 runs of each fixture, to keep the CPU run near an hour. They show
  that each profile works, not a success rate for each fixture.
- **CPU speed depends on the CPU.** This is an 8-core i9-9900K with 32 GB of RAM. A laptop CPU will
  be slower, and `cpu-only` needs about 9 GB of RAM for the two models.
- **Both profiles stay `tested = false`.** By the rule in
  [MODEL-LOADOUTS.md](../../MODEL-LOADOUTS.md#contributing-a-profile), that waits for a second person
  to reproduce these numbers.
- Not covered: OpenCode 2.3.0, Linux, other GPUs, and the eval's other arms.
