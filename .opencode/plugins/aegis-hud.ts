/**
 * Aegis status display for OpenCode.
 *
 * Informational only. Nothing about safety depends on this plugin: the guard proxy enforces
 * every rule out of process. If this file is deleted, the harness behaves exactly the same.
 *
 * After each tool call it asks the guard for session status and surfaces taint, remaining
 * budget, breaker trips, and pending approvals.
 */
import type { Plugin } from "@opencode-ai/plugin"

const GUARD_STATUS = process.env.AEGIS_GUARD_STATUS_URL ?? "http://127.0.0.1:4100/status"
const TOKEN = process.env.AEGIS_CLIENT_TOKEN ?? ""

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

function summarize(status: GuardStatus): string {
  const sessions = Object.values(status.sessions)
  const tripped = sessions.find((s) => s.tripped)
  const tainted = sessions.some((s) => s.tainted)
  const budget = sessions.length ? Math.min(...sessions.map((s) => s.remaining_budget)) : 0
  const parts = [`budget ${budget}`, tainted ? "TAINTED" : "clean"]
  if (status.pending_approvals) parts.push(`${status.pending_approvals} awaiting approval`)
  if (tripped?.tripped) parts.push(`breaker: ${tripped.tripped.rule}`)
  return `Aegis: ${parts.join(" | ")}`
}

export const AegisHud: Plugin = async ({ client }) => {
  let last = ""
  return {
    "tool.execute.after": async () => {
      const status = await fetchStatus()
      if (!status) return
      const line = summarize(status)
      if (line === last) return
      last = line
      // The TUI toast API may differ between OpenCode versions; fall back to the log.
      const tui = (client as any).tui
      if (tui?.showToast) {
        await tui.showToast({ body: { message: line, variant: line.includes("breaker") ? "error" : "info" } })
      } else {
        await (client as any).app?.log?.({ body: { service: "aegis-hud", level: "info", message: line } })
      }
    },
  }
}
