/** ECharts draws to a canvas that inherits neither the page font nor its CSS vars,
 *  so charts re-declare both here. Kept in sync with uno.config.ts by hand. */

// monospace (the font-mono token): keeps numeric axis labels & values aligned.
export const CHART_FONT =
  "'IBM Plex Mono', 'Sarasa Mono SC', 'JetBrains Mono', 'Cascadia Code', ui-monospace, SFMono-Regular, Menlo, Consolas, 'PingFang SC', 'Microsoft YaHei', monospace"

// text-colour ramp, mirroring the fg.* tokens — the single source of truth for
// chart text so a dark-mode legibility tweak is one edit, not a grep across charts.
export const CHART_INK = {
  fg: '#f4f4f7', // primary: tooltip body, hovered values   (== fg.DEFAULT)
  mut: '#b4b4be', // secondary: axis labels, legend, series  (≈ fg.mut)
  dim: '#86868f', // tertiary: y-axis ticks, tooltip head    (≈ fg.dim)
  faint: '#54545e', // faintest: inactive legend items
}

/** Compact, chart-friendly number formatting. */
export function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  if (v === 0) return '0'
  const abs = Math.abs(v)
  if (abs >= 1e9) return `${trim(v / 1e9)}B`
  if (abs >= 1e6) return `${trim(v / 1e6)}M`
  if (abs >= 1e4) return `${trim(v / 1e3)}k`
  if (abs >= 1) return trim(v, 4)
  if (abs >= 1e-3) return trim(v, 4)
  return v.toExponential(2)
}

function trim(v: number, sig = 3): string {
  return Number(v.toPrecision(sig)).toString()
}

/** 0.7345 -> "73.5%". For metrics declared with unit:"percent". */
export function fmtPercent(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  return `${trim(v * 100)}%`
}

/** Format a value the way its metric was declared (percent-aware), else compact number. */
export function fmtMetric(v: number | null | undefined, unit?: string): string {
  return unit === 'percent' ? fmtPercent(v) : fmtNum(v)
}

export function fmtStep(v: number): string {
  return v.toLocaleString('en-US')
}

export function timeAgo(ts: number): string {
  const sec = Math.max(0, Date.now() / 1000 - ts)
  if (sec < 60) return 'just now'
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
  if (sec < 86400 * 30) return `${Math.floor(sec / 86400)}d ago`
  return new Date(ts * 1000).toLocaleDateString()
}

export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return `${h}h ${m.toString().padStart(2, '0')}m`
  if (m > 0) return `${m}m ${(s % 60).toString().padStart(2, '0')}s`
  return `${s}s`
}

export function fmtClock(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Debiased EMA, tensorboard-style smoothing. */
export function ema(values: number[], weight: number): number[] {
  if (weight <= 0) return values
  const out: number[] = []
  let last = 0
  let n = 0
  for (const v of values) {
    last = last * weight + (1 - weight) * v
    n++
    out.push(last / (1 - Math.pow(weight, n)))
  }
  return out
}
