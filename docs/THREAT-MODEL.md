# Threat model

Aegis-Local is a single-user workstation tool. This page states what it defends against, what it
doesn't, and where each defense lives. If you find a gap, please report it as described in
[SECURITY.md](../SECURITY.md).

## Assets

- The user's source code, notes, and files on the host.
- Credentials the harness holds: a GitHub token, any API keys in `.env`.
- The integrity of results the user acts on: code that claims to pass tests, math that claims to
  be correct, citations that claim to come from a source.
- The host machine and local network.

## Adversaries and failure sources

| Source | Example |
|---|---|
| Untrusted content | A GitHub issue, web page, or file in a cloned repo containing instructions aimed at the model |
| The model itself | Hallucinated APIs, wrong algebra, confident false citations, retry loops |
| Malicious or buggy tool output | A dependency's test runner printing text designed to look like a system message |
| Network neighbors | Another device on the LAN probing open ports |

Out of scope: an attacker who already has code execution on the host, compromised container
runtimes or GPU drivers, and malicious model weights designed to misbehave under specific
triggers. (The sandbox limits the damage such weights could do, but detecting them is not a goal.)

## Defenses

| Threat | Defense | Where it lives |
|---|---|---|
| Malformed or invented tool calls | Grammar/JSON-schema constrained decoding; strict argument validation | `llama-server`, router, guard |
| Prompt injection leading to an action | Session taint high-water mark; quarantine through the Tier 0 extractor; approvals for mutating tools in tainted sessions | Guard |
| Infinite loops and retry spirals | Canonical call hashing, oscillation detection, near-duplicate failure streaks, a failure-streak limit that workspace edits don't reset, hard budgets | Guard |
| Destructive commands | Allowlisted commands, no shell, read-only root, `tmpfs` scratch, dropped capabilities, resource limits | Sandbox workers |
| Data exfiltration from sandboxes | Internal-only network with no route out | Compose networks |
| A malicious or compromised package installed for the project's tests (`workspace prepare`) | The install runs in its own container: it has network access but never sees the repository (it gets only the dependency list), holds no tokens, and shares no network with the router, guard, or workers. By default it installs wheels only, so no build script runs. Requirements must name packages on the index: no pip options, URLs, or paths. The worker mounts the result read-only. The user starts it; the agent can't | `workspace-prepare`, `prepare.py` |
| Host file damage | Repo copied into a volume; changes leave only as an exported diff the user applies | `aegis.py workspace` |
| A workspace edit turning into code execution through Git | Neither edit tool writes under `.git`, where Git reads `core.fsmonitor`, hooks, and similar settings on its next command (including the read-only tools and `workspace export`, which need no approval). `replace_in_file` refuses `.git` and anything outside the workspace after resolving symlinks; `apply_patch` relies on `git apply`, which rejects `.git` paths | `code-worker` |
| LAN exposure | All ports bound to `127.0.0.1` (or the container bridge on Linux) | Compose, `aegis.py` |
| Wrong math or physics | Numeric and symbolic answers come from SymPy, pint, SciPy | `math-physics-mcp` |
| Wrong code | Changes judged by exit codes of tests, type checks, linters | `code-worker` |
| Invented citations | Quotes checked byte for byte against hashed source chunks | `writing-auditor` |
| Bypass through the IDE | Enforcement never depends on OpenCode, its plugins, or agent prompts; OpenCode's host-side edit, shell, and web tools are disabled | Design rule, `config/opencode.json` |
| Sandboxed code calling the guard | `/mcp` requires a client token and `/admin` a separate admin token; sandboxes hold neither | Guard, `aegis.py` |
| Workers reaching each other | One internal network per worker | Compose networks |
| Runaway computation (a pathological integral) | Each math computation runs in its own process and is killed at a wall-clock limit; container CPU, memory, and PID limits as backstop | `math-physics-mcp` |
| Overlong or truncated extractions | Extraction schema bounds every field, compiled into the decoding grammar; guard re-validates and caps fields; extractor failure denies the result | Router config, guard |
| Tampered release images | Images built only by the release workflow from owner-only tags, signed with Sigstore, with SBOM and provenance | `.github/workflows/release.yml` |

