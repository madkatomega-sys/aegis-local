/**
 * Aegis chat tags for OpenCode, 1.x and 2.x.
 *
 * OpenCode keeps one MCP connection to the guard for as long as its server runs, so without this
 * plugin every chat shares one guard session: one circuit breaker, one call budget, one taint flag.
 * Before each guard tool call, this plugin adds a `_aegis_chat` argument: the chat's root session
 * ID and an HMAC of it keyed with the client token. The guard checks the HMAC, keeps separate state
 * for each chat, and removes the argument before the call reaches a worker.
 *
 * The root of a chat is found by following its parent (a subagent's chat) and, in 2.x, the session
 * it was forked from (a fork copies its source's conversation, untrusted content included), as far
 * as they go. So subagents and forks share the state of the chat they came from, and neither
 * delegating nor forking sheds taint.
 *
 * Nothing depends on this plugin for safety. Without it, every call counts against the connection's
 * one shared session, which is stricter. The model never sees the client token, so it can't forge
 * a tag; the guard refuses any tag that doesn't verify.
 *
 * One file serves both plugin APIs. Its only export is a default object: OpenCode 1.x calls its
 * `server` function, and 2.x calls `setup` (and needs `id`). 1.x also calls every other export of a
 * plugin file as a plugin, so nothing else is exported. In 2.x the hook also sees the calls made
 * from inside the code-mode `execute` tool.
 *
 * Loaded by `python aegis.py opencode`, which sets OPENCODE_CONFIG_DIR to this folder.
 */
import { createHmac } from "node:crypto"

const TOKEN = process.env.AEGIS_CLIENT_TOKEN ?? ""
const GUARD_TOOL_PREFIX = "aegis_" // OpenCode names the guard's tools aegis_<server>__<tool>
const TAG_ARG = "_aegis_chat"
const MAX_DEPTH = 32

type Next = (sessionID: string) => Promise<string | null | undefined> // undefined: can't tell

// Walk up from a chat to its root. If the chain can't be read, return null: no tag, which means
// the stricter, shared session.
function rootFinder(next: Next) {
  const roots = new Map<string, string | null>()
  return async (sessionID: string): Promise<string | null> => {
    if (roots.has(sessionID)) return roots.get(sessionID) ?? null
    let current = sessionID
    let root: string | null = null
    for (let depth = 0; depth < MAX_DEPTH; depth++) {
      const up = await next(current)
      if (up === undefined) break
      if (up === null) {
        root = current
        break
      }
      current = up
    }
    roots.set(sessionID, root)
    return root
  }
}

// Tag a guard call's arguments in place (OpenCode keeps its own reference to the object).
async function tag(tool: unknown, sessionID: string, args: any, rootOf: (id: string) => Promise<string | null>) {
  if (typeof tool !== "string" || !tool.startsWith(GUARD_TOOL_PREFIX)) return
  if (!args || typeof args !== "object" || Array.isArray(args)) return
  delete args[TAG_ARG] // never forward a tag the model wrote itself
  if (!TOKEN) return
  const chat = await rootOf(sessionID)
  if (!chat) return
  const mac = createHmac("sha256", TOKEN).update(`aegis-chat:${chat}`).digest("hex")
  args[TAG_ARG] = `${chat}.${mac}`
}

export default {
  id: "aegis-chat",

  // OpenCode 1.x
  server: async ({ client }: any) => {
    const rootOf = rootFinder(async (id) => {
      try {
        const res = await client.session.get({ path: { id } })
        if (!res?.data) return undefined
        return res.data.parentID ?? null
      } catch {
        return undefined
      }
    })
    return {
      "tool.execute.before": async (input: any, output: any) => {
        await tag(input?.tool, input?.sessionID, output?.args, rootOf)
      },
    }
  },

  // OpenCode 2.x
  setup: async (ctx: any) => {
    const rootOf = rootFinder(async (id) => {
      try {
        const info = await ctx.session.get({ sessionID: id })
        if (!info || info.id !== id) return undefined
        return info.parentID ?? info.fork?.sessionID ?? null
      } catch {
        return undefined
      }
    })
    await ctx.tool.hook("execute.before", async (event: any) => {
      await tag(event?.tool, event?.sessionID, event?.input, rootOf)
    })
  },
}
