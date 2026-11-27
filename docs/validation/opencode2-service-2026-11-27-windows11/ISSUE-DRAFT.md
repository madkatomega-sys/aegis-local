# Draft upstream issue (anomalyco/opencode), not filed

**Title:** A remote MCP server that fails authentication leaves a run looping without its tools, and `{env:}` in its headers comes from the background service's environment

**Version:** opencode-cli 2.3.0 (the build bundled with the desktop app), Windows 11.

### What happens

A config has a remote MCP server whose `Authorization` header uses an environment variable:

```json
"mcp": {
  "tools": {
    "type": "remote",
    "url": "http://127.0.0.1:4100/mcp",
    "headers": { "Authorization": "Bearer {env:MY_TOKEN}" }
  }
}
```

1. When `MY_TOKEN` isn't set **in the background service's environment**, the server answers 401.
   The log shows `mcp connect failed ... status.status=needs_auth`. The run then carries on
   without that server's tools, and nothing tells the user. The agent keeps going without
   finishing: 42 model requests in 2 minutes in our case, with `question` calls cancelled ("This
   non-interactive run cannot ask the user questions"), and subagents started in the background.
   The client looks hung.
2. Setting `MY_TOKEN` in the **client's** environment (`opencode run`) doesn't help once the
   service is running. Only the service's environment counts. The desktop app starts that service
   itself, so a variable set in a terminal before `opencode run` never reaches it.

### Reproduce

1. Start the service without the variable: `opencode-cli serve --service`, with `MY_TOKEN` unset.
2. In another terminal: `set MY_TOKEN=...`, then
   `opencode-cli run --format json -- "use the tools server to list files"`.
3. The run doesn't finish, and the service log has `needs_auth`.
4. Start the service with `MY_TOKEN` set, repeat step 2, and it finishes in seconds
   (`mcp connected ... tools=N`).

### What would help

- **Fail visibly:** surface `needs_auth` (or any MCP connect failure) to the client, or end the run
  with an error, instead of continuing without the tools.
- **Document the scoping:** say in the config docs that `{env:}` in MCP settings is resolved in
  the server process. Or resolve it from the client's environment when the client sends the
  request.

### Also

Two more things we saw, which may be expected:
- 2.x takes the project directory from `PWD` rather than the process's working directory.
- Plugins written for 1.x fail with "Plugin must export a default definition with an id and an
  effect or setup function". A note in the migration docs would help plugin authors.