## How the defenses are tested

- Every policy, breaker, and taint decision has unit tests (`tests/test_policy.py`,
  `tests/test_breaker.py`).
- `tests/test_guard_app.py` drives the guard over HTTP with a fixture GitHub issue that contains
  a prompt injection, and checks that the raw text never reaches the caller, the session is
  tainted, the mutating call it asks for is held, and an approval works exactly once.
- `tests/smoke/smoke.py` runs on every CI build against the real containers. Besides the
  injection and breaker checks, it confirms from inside each sandbox that the internet, the
  router, other sandboxes, and the container's own root filesystem are all unreachable or
  read-only. It also runs `workspace-prepare`: that container can't reach the router, guard, or
  worker, can't see the repository, holds no tokens, and refuses pip options. It installs a real
  wheel, which the worker can then import but not change.
- `tests/test_prepare.py` checks which requirement lines `workspace prepare` accepts: index
  packages, extras, markers, hashes; never options, URLs, or paths. It also checks how
  dependencies are read from requirements files and `pyproject.toml`, and that the compose file
  keeps the install container away from the repository, the tokens, and the stack's networks.
- `tests/test_code_worker.py` checks the edit tools against a throwaway Git repository,
  including that `replace_in_file` won't write into `.git`, outside the workspace, or through a
  symlink to either. `tests/test_policy.py` fails if any code-worker tool other than the readers
  isn't marked mutating, since the `code__*` catch-all rule is read-only.

## Known limitations

- **Session-level taint is coarse.** Once a session reads untrusted content, every later mutating
  call in it needs approval, even calls unrelated to that content. This is intentional: it fails
  closed.
- **The extractor is a model.** Quarantine reduces injection risk; it doesn't remove it. A
  crafted payload could still shape the extracted fields. Approvals are the backstop.
- **Approvals rely on the user reading them.** `aegis approve` shows the tool, arguments, and
  taint sources. Approving blindly defeats the gate.
- **Prose can be wrong without a citation.** The writing auditor flags unsupported claims; it
  can't make a draft true.
- **Guard sessions live in memory.** Restarting the guard ends every session, including its
  taint and breaker state; clients must start a new session, which begins untainted. Approvals
  persist in the ledger, but an approval only matches the session that asked for it.
- **Tokens are stored in plain text** in `.aegis/tokens.env` (mode 0600 on Linux; protected by
  the user profile's permissions on Windows). Anyone who can read your home directory can call
  the guard.
- **A change to the workspace restarts the loop checks.** After a successful `progress` call
  (a new patch or edit), the guard no longer counts earlier results against identical calls, so
  the agent can rerun the tests. An agent that keeps producing new changes is stopped by the
  failure streak (five failed runs in a row) or the session budget, not by the repeat rules.
- **Every tool that changes the workspace needs its own policy rule.** The `code__*` catch-all
  in `config/policy.yaml` is read-only, so a new code-worker tool that writes is approval-free
  until it gets a `mutating` rule ahead of the catch-all. `tests/test_policy.py` checks this for
  the tools the worker registers today.
- **Prepared packages run inside the sandbox.** `workspace prepare` keeps the install away from
  the repository and the stack, but the packages it installs are then imported by the project's
  tests in the code worker. There they can do anything the tests can: read and change the
  workspace, with no network. Install only dependencies you would run on your own machine.
  `--allow-source-builds` also runs each package's build script, with network access, in the
  install container.
- **The code worker trusts its workspace by default.** Set the `code` server to `untrusted` in
  `config/mcp-servers.yaml` when you load a repository you didn't write, so its file contents
  and test output taint the session.

Reviewed for release 0.1.0.
