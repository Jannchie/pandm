/** Categorical palette tuned for dark backgrounds. */
export const PALETTE = [
  '#8b95f6', // indigo
  '#5fb3f5', // blue
  '#48cfad', // teal
  '#f5a35f', // orange
  '#ef7d9b', // pink
  '#b78cf7', // purple
  '#46c8d6', // cyan
  '#facc5f', // yellow
  '#9adb5e', // green
  '#f97f72', // red
]

const assigned = new Map<string, string>()

/** Stable per-run color: first come, first served, least-used first. */
export function runColor(runId: string): string {
  let color = assigned.get(runId)
  if (!color) {
    const used = new Set(assigned.values())
    color = PALETTE.find((c) => !used.has(c)) ?? PALETTE[assigned.size % PALETTE.length]
    assigned.set(runId, color)
  }
  return color
}
