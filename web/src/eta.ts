/** Completion-time estimation from the progress a run reports.
 *
 * The server stores only the latest (current, total, progress_ts). We keep one
 * prior sample per run in memory and derive the rate from how much progress
 * moved between two polls (EMA-smoothed). Before a second sample exists we fall
 * back to the lifetime average (current / time-since-start) so the first poll
 * already shows an ETA. The extrapolation is anchored at progress_ts, so the
 * "time left" counts down on its own between polls.
 */
import type { Run } from './api'

interface Sample {
  current: number
  ts: number
  rate: number | null // units per second
}

const samples = new Map<string, Sample>()

// weight kept on the previous rate; higher = steadier, slower to react to speedups
const SMOOTH = 0.4

export interface Eta {
  fraction: number | null // 0..1 completed, null when total is unknown
  finishAt: number | null // epoch seconds, null until a positive rate is known
  ratePerSec: number | null
}

export function estimateEta(run: Run): Eta | null {
  const current = run.progress
  if (current == null) return null // run never reported progress
  const total = run.progress_total
  const ts = run.progress_ts ?? run.updated_at
  const fraction =
    total != null && total > 0
      ? Math.min(1, Math.max(0, current / total))
      : null

  const prev = samples.get(run.id)
  let rate = prev?.rate ?? null
  if (run.progress_ts != null) {
    if (prev && ts > prev.ts && current > prev.current) {
      const inst = (current - prev.current) / (ts - prev.ts)
      rate = rate != null ? rate * SMOOTH + inst * (1 - SMOOTH) : inst
    } else if (rate == null && current > 0 && ts > run.created_at) {
      rate = current / (ts - run.created_at) // first-sample fallback: lifetime average
    }
    // advance the sample only when progress actually moved, so repeated
    // re-renders between polls can't corrupt the derived rate
    if (!prev || ts > prev.ts) samples.set(run.id, { current, ts, rate })
  }

  const finishAt =
    rate != null && rate > 0 && total != null && total > current
      ? ts + (total - current) / rate
      : null
  return { fraction, finishAt, ratePerSec: rate }
}

export function clearEta(runId: string): void {
  samples.delete(runId)
}
