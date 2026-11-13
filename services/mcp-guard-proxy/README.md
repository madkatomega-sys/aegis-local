# mcp-guard-proxy

The control plane for tool use. OpenCode (or any MCP client) connects only to this service. It
fronts the upstream tool servers in [`config/mcp-servers.yaml`](../../config/mcp-servers.yaml)
and applies [`config/policy.yaml`](../../config/policy.yaml) to every call.

| Module | Responsibility |
|---|---|
| [`canonical.py`](aegis_guard/canonical.py) | Canonical JSON and call hashes, so formatting can't hide a repeat |
| [`breaker.py`](aegis_guard/breaker.py) | Budgets, repeat and retry detection, oscillation, near-miss failure streaks |
| [`policy.py`](aegis_guard/policy.py) | Tool rules, session taint, allow / hold / deny decisions |
| [`quarantine.py`](aegis_guard/quarantine.py) | Turns untrusted output into a schema-checked extraction via the tier 0 extractor |
| [`ledger.py`](aegis_guard/ledger.py) | SQLite audit log and the one-shot approval queue |
| [`upstream.py`](aegis_guard/upstream.py) | Minimal Streamable HTTP MCP client for upstream servers |
| [`app.py`](aegis_guard/app.py) | The MCP endpoint, status, and admin API |

## Order of checks for `tools/call`

1. **Policy.** No matching rule or a deny rule: denied. A mutating tool in a tainted session (or
   one marked `approval: always`): held until `python aegis.py approve <id>`.
2. **Circuit breaker.** Budget, repeat, retry-after-failure, oscillation, near-miss streak. Once
   tripped, the session's tool access stays stopped until `python aegis.py reset <session>`.
3. **Upstream call.**
4. **Taint and quarantine.** Output from an untrusted server taints the session and is replaced
   by a structured extraction before it reaches the agent.
5. **Ledger.** Decision, rule, outcome, and a hash of the result.

## Authentication

- `AEGIS_CLIENT_TOKEN` is required on `/mcp` and `/status`. OpenCode sends it from its environment.
- `AEGIS_ADMIN_TOKEN` is required on `/admin/*`. Only `aegis.py` has it.

The service refuses to start without both. Sandbox containers can reach the guard over their
internal networks but never receive either token.
