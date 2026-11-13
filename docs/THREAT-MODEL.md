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
| Infinite loops and retry spirals | Canonical call hashing, oscillation detection, near-duplicate failure streaks, hard budgets | Guard |
| Destructive commands | Allowlisted commands, no shell, read-only root, `tmpfs` scratch, dropped capabilities, resource limits | Sandbox workers |
| Data exfiltration from sandboxes | Internal-only network with no route out | Compose networks |
| Host file damage | Repo copied into a volume; changes leave only as an exported diff the user applies | `aegis.py workspace` |
| LAN exposure | All ports bound to `127.0.0.1` (or the container bridge on Linux) | Compose, `aegis.py` |
| Wrong math or physics | Numeric and symbolic answers come from SymPy, pint, SciPy | `math-physics-mcp` |
| Wrong code | Patches judged by exit codes of tests, type checks, linters | `code-worker` |
| Invented citations | Quotes checked byte for byte against hashed source chunks | `writing-auditor` |
| Bypass through the IDE | Enforcement never depends on OpenCode, its plugins, or agent prompts; OpenCode's host-side edit, shell, and web tools are disabled | Design rule, `config/opencode.json` |
| Sandboxed code calling the guard | `/mcp` requires a client token and `/admin` a separate admin token; sandboxes hold neither | Guard, `aegis.py` |
| Workers reaching each other | One internal network per worker | Compose networks |

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
