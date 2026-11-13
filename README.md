# Aegis-Local

**A deterministic harness for local AI.** Aegis-Local runs open-weight language models on your
own Windows 11 or Linux PC and puts them inside a control plane they can't talk their way out of:
constrained output, an out-of-process tool firewall, loop breakers, and sandboxed verifiers for
code, math, physics, and writing.

[![CI](https://github.com/madkatomega-sys/aegis-local/actions/workflows/ci.yml/badge.svg)](https://github.com/madkatomega-sys/aegis-local/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)

> **Status: pre-alpha.** The architecture, configuration schemas, and control-plane logic (loop
> detection, taint tracking, approvals, tier routing) are in place and unit-tested, and the guard
> proxy has been exercised against the math worker over real MCP. Container images, the full
> stack with a model, and the OpenCode integration haven't been verified end to end yet. See the
> [plan](docs/PLAN.md#9-milestones-and-acceptance-criteria) for what's next, and the
> [contributing guide](CONTRIBUTING.md) if you'd like to help build it.

---

## Why

Language models predict plausible text. That makes them useful, and it also means they:

- **follow instructions hidden in data** they were only supposed to read (prompt injection),
- **can't tell they're stuck**, so they retry the same failing step indefinitely,
- **can't verify themselves**: asking "are you sure?" gets another plausible answer, not a check.

These aren't bugs a bigger model will fix. They mirror results from the foundations of computing:
instructions and data sharing one tape, the undecidability of halting, and the limits of
self-verification. Aegis-Local's answer is architectural. Treat the model as a fast, untrusted
coprocessor, and make every decision that matters in deterministic code outside it.

## What you get

| Component | What it does | Why |
|---|---|---|
| **Native inference** (`llama-server`) | Runs models on your GPU (CUDA, ROCm, SYCL, Vulkan) with grammar-constrained output | Malformed tool calls become impossible to emit; full native GPU speed on any OS |
| **Model router** (`:4000`) | OpenAI-compatible endpoint with stable aliases routed to a fast tier and a reasoning tier | Right-size the model per task; swap models without touching your IDE config |
| **MCP guard proxy** (`:4100/mcp`) | The only tool server your IDE sees. Hashes every call, trips circuit breakers on loops, labels and quarantines untrusted content, holds risky actions for your approval | Safety enforced out of process, where neither the model nor the IDE can bypass it |
| **Sandboxed verifiers** | `code-worker` (tests, types, lint), `math-physics-mcp` (SymPy, pint, SciPy), `writing-auditor` (citation hashes) | Answers are checked by compilers, algebra systems, and source hashes rather than taken on trust |
| **OpenCode integration** | Drop-in config, least-privilege agents, a status plugin | A comfortable daily workflow with diffs, LSP, and GitHub |

```text
OpenCode ──► model-router :4000 ──► llama-server (native GPU, one per tier)
    │
    └──────► mcp-guard-proxy :4100/mcp ──► code-worker │ math-physics-mcp │ writing-auditor
                                            (internal network, no internet route)
```

## Quick start

> These commands describe the target workflow. `doctor` and `profiles` are tested; `up`
> and the workspace commands are written but not yet verified on real hardware.

```bash
git clone https://github.com/madkatomega-sys/aegis-local.git
cd aegis-local
cp .env.example .env
python aegis.py doctor
python aegis.py up --profile nvidia-12gb
opencode
```

Requirements: Python 3.11+, Docker or Podman, a `llama-server` build for your GPU, and GGUF model
files of your choice. Platform details are in [docs/PLATFORMS.md](docs/PLATFORMS.md).

## Hardware profiles

Profiles in [`config/profiles/`](config/profiles/) describe which model size class goes on which
tier for a given GPU. Any model llama.cpp can load will work; profiles are starting points, not
requirements.

| Profile | Hardware |
|---|---|
| `cpu-only` | No GPU (testing, CI) |
| `nvidia-8gb`, `nvidia-12gb`, `nvidia-16gb`, `nvidia-24gb-plus` | GeForce RTX 30/40/50 series |
| `amd-16gb`, `amd-24gb`, `amd-unified-128gb` | Radeon RX 7000/9000, Ryzen AI Max |
| `intel-arc-12gb`, `intel-arc-16gb` | Arc B580, A770 |

See [docs/MODEL-LOADOUTS.md](docs/MODEL-LOADOUTS.md) for the reasoning behind each one.

## Repository layout

```text
aegis-local/
├── aegis.py                  Cross-platform launcher (standard library only)
├── docker-compose.yml        Router, guard proxy, and sandboxes
├── .env.example              Backend, profile, and token settings
├── config/
│   ├── router.yaml           Model aliases, tiers, routing rules, token caps
│   ├── policy.yaml           Loop breaker, budgets, taint and approval rules
│   ├── mcp-servers.yaml      Upstream tool servers and their trust levels
│   ├── opencode.json         Drop-in OpenCode configuration
│   ├── agents/               planner, builder, verifier, essayist prompts
│   └── profiles/*.toml       GPU and model loadouts
├── services/
│   ├── model-router/         OpenAI-compatible tier router (FastAPI)
│   ├── mcp-guard-proxy/      MCP gateway: breaker, taint, quarantine, approvals, audit log
│   └── sandboxes/
│       ├── code-worker/      Git worktree, pytest, ruff, pyright
│       ├── math-physics-mcp/ SymPy, pint, SciPy
│       └── writing-auditor/  Vault index and citation checks
├── .opencode/plugins/        status display (informational only)
├── vault-example/            sample notes for the writing auditor
├── docs/                     Plan, threat model, platforms, model loadouts
└── tests/                    Unit tests for the control plane
```

## Documentation

- [Project plan](docs/PLAN.md): the why and how of every component, milestones, and open questions
- [Threat model](docs/THREAT-MODEL.md): what the harness defends against, and what it doesn't
- [Platform setup](docs/PLATFORMS.md): Windows 11 and Linux
- [Model loadouts](docs/MODEL-LOADOUTS.md): picking models and quantizations for your GPU

## Contributing

Contributions are welcome, from bug reports to new sandboxes. Good places to start:

- **GPU profiles**: test a profile on your hardware and submit measured numbers.
- **Sandboxes**: a Lean 4 prover, a Rust/C++ build worker, a LaTeX checker.
- **Cross-OS testing**: Podman on Windows, rootless Podman on Linux, unusual distributions.
- **Agent definitions**: least-privilege OpenCode agents for scientific computing, auditing, and
  technical writing.

Read [CONTRIBUTING.md](CONTRIBUTING.md) first. Commits need a `Signed-off-by` line
([DCO](https://developercertificate.org/)); there is no CLA. Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md), and report security issues privately as described in
[SECURITY.md](SECURITY.md).

## License

Copyright 2026 the Aegis-Local contributors.

Licensed under the [Apache License, Version 2.0](LICENSE). You may use, modify, and distribute
this project, including commercially, under its terms. See [NOTICE](NOTICE) for attribution.

Aegis-Local does not include model weights. Models you download are governed by their own
licenses, which you accept separately. Third-party tools the containers install (llama.cpp,
SymPy, pint, SciPy, FastAPI, and others) keep their own licenses; all are permissive and
compatible with Apache-2.0.
