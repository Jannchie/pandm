/** $ per GPU-hour, looked up from the GPU model nvidia-smi reported.
 *
 * Source: AWS EC2 on-demand, Linux, us-east-1 — the instance's hourly price
 * divided by its GPU count, so the number is auditable against the console.
 * A static snapshot on purpose: the live AWS Price List API needs SigV4-signed
 * requests (credentials) and its unauthenticated bulk file is hundreds of MB,
 * neither of which belongs in a dashboard. Bump the table when prices move.
 */
// ponytail: static price table, replace with a server-side cached fetch if
// someone actually needs it live
export const PRICE_SNAPSHOT = '2025-06'

interface Sku {
  match: RegExp // against the nvidia-smi name, e.g. "NVIDIA A100-SXM4-80GB"
  instance: string
  perGpuHour: number
}

// order matters: first match wins, so the more specific patterns go first
const SKUS: Sku[] = [
  { match: /H100/i, instance: 'p5.48xlarge', perGpuHour: 98.32 / 8 },
  { match: /A100.*80/i, instance: 'p4de.24xlarge', perGpuHour: 40.96 / 8 },
  { match: /A100/i, instance: 'p4d.24xlarge', perGpuHour: 32.77 / 8 },
  { match: /L40S/i, instance: 'g6e.xlarge', perGpuHour: 1.861 },
  { match: /\bL4\b/i, instance: 'g6.xlarge', perGpuHour: 0.805 },
  { match: /A10G/i, instance: 'g5.xlarge', perGpuHour: 1.006 },
  { match: /V100/i, instance: 'p3.2xlarge', perGpuHour: 3.06 },
  { match: /\bT4\b/i, instance: 'g4dn.xlarge', perGpuHour: 0.526 },
]

export interface GpuPrice {
  perGpuHour: number
  source: string // e.g. "aws p5.48xlarge"
}

/** Price for the first recognised GPU name, or null when nothing matches. */
export function gpuPrice(names: string[] | undefined): GpuPrice | null {
  for (const name of names ?? []) {
    const sku = SKUS.find((s) => s.match.test(name))
    if (sku)
      return { perGpuHour: sku.perGpuHour, source: `aws ${sku.instance}` }
  }
  return null
}
