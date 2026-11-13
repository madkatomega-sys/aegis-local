# Model loadouts by GPU

Aegis-Local doesn't ship model weights and doesn't require any particular model. This page
explains how to pick models for each tier and lists starting points per hardware class. Each
profile in [`config/profiles/`](../config/profiles/) encodes one row of these tables.

## How the tiers are used

| Tier | Alias | Job | What matters |
|---|---|---|---|
| 0 | `aegis-extractor`, `aegis-fast` | Reads untrusted text (issues, web pages, unfamiliar files) with **no tools**, and returns schema-constrained JSON. Also handles quick lookups and summaries. | Instruction following and JSON reliability. Speed. It can run on the CPU. |
| 1 | `aegis-reasoner` | Planning, code synthesis, setting up math and physics for the symbolic tools, drafting prose. | Reasoning and coding quality. Context length. |
| 2 | (tools, not a model) | Exact math, units, numerics, compilation, tests, citation checks. | Determinism. Lives in the sandbox workers. |

## Picking a quantization

| Quant | Use it when |
|---|---|
| `Q8_0` | Small models (≤4B) where the extra memory is cheap and accuracy matters most. |
| `Q6_K` | Small-to-mid dense models with headroom to spare. |
| `Q5_K_M` / `Q4_K_M` | The default for mid and large dense models. |
| `IQ4_XS` | Squeezing a MoE or large dense model into a tight VRAM budget. |
| `Q4_0` (QAT) | When the publisher released quantization-aware trained weights; these hold up better than post-hoc Q4. |

Budget for the KV cache as well as the weights: at 32k context, a mid-size model's cache can take
1–3 GB even when quantized to `q8_0`.

## Starting points

Size classes are the stable part of each row. Model names are candidates that were current when
this page was written. Before a profile is marked `tested = true`, a maintainer checks the model's
availability, its license, and its results on the harness's own tool-call and verification tests.

| Hardware | Backend (Windows / Linux) | Tier 0 | Tier 1 | Placement |
|---|---|---|---|---|
| **NVIDIA 8 GB** (RTX 3060 Ti, 4060) | CUDA / CUDA | 1–4B dense Q6 on CPU | 7–9B dense Q4_K_M | Tier 1 on GPU, Tier 0 in RAM |
| **NVIDIA 12 GB** (RTX 3060 12G, 4070, 5070) | CUDA / CUDA | 4B dense Q8_0 on CPU (e.g. Qwen3.5-4B) | 9–14B dense Q4–Q6 (e.g. Qwen3.5-9B Q6_K) | Tier 1 on GPU, Tier 0 in RAM |
| **NVIDIA 16 GB** (RTX 4080, 5070 Ti, 5080) | CUDA / CUDA or vLLM | 4B dense Q8_0 | 20–30B MoE IQ4_XS (e.g. Gemma 4 26B-A4B) | Tier 1 on GPU, Tier 0 on GPU if it fits, else RAM |
| **NVIDIA 24–32 GB** (RTX 3090, 4090, 5090) | CUDA / CUDA or vLLM | 9B dense Q6_K | 27–32B dense Q4_K_M–Q5_K_M (e.g. Qwen3.5-27B, Gemma 4 31B) | Both resident on GPU |
| **AMD 16 GB** (RX 9070 XT, 7800 XT) | ROCm HIP or Vulkan / ROCm or Vulkan | 4B dense Q8_0 | 20–30B MoE IQ4_XS | Tier 1 on GPU |
| **AMD 24 GB** (RX 7900 XTX) | ROCm HIP or Vulkan / ROCm or Vulkan | 9B dense Q6_K | 27–32B dense Q4_K_M | Both resident |
| **AMD unified memory** (Ryzen AI Max+ 395, 96–128 GB) | ROCm or Vulkan / Vulkan RADV or ROCm | 9B dense Q8_0 | 30–35B MoE Q6_K (e.g. Qwen3.5-35B-A3B) or 100B+ MoE Q4_K_M (e.g. Qwen3.5-122B-A10B) | Both resident; raise the GPU memory allocation in BIOS/driver |
| **Intel Arc 12 GB** (B580) | SYCL or Vulkan / SYCL or Vulkan | 4B dense Q6_K on CPU | 9–14B dense Q4_K_M | Tier 1 on GPU |
| **Intel Arc 16 GB** (A770) | SYCL or Vulkan / SYCL or Vulkan | 4B dense Q6_K | 20–30B MoE IQ4_XS | Tier 1 on GPU |
| **CPU only** | CPU / CPU | 1–4B dense Q4_K_M | 7–9B dense Q4_K_M | Everything in RAM; use for CI and evaluation |

## Contributing a profile

1. Copy the closest file in `config/profiles/` and rename it for your hardware.
2. Fill in the models you used and the measured numbers in `[measured]`: tokens per second for
   each tier at your context size, and peak VRAM.
3. Run `python aegis.py up --profile <name>` and the router's smoke test.
4. Open a pull request using the **GPU profile** issue template's checklist.

Profiles stay `tested = false` until a second person reproduces the numbers.
