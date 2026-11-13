# Aegis-Local: Project Plan

This document is the plan of record. It explains what Aegis-Local is, why each part exists,
how the parts fit together, and what has to be true before each stage counts as done. It
deliberately has no calendar dates. Stages are ordered by dependency, not by deadline.

- [1. Why this project exists](#1-why-this-project-exists)
- [2. Goals and non-goals](#2-goals-and-non-goals)
- [3. The end product](#3-the-end-product)
- [4. Architecture](#4-architecture)
- [5. The five build stages](#5-the-five-build-stages)
- [6. Cross-platform design (Windows 11 and Linux)](#6-cross-platform-design-windows-11-and-linux)
- [7. Hybrid model router and GPU loadouts](#7-hybrid-model-router-and-gpu-loadouts)
- [8. Build strategy: scratch vs. OpenCode plugin vs. hybrid](#8-build-strategy-scratch-vs-opencode-plugin-vs-hybrid)
- [9. Milestones and acceptance criteria](#9-milestones-and-acceptance-criteria)
- [10. Design corrections made while scaffolding](#10-design-corrections-made-while-scaffolding)
- [11. Risks and open questions](#11-risks-and-open-questions)
- [12. Licensing](#12-licensing)

---

## 1. Why this project exists

Martin Davis's *The Universal Computer* traces the computer back to a problem in logic, not
arithmetic. Leibniz wanted a language in which arguments could be settled by calculation.
Boole and Frege turned inference into rules for moving symbols around. Turing showed that one
simple machine could imitate any other if the other machine's rules were written onto the same
tape as its data. Gödel and Turing then proved where that program stops: some true statements
can't be proved inside a system, and no program can decide, in general, whether another program
will halt.

Three of those results describe today's AI agents with uncomfortable precision.

| Classical result | What it means for an LLM agent | What we build in response |
|---|---|---|
| Instructions and data share one tape | System prompts, user requests, and untrusted web or repo text all arrive in one token stream. Text that *looks* like an instruction can act like one (prompt injection). | Keep the control plane out of the model. Label untrusted data at the boundary and quarantine it before a privileged model sees it. |
| Halting is undecidable | A model can't reliably tell from inside its own reasoning that it is stuck. It will retry slight variations of a failing step indefinitely. | Detect loops *outside* the model, in deterministic code that hashes every tool call and enforces hard budgets. |
| A system can't certify itself | Asking a model "are you sure?" produces another plausible completion, not a check. | Every claim that can be checked is checked by something that isn't a language model: a compiler, a test suite, a computer algebra system, a unit checker, a source hash. |

The thesis of the project: **treat the language model as a fast, useful, untrusted coprocessor,
and put it inside a deterministic harness that the model cannot talk its way out of.** Nothing in
this design depends on models getting smarter. A better model makes the harness more productive;
it doesn't make the harness unnecessary.

## 2. Goals and non-goals

**Goals**

1. Run entirely on one local machine (Windows 11 or Linux), with no cloud dependency required.
2. Enforce safety and reliability *out of process*, at network and protocol boundaries the model
   and the IDE can't bypass.
3. Make four kinds of work trustworthy: **coding, math, physics, and writing**, each paired with
   a deterministic verifier.
4. Stay model-agnostic. Any model that llama.cpp (or another OpenAI-compatible server) can serve
   should work. GPU profiles are suggestions, not requirements.
5. Stay IDE-agnostic at the core. OpenCode is the first-class frontend, but the router speaks the
   OpenAI API and the guard speaks standard MCP, so other clients can attach.
6. Be redeployable from a Git clone with one command, and identical in behavior across operating
   systems.
7. Be a healthy open-source project: permissive license, clear contribution path, reviewable code.

**Non-goals**

- Training or fine-tuning models.
- Replacing OpenCode or building a new IDE.
- Claiming hallucination is "eliminated." The harness makes unverified output *visible* and makes
  malformed or unauthorized actions *impossible to execute*. A fluent wrong paragraph of prose can
  still be written; the writing auditor's job is to flag it.
- Multi-user or server deployment. This is a single-user workstation tool. Everything binds to
  loopback.

## 3. The end product

A single Git repository that, after `python aegis.py up`, gives a user:

- A **local inference engine** (llama.cpp `llama-server`) running natively on their GPU, with
  grammar and JSON-schema constrained output.
- A **model router** on `127.0.0.1:4000` that exposes stable model aliases
  (`aegis-reasoner`, `aegis-fast`, `aegis-extractor`) and sends each request to the right tier.
- An **MCP guard proxy** on `127.0.0.1:4100/mcp` that is the *only* MCP server the IDE talks to.
  It fronts every tool server, hashes every call, labels untrusted output, quarantines it, trips
  circuit breakers on loops, and holds destructive actions for human approval.
- **Sandboxed tool workers** for code, math/physics, and writing, on a container network with no
  route to the internet.
- A **drop-in OpenCode configuration** with least-privilege agents (planner, builder, verifier,
  essayist) and a small status plugin.

## 4. Architecture

```text
┌──────────────────────────────── HOST (Windows 11 or Linux) ─────────────────────────────────┐
│                                                                                             │
│   OpenCode (TUI / desktop)                         llama-server (native GPU: CUDA / ROCm /  │
│   config/opencode.json                             SYCL / Vulkan / Metal), one per tier     │
│        │ OpenAI API          │ MCP (Streamable HTTP)          ▲                             │
│        │ 127.0.0.1:4000      │ 127.0.0.1:4100/mcp             │ host.docker.internal        │
└────────┼─────────────────────┼────────────────────────────────┼─────────────────────────────┘
         │                     │                                │
┌────────┼─────────────────────┼──────── CONTAINER RUNTIME ─────┼─────────────────────────────┐
│        ▼                     ▼                                │                             │
│   ┌───────────────┐    ┌──────────────────────┐               │                             │
│   │ model-router  │◄───┤ mcp-guard-proxy      │   network: inference (host access only)     │
│   │ tier select,  │────┼──────────────────────┼───────────────┘                             │
│   │ budgets,      │    │ cycle breaker        │                                             │
│   │ schema inject │    │ taint + quarantine   │── network: egress ──► GitHub MCP (optional) │
│   └───────────────┘    │ approvals, audit log │                                             │
│                        └──────────┬───────────┘                                             │
│                                   │ network: sandbox (internal, no internet route)          │
│              ┌────────────────────┼─────────────────────┐                                   │
│              ▼                    ▼                     ▼                                   │
│       code-worker          math-physics-mcp      writing-auditor                            │
│       git worktree,        SymPy, pint,          vault index (SQLite FTS5),                 │
│       pytest, ruff,        SciPy                 citation hash checks                       │
│       pyright, cargo                                                                        │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### The planes

| Plane | Components | Trust | Who can change its behavior |
|---|---|---|---|
| Control | `aegis.py`, `mcp-guard-proxy`, `model-router`, `config/*.yaml` | Trusted, deterministic | Only the user, by editing config |
| Inference | `llama-server` tiers | Untrusted output, stateless | Nobody at runtime; it only maps tokens to tokens |
| Execution | sandbox workers | Untrusted input, contained | Nobody; workers run what the guard forwards, inside limits |
| Frontend | OpenCode, plugin, agents | Convenience only | The user; **never relied on for enforcement** |

The rule that holds the design together: **a decision that matters for safety is made by the
control plane, never by a model and never by the IDE.**

### A request, end to end

1. The user asks `@planner` in OpenCode to fix a failing test referenced in a GitHub issue.
2. OpenCode calls the GitHub tool through the guard proxy. The guard forwards it, receives the
   issue text, and labels it **TAINTED** (source: `github`).
3. Because the session holds write-capable tools, the guard does not return the raw issue text.
   It sends the text to the router's `aegis-extractor` alias (Tier 0, no tools, JSON-schema
   constrained) and returns only the structured extraction, still labeled as tainted-derived.
4. The planner (Tier 1) writes a plan and hands it to the builder, which calls
   `code__apply_patch`. The guard checks the session's taint level (tainted, so mutating calls
   need approval) and holds the call until the user runs `python aegis.py approve <id>`. When the
   approved call is repeated, the guard checks its hash against the session history (no loop)
   and lets it through once.
5. After approval, `code-worker` applies the patch to its own worktree and runs the test suite.
   A non-zero exit code is returned as a failure, not as a suggestion to try harder.
6. If the planner retries the same failing patch, or alternates between two patches, the cycle
   breaker trips and the session stops with the trace attached.
7. When tests pass, `aegis workspace export` writes the verified diff back to the user's checkout.

## 5. The five build stages

Each stage closes one failure mode. Stages are listed in dependency order.

### Stage 1: Constrained inference substrate

- **What:** A host-native `llama-server` per resident tier, started by `aegis.py` from a GPU
  profile. Flash attention on, KV cache quantized (`q8_0`) where the backend supports it,
  bound to loopback.
- **Why:** Free sampling lets a model emit broken JSON, invented flags, and malformed diffs.
  Grammar-constrained decoding masks every token that would violate the grammar before sampling,
  so invalid tool-call syntax cannot be produced. Running natively (not in a container) gives full
  GPU speed on every vendor and avoids WSL2 GPU passthrough gaps on Windows.
- **How:** `config/profiles/*.toml` describe each tier: model file, quantization, context size,
  GPU layers, port. `aegis.py up` picks the backend binary for the OS, launches the tiers, waits
  for `/health`, and records PIDs under `.aegis/run/`.
- **Done when:** `aegis.py doctor` reports the GPU backend, and each tier answers a
  schema-constrained request with valid JSON 100 times out of 100.

### Stage 2: Hybrid model router

- **What:** A small FastAPI service (`services/model-router`) exposing the OpenAI API with stable
  aliases, forwarding to tier endpoints.
- **Why:** A large model on every trivial request wastes time and VRAM; a small model on calculus
  or a multi-file refactor makes quiet mistakes. Stable aliases also mean the IDE config never
  changes when the user swaps models.
- **How:** `config/router.yaml` maps aliases and task hints to tiers. Selection order, all
  deterministic: explicit alias → `X-Aegis-Task` header → prompt-size threshold → default tier.
  The router enforces per-request token caps and injects a JSON schema for the extractor alias.
  Tier 2 (symbolic oracles) is not a model at all: math, units, and compilation always go to
  sandbox tools, never to model weights.
- **Done when:** Routing decisions are unit-tested, streaming passes through unchanged, and
  swapping a profile requires no OpenCode change.

### Stage 3: Control/data firewall and loop breaker

- **What:** `services/mcp-guard-proxy`, a Streamable HTTP MCP gateway. The IDE sees one MCP
  server; the guard fans out to the real ones and prefixes their tools (`math__solve`,
  `code__run_tests`).
- **Why:** Prompt injection is the unified tape again. Loop detection can't live in a prompt
  because the model can't see that it is looping. Both checks have to sit outside the model.
- **How:**
  - **Canonical call hashing.** Every `tools/call` is reduced to `(tool, canonical JSON args)` and
    hashed. Repeats, A→B→A→B oscillation, and runs of near-identical *failing* calls trip the
    breaker. Near-identical is measured with deterministic text similarity by default; an
    embedding model can be plugged in later.
  - **Budgets.** Hard caps on tool calls per session and on consecutive failures.
  - **Taint labels.** Each upstream server has a trust level in `config/mcp-servers.yaml`. Output
    from `untrusted` sources marks the session tainted (a high-water mark that never goes back
    down within a session).
  - **Quarantine.** Raw untrusted output is passed through the Tier 0 extractor with a fixed
    schema before it is returned, so a privileged model reads fields, not free text.
  - **Approvals.** Tools marked `mutating` require out-of-band approval when the session is
    tainted, or always, per policy. Approval happens through `aegis approve`, not through the
    model.
  - **Audit log.** Every call, decision, and result hash goes into SQLite.
- **Done when:** Breaker and taint logic have full unit coverage; a scripted prompt-injection test
  (malicious text in a fixture issue) cannot cause a mutating call without approval.
- **Known limit:** This is session-level taint tracking, a simplified cousin of the
  capability-based designs in Google DeepMind's CaMeL and Microsoft's FIDES. Full per-value
  tracking needs the planner to emit a program the harness interprets. That is a research
  milestone, not a launch requirement.

### Stage 4: Ephemeral execution sandboxes

- **What:** Three MCP workers, each a container with a read-only root filesystem, `tmpfs` scratch,
  dropped capabilities, `no-new-privileges`, CPU/memory/PID limits, and **no route to the
  internet**.
  - `code-worker`: a Git worktree in a named volume; `pytest`, `ruff`, `pyright`, and other
    allowlisted commands; patch apply and diff.
  - `math-physics-mcp`: SymPy for exact algebra, calculus, and ODEs; pint for dimensional checks;
    SciPy for numerics.
  - `writing-auditor`: indexes a read-only notes vault into SQLite FTS5 (BM25), hashes every chunk,
    and verifies that quoted citations match their source chunk byte for byte.
- **Why:** Even a well-meaning model can run a destructive command on the wrong path. And the
  model should never do mental math: numeric and symbolic claims must come from tool output.
- **How:** Workers expose MCP over HTTP only on the internal `sandbox` network. The user's repo is
  copied into a volume (`aegis workspace load`), never bind-mounted read-write, so the agent can't
  touch the host checkout and Windows avoids slow cross-filesystem I/O.
- **Done when:** Each worker passes its own tests, can't resolve or reach external hosts, and a
  write outside its volume or `tmpfs` fails.

### Stage 5: IDE and GitHub integration

- **What:** `config/opencode.json`, agent prompts in `config/agents/`, and a status plugin in
  `.opencode/plugins/`.
- **Why:** A deterministic backend is only useful if daily work feels normal: diffs, LSP
  diagnostics, chat, PRs.
- **How:** OpenCode points at the router as an OpenAI-compatible provider and at the guard as its
  only MCP server. OpenCode's own host-side `edit`, `bash`, and `webfetch` tools are turned off,
  so every action goes through the guard. Agents are least-privilege: the planner can't write,
  the builder changes only the sandboxed workspace, the verifier can only call verification
  tools, and the essayist can only cite indexed sources. GitHub access goes through the guard
  with a fine-grained token, and GitHub content is always labeled untrusted.
  `python aegis.py opencode` starts OpenCode with `OPENCODE_CONFIG` and the client token set.
- **Done when:** A fresh clone plus `aegis.py up` plus `opencode` works on both operating systems
  with the same config files.

## 6. Cross-platform design (Windows 11 and Linux)

One architectural choice makes portability straightforward: **inference runs natively on the
host; everything else runs in containers.**

| Concern | Decision | Reason |
|---|---|---|
| GPU access | `llama-server` on the host, never in a container | WSL2 GPU passthrough works well for CUDA but is uneven for ROCm and SYCL. Native binaries work for every vendor. |
| Container runtime | Docker Desktop or Podman Desktop on Windows (WSL2); Docker Engine or rootless Podman on Linux | Same Compose file everywhere. |
| Host reachability | `extra_hosts: host.docker.internal:host-gateway` | Supported by Docker and Podman on both OSes. See open question on Linux loopback binding. |
| MCP transport | Streamable HTTP, not stdio | stdio framing between Windows consoles and Linux containers is fragile. HTTP keeps `opencode.json` byte-identical across OSes. |
| Launcher | One Python CLI (`aegis.py`) using only the standard library | No drift between `.sh` and `.ps1` scripts; no `pip install` needed before first run. Profiles use TOML because Python reads it natively. |
| Line endings | `.gitattributes` forces LF | Git for Windows' `autocrlf` breaks container entrypoints. |
| Workspace I/O | Repo copied into a named volume | Bind-mounting `C:\` paths into WSL2 containers is slow and would let the agent touch the host checkout. |
| Network exposure | Every published port binds to `127.0.0.1` | An unauthenticated inference or tool endpoint must never be reachable from the LAN. |
| Paths in citations | POSIX `vault://` URIs | Citation hashes stay valid if the vault moves between machines. |

## 7. Hybrid model router and GPU loadouts

The router is model-agnostic. Profiles are written in terms of **size classes** so they age well;
the model names below are candidates that were current when this plan was written and should be
re-checked (availability, license, and benchmark results) before a profile is promoted to
"tested". Full tables live in [MODEL-LOADOUTS.md](MODEL-LOADOUTS.md).

| Hardware class | Tier 0 (extractor / fast) | Tier 1 (reasoner) | Placement strategy |
|---|---|---|---|
| NVIDIA 8 GB | 1–4B dense, Q4–Q6 (CPU) | 7–9B dense, Q4 | Tier 1 on GPU, Tier 0 in system RAM |
| NVIDIA 12 GB | 4B dense, Q6–Q8 (CPU) | 9–14B dense, Q4–Q6 | Tier 1 on GPU, Tier 0 in system RAM |
| NVIDIA 16 GB | 4B dense, Q8 | 20–30B MoE (3–4B active), IQ4 | MoE gives large-model quality at small-model speed |
| NVIDIA 24–32 GB | 9B dense, Q6 | 27–32B dense, Q4–Q5 | Both tiers resident on GPU |
| AMD 16–24 GB discrete | 4B dense, Q8 | 16 GB: 20–30B MoE; 24 GB: 27–32B dense | ROCm HIP build, Vulkan fallback |
| AMD unified-memory APU (96–128 GB) | 9B dense, Q8 | 30–35B MoE or 100B+ MoE, Q4–Q6 | Large sparse MoE suits high capacity, modest bandwidth |
| Intel Arc 12–16 GB | 4B dense, Q6 | 12 GB: 9–14B dense; 16 GB: 20–30B MoE | SYCL build, Vulkan fallback |
| CPU only | 1–4B dense, Q4 | 7–9B dense, Q4 | Slow but functional; good for CI and testing |

## 8. Build strategy: scratch vs. OpenCode plugin vs. hybrid

| Option | Gains | Costs | Verdict |
|---|---|---|---|
| A. Custom harness and custom UI | Total control | Rebuild diffs, LSP, file trees, sessions, keybindings before any real work | Too much undifferentiated work |
| B. OpenCode plugin only | Fast to build, great UX | Enforcement lives inside the process being guarded. Hooks may not cover every path (subagent sessions, MCP calls), and a plugin can be disabled or bypassed by a misconfigured agent. | Not a security boundary |
| C. **Hybrid: OpenCode frontend, out-of-process kernel** | Good UX for free; guarantees hold even if the frontend misbehaves | Two moving parts to keep in sync | **Chosen** |

Under option C, the OpenCode plugin is cosmetic (status and visibility). If it is removed, nothing
about safety changes. That is the test of a correct split.

## 9. Milestones and acceptance criteria

| Milestone | Contents | Exit criteria |
|---|---|---|
| **M0 Foundation** | License, README, governance and contribution docs, this plan, repository layout, config schemas, service skeletons, unit tests, CI | CI green; `docker compose config` validates; docs reviewed |
| **M1 Inference** | `aegis.py doctor/up/down/status`, profile loader, native launch on both OSes | One profile verified per GPU vendor available to maintainers |
| **M2 Router** | Alias routing, streaming pass-through, schema injection, token caps | Routing tests; OpenCode chats through the router |
| **M3 Guard** | MCP gateway, upstream sessions, breaker, taint, quarantine, approvals, audit log | Injection fixture test passes; breaker trips on scripted loops |
| **M4 Sandboxes** | code, math-physics, writing workers | Each worker's tests pass; egress test fails to connect, as intended |
| **M5 Integration** | OpenCode config, agents, status plugin, GitHub upstream | Clean-clone walkthrough on Windows 11 and Linux |
| **M6 Release 0.1** | Threat model review, signed container images, tested profile matrix, docs pass | Tagged release, published images, changelog |
| **Later** | Per-value taint (CaMeL-style planner programs), Lean 4 worker, NLI claim checker, Qdrant hybrid search, embedding-based similarity, vLLM backend | Proposed through issues and design docs |

## 10. Design corrections made while scaffolding

Writing the scaffold surfaced places where the original sketch didn't hold up. These are now part
of the plan.

1. **`--network=none` vs. HTTP MCP.** A container with no network can't serve HTTP to the guard.
   Sandboxes instead sit on a Compose network marked `internal: true`, which has no route out but
   lets the guard reach them. The guard is the only container on both that network and the egress
   network.
2. **Quarantine has to happen in the guard, not in the IDE.** OpenCode returns tool output straight
   into the calling agent's context. The only place to stop raw untrusted text is before the guard
   returns it, so quarantine is a guard feature that calls the extractor tier itself.
3. **Session-level taint, not per-value taint.** True information-flow tracking requires the
   planner to emit a program the harness runs. OpenCode doesn't work that way. Session-level taint
   is weaker but enforceable today, and it fails closed.
4. **The launcher has no third-party dependencies.** A user on a fresh Windows machine shouldn't
   have to `pip install` anything to run `aegis.py doctor`. Host profiles moved to TOML for this
   reason; service configs stay YAML.
5. **Model names are not hard-coded.** Profiles reference size classes and a model file path the
   user fills in, so a new model release never requires a code change.
6. **"Eliminate hallucination" became "make unverified output visible."** Constrained decoding
   eliminates malformed actions. Verifiers catch wrong code and wrong math. Prose can still be
   wrong; the auditor flags uncited claims rather than pretending to prevent them.
7. **OpenCode's host-side tools are off.** If OpenCode's built-in `edit`, `bash`, or `webfetch`
   stayed on, the model could change host files, run commands, or read the web without passing
   through the guard, which would make the whole control plane optional. Code changes happen in
   the sandbox and come back as a patch the user applies. A "relaxed mode" that allows host
   edits with OpenCode's own diff approval is a possible later option, clearly labeled as weaker.
8. **Agent prompts live in `config/agents/`, not `.opencode/agents/`.** OpenCode loads
   `.opencode/agents/` only when started inside this repository, but users run it in their own
   project folders. Defining agents in `config/opencode.json` (loaded through `OPENCODE_CONFIG`)
   works from any folder.
9. **The guard and its admin API need tokens.** Sandboxes share a network with the guard so it
   can reach them, which also means code running in a sandbox could reach the guard. A client
   token (held by OpenCode) and a separate admin token (held only by `aegis.py`) stop sandboxed
   code from calling tools or approving its own actions. One internal network per worker keeps
   workers from reaching each other.
10. **The math worker's per-call time limit moved to M4.** A process-wide CPU limit would
    eventually kill the server itself; the right fix is a worker process per evaluation.

## 11. Risks and open questions

| Risk or question | Current thinking |
|---|---|
| **Linux loopback.** On Linux, `host-gateway` resolves to the bridge address, so a `llama-server` bound to `127.0.0.1` isn't reachable from containers. | `aegis.py` binds to the bridge gateway address on Linux (reachable from host and containers, not the LAN). Needs testing on rootless Podman with `pasta`. |
| **Podman Desktop on Windows** reaching host loopback | Docker Desktop is known to work via `host.docker.internal`. Podman needs verification, possibly WSL mirrored networking. |
| **GitHub MCP transport.** The official server is stdio-first; a hosted HTTP endpoint also exists. | Config supports an HTTP upstream with bearer auth. A stdio bridge container is the fallback. |
| **Extractor quality.** A small model may drop facts while quarantining. | Extraction schema keeps a `verbatim_snippets` field capped in length and clearly marked as untrusted data; the user can widen it per server. |
| **Similarity threshold tuning.** | Start with deterministic `difflib` ratio at 0.92 and collect audit-log data before tuning. |
| **Approval fatigue.** Too many holds and users will disable them. | Policy distinguishes `always`, `when_tainted`, and `never`; defaults hold only destructive or external writes. |
| **Model licenses vary.** | The project ships no weights. Profiles name candidates only; users check and accept each model's license themselves. |
| **Project dependencies in an offline sandbox.** `code-worker` has no network, so it can't `pip install` a project's requirements. | Add a one-shot "prepare" container with network access that builds a virtual environment into the workspace volume, then run checks offline. |
| **OpenCode configuration details** (tool-name globs in agent configs, `{env:}` and `{file:}` substitution, plugin directory name) | Written against current OpenCode docs; verify against the installed version during M5 and pin a minimum OpenCode version. |
| **Model names in profiles.** | Candidates were current when written. Verify availability, license, and results before marking a profile `tested`. |

## 12. Licensing

Aegis-Local is licensed under the **Apache License 2.0**. The reasons:

- **It doesn't restrict what users connect.** Apache-2.0 is permissive. Users can point the
  harness at any model (open-weight or commercial endpoint) and any MCP server without the
  license reaching into those components. A copyleft license such as the GPL or AGPL would create
  questions for anyone combining the harness with differently licensed tools.
- **It's compatible with the stack.** OpenCode is MIT-licensed. The MCP specification and SDKs,
  llama.cpp (MIT), SymPy (BSD), pint (BSD), FastAPI (MIT), and httpx (BSD) are all permissive and
  compatible with Apache-2.0.
- **It protects contributors and users.** Apache-2.0 includes an explicit patent grant and a
  patent-retaliation clause, which matters for a project in an area with active patent filings.
- **Contributions stay simple.** Contributors certify the
  [Developer Certificate of Origin](https://developercertificate.org/) with a `Signed-off-by`
  line. There is no CLA to sign.

Model weights are not part of this repository and are not covered by its license.
