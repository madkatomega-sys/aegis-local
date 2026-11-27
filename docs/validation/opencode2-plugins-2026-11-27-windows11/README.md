# OpenCode 2.x: the Aegis plugins, and what code mode can reach

W8 of the [0.4 plan](../../PLAN.md#the-plan-for-04-hardening-and-breadth). It follows
[W6](../opencode2-service-2026-11-27-windows11/README.md), which found that OpenCode 2.3.0
doesn't load the Aegis plugins, and that its code-mode tool `execute` hadn't been checked against
the Aegis lockdown since 0.2.

**Result:**
- **Chat tags work under 2.x.** One plugin file now serves both OpenCode plugin APIs, and every
  guard call is tagged with its chat under 1.19.5 and 2.3.0, including calls made from inside code
  mode. Under 2.x a fork shares its source chat's session, and a subagent its parent's.
- **One gap was found in the code-mode audit, and fixed in the config.** Code mode exposed eight
  of OpenCode's own tools, which the permission rules don't cover. One of them, `worktree_create`,
  ran `git` on the host. All eight are now turned off by name, and the fix is checked.
- **Not done: the status display under 2.x.** In 2.x, toasts belong to GUI and TUI plugins, which
  run in the client, not to server plugins.

| | |
|---|---|
| Date | 2026-11-27 |
| Machine | Windows 11 Pro 26300, RTX 4070 12 GB, the 0.3 stack (`nvidia-12gb`) |
| OpenCode | 1.19.5, and the CLI the desktop app bundles: `opencode-cli` 2.3.0 |
| Code | branch `feature/opencode-2x-plugins`, from `main` at `c87ffa9` |

Every 2.x run used a fresh, private OpenCode home (all four `XDG_*_HOME` variables, and `PWD`),
so no OpenCode data of the user's was read or used.

## The plugin API

The source of OpenCode's plugin package at the
[`v2.3.0` tag](https://github.com/anomalyco/opencode/tree/v2.3.0/packages/plugin/src) shows how
2.x plugins work. A 2.x plugin is a default export `{ id, setup }`, and `setup(ctx)` registers
hooks by domain. The tag needs `ctx.tool.hook("execute.before", …)`, whose event carries `tool`,
`sessionID` and a mutable `input`, and `ctx.session.get({ sessionID })`.

The session schema records:
- `parentID`, for a subagent's chat;
- `fork.sessionID`, for a fork, which is new in 2.x. A fork copies its source's conversation,
  untrusted content included, so it must share the source's guard session.

A probe plugin showed three things:
- Guard tools are named `aegis_<server>__<tool>` in 2.x too.
- `input` is the argument object.
- **Calls made from inside `execute` go through the hook as their own tool calls.** For example,
  `tools.aegis.code__list_files(...)` arrives as `aegis_code__list_files`. So a plugin does see
  code-mode calls.

**One file for both versions.** OpenCode 1.19.5 refused a 2.x-style file with "must default
export an object with server()". So 1.x also accepts an object as the default export, and reads
its `server` function. Each Aegis plugin file now has exactly one export, a default object with:
- `id`;
- `server`, the 1.x plugin, unchanged in behavior;
- `setup`, the 2.x plugin.

Both versions load both files without an error. Two files side by side, one per version, also
tagged correctly, but each version then logs a load error for the other's file.

## Chat tags

| Check | OpenCode | Result |
|---|---|---|
| One chat, both plugin files in `.opencode/plugins` | 1.19.5 | its guard call tagged with its chat |
| The same | 2.3.0 `--standalone` | its guard call tagged with its chat |
| `web_check.py`: two Builder chats in one browser server, titles, status toasts | 1.19.5 | all checks passed: every call tagged, a guard session per chat, a status toast in each chat |
| One background service (one MCP connection), as the desktop app runs it: chats A and B, a fork F of A, and S, which delegates to the verifier subagent | 2.3.0 | A and B each have their own session. F's call is tagged with **A**. All five of S's subagent calls (list, read, `run_check`) are tagged with **S**. All on one connection, every call tagged |

## What code mode can reach

The probe logged the tools each agent is offered:
- **planner:** `question`, `skill`, `subagent`, `execute`.
- **builder and essayist:** `question`, `skill`, `execute`.

The guard's tools aren't offered directly; they are called through `execute`.

`execute` runs JavaScript in OpenCode's "confined Code Mode runtime". Its own description says
there are no imports, no filesystem access and no timers, that `fetch` is available, and that
everything else goes through `tools`. Each line below is from a builder run that asked the model to
use code mode to reach something the Aegis lockdown denies.

| Asked for | What happened |
|---|---|
| `fetch` to the router on loopback, and to another local port | **Refused:** "Permission denied: webfetch". `fetch` in code mode is the `webfetch` permission, which the Aegis config denies |
| `tools.opencode.read` on a file | Not in the code-mode catalog |
| A shell command | No shell in the catalog. The model tried `fetch` instead, which was refused as above |
| The code-mode catalog | Three namespaces. `aegis`: the guard's tools. `browser`: empty. `opencode`: `list_mcp_resources`, `models`, `read_mcp_resource`, `session_move`, `session_rename`, `worktree_create`, `worktree_list` |
| `tools.opencode.worktree_list()` | **Ran.** It returned the project's worktree list, here a path naming the user's home folder |
| `tools.opencode.worktree_create({ name })` | **Ran `git` on the host.** It failed only because the test project wasn't a Git repository ("fatal: invalid reference: HEAD"). In a real checkout it would have made a worktree there, outside the sandbox and the guard |

**The fix:** `config/opencode.json` turns those tools off by name, in its global `tools` map:
`opencode_worktree_create`, `opencode_worktree_remove`, `opencode_worktree_list`,
`opencode_session_move`, `opencode_session_rename`, `opencode_models`,
`opencode_list_mcp_resources` and `opencode_read_mcp_resource`. Re-run with the real config:
- the `opencode` namespace is empty;
- `worktree_create` is "Unknown tool";
- code mode's own `search` for "worktree" finds nothing.

`tests/test_launcher.py` checks that all eight stay off, and that `webfetch` stays denied.

**Not left open, but noted:**
- **`skill` is offered** although the config denies the `skill` permission. In one run the builder
  called it, and it failed with "Unable to load skill search", because no skills are installed.
  Whether the deny rule itself is enforced wasn't shown.
- **`question` is offered.** It can only ask the user.

## Two oddities in 2.3.0

Both are recorded here and not investigated further:
- **The `"opencode_*"` wildcard.** In the `tools` map it made 2.3.0 stop right after setting up
  its database, before reading the rest of its config, twice in a row. The same tools listed by
  name work.
- **A new copy of the config with `$schema` in it.** A config file written fresh (by Python's
  `json.dump`) that kept the `$schema` line stopped the same way. The same content without that
  line, or a byte-for-byte copy of the repository's file, didn't. The repository's own config,
  edited for this change, loads normally.

## Not covered

- **The status display under 2.x.** `aegis-hud.ts` loads under 2.x, but its `setup` does nothing.
  2.x gives toasts only to GUI and TUI plugins.
- **The desktop app's own window.** All 2.x runs used the CLI the app bundles. Using the window
  would mean creating sessions in the user's own OpenCode data.
- **`aegis.py opencode --web` with 2.x.** It still requires 1.x. 2.x's `serve` is a different API
  and web server, and `web_check.py` speaks 1.x's API.
- **The eval suite under 2.x.**
