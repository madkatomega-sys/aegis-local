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
- The extractor schema bounds every field (`maxLength`, `maxItems`), so constrained decoding
  can't produce an extraction too long to finish within the token cap.

### Security

- Every published port binds to loopback, and llama-server binds to loopback (Windows) or the
  container bridge (Linux).
- The guard and its admin API require separate tokens that sandboxes never receive.

[Unreleased]: https://github.com/madkatomega-sys/aegis-local/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/madkatomega-sys/aegis-local/releases/tag/v0.1.0
