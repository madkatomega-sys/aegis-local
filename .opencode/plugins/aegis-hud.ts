/**
 * Aegis status display for OpenCode.
 *
 * Informational only. Nothing about safety depends on this plugin: the guard proxy enforces
 * every rule out of process. If this file is deleted, the harness behaves exactly the same.
 *
 * After each guard tool call it asks the guard for status and shows the current chat's state:
 * remaining call budget, whether it has read untrusted content, a tripped breaker, and pending
 * approvals. A chat's guard session is "<connection>:<root chat ID>" when aegis-chat.ts tags its
 * calls; without tags, every chat shares the connection's session, and that is what it shows.
 *
 * One file serves both plugin APIs; its only export is a default object. OpenCode 1.x calls its
 * `server` function and also calls every other export as a plugin, so nothing else is exported.
 * OpenCode 2.x calls `setup`, which does nothing yet: in 2.x, toasts belong to GUI and TUI
 * plugins, which run in the client, not to server plugins like this one. Under 2.x there is no
 * status display until one of those is written.
 */

const GUARD_STATUS = process.env.AEGIS_GUARD_STATUS_URL ?? "http://127.0.0.1:4100/status"
const TOKEN = process.env.AEGIS_CLIENT_TOKEN ?? ""
const GUARD_TOOL_PREFIX = "aegis_"

type SessionStatus = {
  remaining_budget: number
  tainted: boolean
  taint_sources: string[]
  tripped: { rule: string; reason: string } | null
}

type GuardStatus = {
  sessions: Record<string, SessionStatus>
  pending_approvals: number
}

async function fetchStatus(): Promise<GuardStatus | null> {
  if (!TOKEN) return null
  try {
    const res = await fetch(GUARD_STATUS, { headers: { Authorization: `Bearer ${TOKEN}` } })
    return res.ok ? ((await res.json()) as GuardStatus) : null
  } catch {
    return null
  }
}

// The guard session for a chat: the tagged one ("...:<chat>") if there is one, else the
// connection's untagged session when there is exactly one.
function sessionFor(status: GuardStatus, chat: string | null): SessionStatus | null {
  const entries = Object.entries(status.sessions)
  const tagged = chat ? entries.find(([id]) => id.endsWith(`:${chat}`)) : undefined
  if (tagged) return tagged[1]
  const untagged = entries.filter(([id]) => !id.includes(":"))
  return untagged.length === 1 ? untagged[0][1] : null
}

function summarize(session: SessionStatus | null, pending: number): string {
  const parts: string[] = []
  if (session) {
    parts.push(`budget ${session.remaining_budget}`)
    parts.push(session.tainted ? `read untrusted content (${session.taint_sources.join(", ")})` : "clean")
    if (session.tripped) parts.push(`breaker tripped: ${session.tripped.rule}`)
  }
  if (pending) parts.push(`${pending} awaiting approval`)
  return parts.length ? `Aegis: ${parts.join(" | ")}` : ""
}

const aegisHud = async ({ client }: any) => {
  const roots = new Map<string, string | null>()
  const last = new Map<string, string>()

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
    "tool.execute.after": async (input: any) => {
      if (typeof input?.tool !== "string" || !input.tool.startsWith(GUARD_TOOL_PREFIX)) return
      const status = await fetchStatus()
      if (!status) return
      const chat = await rootOf(input.sessionID)
      const session = sessionFor(status, chat)
      const line = summarize(session, status.pending_approvals)
      // Speak when something worth knowing changes, not after every call: the first call, untrusted
      // content, a tripped breaker, an approval waiting, or the budget running low (20 or fewer).
      const budget = session?.remaining_budget ?? 0
      const signature = JSON.stringify([
        session?.tainted ?? null,
        session?.tripped?.rule ?? null,
        status.pending_approvals,
        budget <= 20 ? budget : "ok",
      ])
      const key = chat ?? ""
      if (!line || signature === last.get(key)) return
      last.set(key, signature)
      const alarming = line.includes("tripped") || line.includes("untrusted") || line.includes("approval")
      // The toast API may differ between OpenCode versions; fall back to the log.
      const tui = (client as any).tui
      try {
        if (tui?.showToast) {
          await tui.showToast({ body: { message: line, variant: alarming ? "warning" : "info" } })
          return
        }
      } catch {
        // fall through to the log
      }
      await (client as any).app?.log?.({ body: { service: "aegis-hud", level: "info", message: line } })
    },
  }
}

export default {
  id: "aegis-hud",
  server: aegisHud, // OpenCode 1.x
  setup: async () => {}, // OpenCode 2.x: no toast API for server plugins (see above)
}
