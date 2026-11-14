# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-13

The first release. The control plane (router, guard, sandboxes, launcher) is complete and
tested end to end on a real model. Expect configuration and APIs to change before 1.0.

### Added

- **Launcher** (`aegis.py`, standard library only): `doctor`, `profiles`, `up`, `down`,
  `status`, `pending`, `approve`, `deny`, `reset`, `workspace load` and `export`, `opencode`,
  `env`, and `--version`. `up --pull` runs the published, signed images instead of building.
- **Model router**: OpenAI-compatible endpoint with the `aegis-reasoner`, `aegis-fast`, and
  `aegis-extractor` aliases, task-header routing, prompt-size escalation, token caps, streaming
  pass-through, and a JSON schema forced on the extractor.
- **MCP guard proxy**: one Streamable HTTP MCP endpoint (protocol versions 2025-03-26 through
  2025-11-25) in front of every tool server; canonical call hashing; repeat, oscillation, and
  near-miss loop breakers; budgets; session taint; quarantine of untrusted output through the
  extractor; single-use approvals; SQLite audit ledger; client and admin tokens.
- **Sandboxes**: `code-worker` (workspace volume, allowlisted pytest, ruff, mypy, compileall),
  `math-physics-mcp` (SymPy, pint, SciPy; every computation in its own process with a
  wall-clock limit), and `writing-auditor` (FTS5 search, hashed chunks, quote verification,
  draft audit). Each runs read-only, without capabilities, on its own internal network.
- **Hardware profiles** for CPU, NVIDIA 8 to 24+ GB, AMD 16 GB, 24 GB, and 128 GB unified
  memory, and Intel Arc 12 and 16 GB.
- **OpenCode integration**: drop-in configuration, least-privilege agents, and a status plugin.
- **Tests**: unit tests for every policy decision; HTTP-level tests of the guard (including a
  scripted prompt-injection fixture) and router; a full-stack smoke test that runs in CI with a
  stub model and checks quarantine, approvals, the breaker, the math time limit, and sandbox
  isolation (no internet, no router, no peers, read-only root).
- **Release pipeline**: multi-architecture images on GHCR with SBOMs and build provenance,
  signed keylessly with Sigstore; release notes from this file.
- **Project documents**: plan, threat model, platform guide, model loadouts, governance,
  contributing guide (DCO), code of conduct, and security policy.

### Changed

- Container images use Python 3.15.
- The extractor schema bounds every field (`maxLength`, `maxItems`), and tier0's output cap is
  6,144 tokens, so a maximal extraction always finishes.
- The extractor alias turns model thinking off (`reasoning: off` in `config/router.yaml`), and
  every tier0 profile passes `--reasoning-budget 0`. Thinking models reason before the JSON
  grammar applies, which used up the token budget in validation.
- `aegis.py up` waits until every container is healthy before it returns.

### Fixed

Found by the hardware validation on Windows 11 with an RTX 4070 (see
[docs/validation](docs/validation/0.1.0-windows11-rtx4070/README.md)):

- `workspace load` failed with Git's "dubious ownership" error whenever the host user's ID
  differed from the sandbox's (always on Windows, usually on Linux).
- `aegis.py status` right after `up` could find the guard not yet listening.
- The circuit breaker stopped the ordinary fix loop: running a check, applying a patch, and
  running the same check again tripped the retry rule. A successful call to a tool marked
  `progress` in `config/policy.yaml` (applying a patch, resetting the workspace, reindexing the
  vault) now starts a new epoch for the loop rules. The budget and the failure streak still span
  the session, so repeated failed fix attempts still trip the breaker.
- `aegis.py deny` printed "denyd".

### Security

- Every published port binds to loopback, and llama-server binds to loopback (Windows) or the
  container bridge (Linux).
- The guard and its admin API require separate tokens that sandboxes never receive.

[Unreleased]: https://github.com/madkatomega-sys/aegis-local/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/madkatomega-sys/aegis-local/releases/tag/v0.1.0
