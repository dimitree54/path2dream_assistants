import { appendFileSync, mkdirSync } from "node:fs"

const LOG_DIR = "/tmp/notes-assistant/command-monitor"
const LOG_FILE = LOG_DIR + "/failed-commands.jsonl"
const OUTPUT_TAIL_LIMIT = 4000

export const CommandMonitorPlugin = async () => {
  return {
    "tool.execute.after": async (input, output) => {
      if (input.tool !== "bash") return
      const exit = output.metadata ? output.metadata.exit : undefined
      if (exit === 0) return
      const args = input.args ?? {}
      const record = {
        timestamp: new Date().toISOString(),
        sessionID: input.sessionID ?? null,
        callID: input.callID ?? null,
        command: args.command ?? null,
        description: args.description ?? null,
        workdir: args.workdir ?? null,
        exit: exit ?? null,
        output_tail: String(output.output ?? "").slice(-OUTPUT_TAIL_LIMIT),
      }
      mkdirSync(LOG_DIR, { recursive: true })
      appendFileSync(LOG_FILE, JSON.stringify(record) + "\n")
    },
  }
}
