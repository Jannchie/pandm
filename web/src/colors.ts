/** Categorical palette tuned for dark backgrounds, in two tiers.
 *
 *  Tier 1 (0–9) is the original palette — bright, high-lightness hues. Tier 2
 *  (10–19) extends it for the charts that genuinely need more than ten identities
 *  (a per-opponent panel is exactly ten lines; the eleventh used to collide with
 *  the first). It is deliberately a *darker* band of the same hue families, so the
 *  two tiers read as two layers rather than as twenty equals — and hues alternate
 *  warm/cool so neighbouring slots stay separable.
 *
 *  Verified with the dataviz palette validator against the #0f0f12 panel surface:
 *  every tier-2 slot passes the lightness band, chroma floor, normal-vision
 *  separation and 3:1 contrast checks. One adjacent pair (olive↔rose) lands at
 *  deutan ΔE 6.9, inside the band that requires secondary encoding — which these
 *  charts always carry (a legend for every multi-series chart, plus hover
 *  tooltips naming each line).
 */
export const PALETTE = [
  // tier 1 — bright
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
  // tier 2 — deep
  '#4f5bd5', // deep indigo
  '#c47f26', // amber
  '#2fa08a', // deep teal
  '#7a52c9', // deep violet
  '#d9536b', // rose
  '#8d9826', // olive
  '#2d8fc4', // steel blue
  '#c4573a', // burnt orange
  '#3fae62', // emerald
  '#c25fb0', // magenta
]

const assigned = new Map<string, string>()

/** Stable per-run color: first come, first served, least-used first. */
export function runColor(runId: string): string {
  let color = assigned.get(runId)
  if (!color) {
    const used = new Set(assigned.values())
    color =
      PALETTE.find((c) => !used.has(c)) ??
      PALETTE[assigned.size % PALETTE.length]
    assigned.set(runId, color)
  }
  return color
}

/** Per-series color inside a multi-line panel — positional, wraps around. Distinct
 *  from runColor: in a single-run panel each metric (reward/total, reward/shaping, …)
 *  is one line, coloured by its index rather than by run identity. */
export function seriesColor(index: number): string {
  return PALETTE[index % PALETTE.length]
}
