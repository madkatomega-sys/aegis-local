# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `models get --only tier0|tier1` fetches and sets one tier's file. `models get --max-gb N`
  refuses, before downloading anything, if the files still missing add up to more than `N` GB.
- `docs/validation/model_check.py`, the release check for the model helper (0.3's E9 as a
  script). It runs the helper from a separate checkout, so the main `.env` is never touched, and it
  hard-links files you already have instead of downloading them. Every download goes through
  `--max-gb`. It stops before downloading anything if a setup step fails. In 0.3, a failed shell
  link meant E9 downloaded 7.36 GB more than was approved. The script also removes only the
  folders it created itself.

### Changed

- The code worker, the math-physics worker and the writing auditor run on the mcp 2.x SDK
  (`mcp>=2.3.0,<3`), where `FastMCP` is now `MCPServer`. Tool results and errors look the same
  to the guard. mcp 2 caps request bodies at 4 MiB; the code worker raises that to four times
  its 2 MB edit limit, so large edits still fit.
- The release workflow uses the new major versions of the Docker actions: setup-qemu v4,
  setup-buildx v4, login v4, metadata v6, build-push v7.
- Higher minimum versions: fastapi 0.165.0, ruff 0.17.5, sympy 1.14.0, pint 0.25.3 (0.26.2 in
  the math worker).

## [0.3.0] - 2026-11-20

From a fresh machine to a working agent in the browser. `aegis.py models` detects the GPU,
suggests a profile, and downloads its model files, checked against pinned SHA-256 hashes.
`aegis.py opencode --web` opens OpenCode's browser interface with the guard in front of every
tool, and a setup guide walks through all of it. In the browser, each chat now gets its own guard
session, titles are short again, and the status display follows the current chat. Validation on
Windows 11 with an RTX 4070 is in
[docs/validation](docs/validation/0.3.0-windows11-rtx4070/README.md).

Not verified in this release, and left open for later:
- a native Linux run on real hardware, including the bridge-address binding that keeps
  `llama-server` off the LAN;
- the GitHub upstream with a real token;
- `models suggest` on real AMD and Intel cards, and on Linux (only its parsing is tested);
- signing in to `opencode --web` from a real browser (the validation drives the same HTTP API
  with the same password);
- the OpenCode 2.x desktop app, whose built-in server hangs on every turn with this stack.

