/**
 * Aegis chat tags for OpenCode.
 *
 * OpenCode keeps one MCP connection to the guard for as long as its server runs, so without this
 * plugin every chat in the GUI shares one guard session: one circuit breaker, one call budget, one
 * taint flag. Before each guard tool call, this plugin adds a `_aegis_chat` argument: the chat's
 * root session ID and an HMAC of it keyed with the client token. The guard checks the HMAC, keeps
 * separate state for each chat, and removes the argument before the call reaches a worker.
 *
 * Nothing depends on this plugin for safety. Without it, every call counts against the connection's
 * one shared session, which is stricter. The model never sees the client token, so it can't forge
 * a tag; the guard refuses any tag that doesn't verify.
 *
 * Loaded by `python aegis.py opencode`, which sets OPENCODE_CONFIG_DIR to this folder.
 */
import type { Plugin } from "@opencode-ai/plugin"
import { createHmac } from "node:crypto"

const TOKEN = process.env.AEGIS_CLIENT_TOKEN ?? ""
const GUARD_TOOL_PREFIX = "aegis_" // OpenCode names the guard's tools aegis_<server>__<tool>
const TAG_ARG = "_aegis_chat"

export const AegisChat: Plugin = async ({ client }) => {
  const roots = new Map<string, string | null>()

  // Subagent chats share their parent's context, so they share its state: tag with the root chat.
  // If the chain can't be read, return null and send no tag (the stricter, shared session).
  async function rootOf(sessionID: string): Promise<string | null> {
    if (roots.has(sessionID)) return roots.get(sessionID) ?? null
    let current = sessionID
    let root: string | null = null
    for (let depth = 0; depth < 32; depth++) {
      let parent: string | undefined
      try {
        const res = await (client as any).session.get({ path: { id: current } })
        if (!res?.data) break
        parent = res.data.parentID
      } catch {
        break
      }
      if (!parent) {
        root = current
        break
      }
      current = parent
    }
    roots.set(sessionID, root)
    return root
  }

  return {
    "tool.execute.before": async (input: any, output: any) => {
      if (!TOKEN || typeof input?.tool !== "string" || !input.tool.startsWith(GUARD_TOOL_PREFIX)) return
      if (!output?.args || typeof output.args !== "object") return
      const chat = await rootOf(input.sessionID)
      if (!chat) {
        delete output.args[TAG_ARG] // never forward a tag the model wrote itself
        return
      }
      const mac = createHmac("sha256", TOKEN).update(`aegis-chat:${chat}`).digest("hex")
      // Change the arguments in place: OpenCode keeps its own reference to this object and
      // ignores a replacement.
      output.args[TAG_ARG] = `${chat}.${mac}`
    },
  }
}
