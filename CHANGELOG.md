# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `code__replace_in_file(path, old, new)`: replaces one exact piece of text in an existing
  workspace file. `old` must appear exactly once; when it doesn't, the error says why (more than
  one match, or a match that differs only in indentation) without loosening the match. If `old`
  still has `read_file`'s line numbers, as models that call tools from code (OpenCode's code mode)
  send it, the numbers say which lines to replace, and the text after them must be exactly those
  lines. A line that only looks numbered, such as TSV data, is matched as text. It refuses files
  over 2 MB, files that aren't UTF-8, and a `new` with half of a surrogate pair. It never writes
  under `.git` or outside the workspace, even through a symlink, since Git runs settings and hooks
  from `.git` on its next command. It is mutating and a progress tool in the policy, like
  `apply_patch`, and the planner can't use it. The builder prompt now prefers it to diffs. In an
  A/B with Qwen3-4B on OpenCode 1.19.5, 10 runs per arm: with the same neutral prompt, turning
  the tool on took the builder from 1 to 9 of 10 on a one-line bug, and from 0 to 3 of 10 on a
  two-edit bug; the new prompt and the tool together against `main` were 10 against 2, and 6
  against 1. On 2.3.0 the builder fixed the one-line bug in 6 of 10 runs, the same before and
  after the line-number handling
  ([evidence](docs/validation/replace-in-file-2026-11-14-windows11-rtx4070/README.md)).
- `read_file` and `replace_in_file` split lines the same way: only on `\n`, with a trailing `\r`
  dropped from what `read_file` shows. Text copied from `read_file` matches in files with CRLF,
  mixed line endings, form feeds or Unicode line separators, and an LF edit to a CRLF line keeps
  it CRLF.
- Evidence for OpenCode 1.19.5 and 2.3.0 on Windows 11 with an RTX 4070, in
  `docs/validation/opencode-2026-11-14-windows11-rtx4070/`: the tools each agent is offered
  before and after the hardening below, probes, and ten coding-loop walkthroughs with their
  audit rows. `docs/PLATFORMS.md` names the OpenCode versions checked.

### Changed

- The OpenCode config denies every OpenCode tool that reaches the host, the web, or other
  agents (`read`, `glob`, `grep`, `list`, `lsp`, `skill`, `websearch`, `codesearch`,
  `external_directory`, and `task`, on top of `edit`, `bash`, and `webfetch`), disables the
  built-in `build`, `plan`, `general`, and `explore` agents, lists only the local models, and
  makes `planner` the default agent. The planner may delegate only to the verifier. Checked on
  OpenCode 1.19.5 and 2.3.0.
- The agent prompts say that workspace paths are relative to the workspace root, never host
  paths.

### Fixed

- `code__apply_patch` accepts a diff that is missing its final newline or has miscounted
  hunk headers (`git apply --recount`). Both are common in small models' diffs, and git rejected
  them as corrupt. Removed and context lines must still match the file exactly.
- Release notes now link repository files at the release's tag (relative links broke on the
  release page) and leave out the changelog's link definitions.
- The 0.1.0 validation report explains that its commit hashes are from the squash-merged
  validation branch, and lists how the released code differs from what was validated.

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