CI covers Linux with a stub model, and GitHub content through a fixture server. See
[PLAN.md](docs/PLAN.md#9-milestones-and-acceptance-criteria).

### Added

- A model helper, `python aegis.py models`:
  - `suggest` detects the GPU and its memory (NVIDIA through `nvidia-smi`; AMD and Intel through
    the Windows registry, or `/sys` and `lspci` on Linux) and the system RAM, and suggests a profile.
  - `get` shows what it will download and asks first, checks the free disk space, downloads from
    Hugging Face with resume, checks each file's size and SHA-256, and writes the profile and model
    paths into `.env`. Files already present are checked, not fetched again.
  - `list` shows the catalog, and `verify` re-checks the files `.env` points at.

  The files come from a new catalog, `config/models.toml`, which pins each one by repository, file
  name, size and SHA-256, and each profile names its two files with a `download` key. Only the
  `nvidia-12gb` files are measured; the rest are the profiles' untested candidates.
- `python aegis.py opencode --web [PROJECT]` opens OpenCode's browser interface on `127.0.0.1`:
  - with the Aegis config and plugins;
  - protected by a generated password, kept in `.aegis/tokens.env`;
  - with a private OpenCode data folder under `.aegis/opencode-web`.

  `--port` picks the port (default 4096). It needs OpenCode 1.x, and was checked on 1.19.5.
- `docs/GUIDE.md`, a setup guide from a fresh machine to the browser interface: choosing a profile
  for your GPU, finding and verifying its model files, starting the stack, loading a repository,
  and working in the browser. It also says why the OpenCode desktop app (2.3.0) doesn't work with
  Aegis yet, and why adding Aegis only as a model provider leaves out the guard.
- `docs/validation/web_check.py`, a release check for the browser interface: sign-in, loopback
  only, a cross-site request, two chats with their own guard sessions, titles, and status toasts.

### Changed

- `.env.example` leaves `AEGIS_PROFILE` empty instead of presetting `nvidia-12gb`, so
  `models get` uses the profile `models suggest` picks and writes it in. `doctor` warns when no
  profile is set, since `up` then falls back to `cpu-only`.
- `models get` fetches and checks a file once when both tiers use it.

### Fixed

- Chat titles in OpenCode are titles again, not tier 0's reasoning. With thinking on, the 4B
  model answered "write a title" with a page of "Okay, the user wants a title…", which became
  every chat's title and kept tier 0 busy for a minute or more on the CPU. The `aegis-fast` alias
  (OpenCode's small model, used for titles and summaries) now turns thinking off, as the extractor
  already did: titles take about 9 tokens.
- The status plugin (`aegis-hud.ts`) shows the current chat's guard state: budget, untrusted
  content, a tripped breaker, and approvals waiting. It used to show the lowest budget across all
  sessions. It now speaks only when one of those changes or the budget runs low, not after every
  call. It reacts only to guard tools, and the plugin files export nothing else, since OpenCode
  runs every export as a plugin.
- Chats in one OpenCode server no longer share a guard session.
  - **The problem:** OpenCode keeps one MCP connection for its whole life, so in the browser GUI
    every chat counted against one circuit breaker, one call budget, and one taint flag. A second
    chat's first test run could be stopped by the first chat's history.
  - **The fix:** a new plugin, `.opencode/plugins/aegis-chat.ts`, tags each guard call with its
    root chat, signed with the client token. The guard verifies the tag, keeps separate state per
    chat, and strips the tag before the call reaches a worker. It refuses a tag that doesn't
    verify, and treats untagged calls as before.
  - **Loading it:** `aegis.py opencode` and `aegis.py env` now set `OPENCODE_CONFIG_DIR` to
    `.opencode`, so OpenCode loads the plugins in `.opencode/plugins`. Until now they weren't
    loaded at all, including the status display.

## [0.2.0] - 2026-11-17

Makes the coding loop work with small local models, and measures it. The builder gets an exact
find-and-replace edit tool, which on its own took a 4B model from 1 to 9 fixes in 10 on a
one-line bug. An eval suite in the repository now scores the agent end to end. The 12 GB
profile's recommended 9B model is measured with it. `workspace prepare` lets the sandbox run tests
that import packages. Validation on Windows 11 with an RTX 4070 is in
[docs/validation](docs/validation/0.2.0-windows11-rtx4070/README.md).

Not verified in this release, and left open for later: a native Linux run on real hardware
(including the bridge-address binding that keeps `llama-server` off the LAN), and the GitHub
upstream with a real token. CI covers Linux with a stub model, and GitHub content through a
fixture server. See [PLAN.md](docs/PLAN.md#9-milestones-and-acceptance-criteria).

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
- An eval suite in `evals/`: `python evals/run.py` asks the builder agent to fix six small broken
  projects through the real harness and scores each run by applying its exported diff to a clean
  copy and running the tests. The projects cover one wrong line, two edits in one function, a CRLF
  file, bugs in two files, a missing module, and tests that import a package. It compares builder setups (arms) with interleaved
  runs, resumes an interrupted batch, and records the commit, OpenCode version, model files and
  GPU load with the results. `tests/test_evals.py` checks in CI that every fixture fails as given
  and passes with its reference fix.
- `python aegis.py workspace prepare` installs the loaded project's dependencies, so the
  sandbox's checks can run tests that import packages. The code worker reads them from
  `requirements*.txt` and `pyproject.toml` (dependencies, dev/test extras and dependency groups).
  A one-shot `workspace-prepare` container installs them into a separate `aegis-venv` volume,
  which the worker mounts read-only. That container has network access but never sees the
  repository, holds no tokens, and shares no network with the stack. It installs wheels only
  unless `--allow-source-builds` is given, and refuses pip options, URLs, and paths. The CI smoke
  test checks all of this against the real containers. A new eval fixture, `pace`, needs it.

### Changed

- The OpenCode config denies every OpenCode tool that reaches the host, the web, or other
  agents (`read`, `glob`, `grep`, `list`, `lsp`, `skill`, `websearch`, `codesearch`,
  `external_directory`, and `task`, on top of `edit`, `bash`, and `webfetch`), disables the
  built-in `build`, `plan`, `general`, and `explore` agents, lists only the local models, and
  makes `planner` the default agent. The planner may delegate only to the verifier. Checked on
  OpenCode 1.19.5 and 2.3.0.
- The agent prompts say that workspace paths are relative to the workspace root, never host
  paths.
- The `nvidia-12gb` profile has measured numbers for its first tier 1 candidate, Qwen3.5-9B Q6_K,
  on an RTX 4070: about 51 tokens/s and 8.8 GB peak for the whole card. In the eval suite it fixed
  14 of 15 runs against 10 of 19 for Qwen3-4B, including 11 of 12 runs of the two-edit and
  new-module fixtures, which the 4B never fixed
  ([evidence](docs/validation/12gb-tier1-2026-11-17-windows11-rtx4070/README.md)). The profile
  stays `tested = false` until a second person reproduces the numbers.

### Fixed

- `aegis.py workspace export` keeps CRLF line endings. It read Git's diff in text mode, which
  turned CRLF into LF, so a patch for a file with CRLF line endings didn't apply to the user's
  repository.
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

[Unreleased]: https://github.com/madkatomega-sys/aegis-local/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/madkatomega-sys/aegis-local/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/madkatomega-sys/aegis-local/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/madkatomega-sys/aegis-local/releases/tag/v0.1.0
