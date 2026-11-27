# Why OpenCode 2.3.0's background service "hangs" with Aegis

W6 of the [0.4 plan](../../PLAN.md#the-plan-for-04-hardening-and-breadth). In 0.3, every request
through the OpenCode desktop app (2.3.0) seemed to hang, while the same build in `--standalone`
mode worked ([GUIDE §8](../../GUIDE.md#8-the-opencode-desktop-app-and-other-clients), as of 0.3).
This page reduces that to its cause.

**Result:** it isn't a hang in OpenCode's server. A chain of three things causes it:
1. **The token comes from the wrong environment.** OpenCode 2.x resolves `{env:AEGIS_CLIENT_TOKEN}`
   in the guard's `Authorization` header from the environment of the **server** process, not the
   client's. The desktop app starts its background service itself, without that variable.
2. **So the guard answers 401.** OpenCode logs `mcp connect failed ... status=needs_auth` and goes on
   without the Aegis tools.
3. **So the agent never finishes.** With no Aegis tools, the agent loops: one run made 42 model
   requests in two minutes, calling `question`, starting subagents, and probing `execute`. To a
   user, that looks like a hang.

Start the service with the token in its environment, and the same request finishes in seconds.

| | |
|---|---|
| Date | 2026-11-27 |
| Machine | Windows 11 Pro 26300, RTX 4070 12 GB, the 0.3 stack (`nvidia-12gb`) |
| OpenCode | the CLI the desktop app bundles: `opencode-cli` 2.3.0 (`%APPDATA%\ai.opencode.desktop\cli\2.3.0-x64`) |
| Code | `main` at `a6e7269` |

## Method

Each run:
1. used `opencode-cli run --format json --archived` with a fresh, private OpenCode home (all four
   `XDG_*_HOME` variables), so no OpenCode data of the user's was read or used;
2. ran through the background service (the default) and with `--standalone`;
3. had a 120 s limit, after which it counted as hung;
4. stopped, afterwards, every `opencode-cli` process it had started, so the next run started its
   own service.

Model requests were counted from the llama-server logs. No OpenCode process was running before
or after.

**A side finding:** OpenCode 2.x takes its project directory from `PWD`, not from the process's
working directory. Git Bash sets `PWD` to wherever the shell is, so the first runs loaded the
repository's `.opencode` plugins from a test folder elsewhere. Every run below sets `PWD`.

## Results

| Run | Config | Token in the server's environment | Service | `--standalone` |
|---|---|---|---|---|
| V1 | provider only (the router) | n/a | 3 s, answered | 3 s, answered |
| V2 | V1 + the guard as MCP server | yes | 5 s, answered | 3 s, answered |
| V3 | the full Aegis config, planner agent, "List the files in the sandbox workspace." | yes | 8 s, listed them | 8 s, listed them |
| V4 | V3 + `OPENCODE_CONFIG_DIR=.opencode` (the plugins) | yes | 8 s, listed them | 9 s, listed them |
| V5 | V3, no token anywhere | no | **no result after 120 s**, 42 model requests | **no result after 120 s**, 21 model requests |
| V6 | V3; the service started first *without* the token, then the client *with* it | no (the client's is ignored) | **no result after 120 s**, 22 model requests | n/a |
| V7 | V3; the service started first *with* the token, then the client *without* it | yes (the client's doesn't matter) | 7 s, listed them | n/a |

- **V5–V6:** every run's log has `mcp http authentication rejected status=401` and
  `mcp connect failed server=aegis status.status=needs_auth`.
- **V7:** the log has `mcp connected server=aegis tools=20`.

**What this explains:** V6 is the desktop app's situation. Its service is started by the app, not
by `aegis.py`, so the variables a client brings don't reach it. V5 shows it's the same in
`--standalone`: without the token there, it hangs too. So the 0.3 observation that `--standalone`
worked fits as well, because `--standalone` gets the client's own environment.

**Not confirmed with the desktop app's window itself.** That would mean creating sessions in the
user's own OpenCode data. The check is short; see the guide.

## Also found: the Aegis plugins don't load in OpenCode 2.x

In V4 and the side-finding runs, 2.3.0 refused both plugins:

> Plugin must export a default definition with an id and an effect or setup function.

So under 2.x:
- **The chat tags don't load.** All chats share one guard session, as without the plugin in 1.x.
- **The status display doesn't load.**

The plugin API changed between 1.x and 2.x. 1.x calls every export of a plugin file as a plugin,
so one file can't simply serve both versions.

## Decision

- **Guide:** say what makes the desktop app work (the variables must be in the environment the app
  and its service start with) and what's still missing under 2.x (the plugins). Keep
  `opencode --web` on 1.19.5.
- **2.x support in `--web` and the desktop app: not in 0.4 as planned.** Two pieces come first:
  - plugins written for 2.x's API;
  - a fresh look at 2.x's code-mode tool (`execute`). In V5, the planner used it to call
    OpenCode's own tools, and one call returned the worktree list, which named the user's home
    folder.

  Both are follow-ups in the plan.
- **Upstream report:** [`ISSUE-DRAFT.md`](ISSUE-DRAFT.md) describes two behaviors, with the
  reproduction:
  1. A run whose remote MCP server fails authentication carries on without its tools and without
     telling the user.
  2. `{env:...}` in an MCP header is resolved in the background service's environment, not the
     client's.

  It is a draft for the maintainer to file.
