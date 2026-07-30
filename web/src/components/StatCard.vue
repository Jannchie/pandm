<script setup lang="ts">
import type { ChartDesc } from '../store'
import { ref, watchEffect } from 'vue'
import { seriesColor } from '../colors'
import { fmtMetric } from '../fmt'
import { getSeries, lanes, resolveAxisSpec } from '../store'

// A single value plus the shape of how it got there — for the slow-moving
// quantities (lr, buffer rows, replay size) that spend a full chart slot to say
// one number. Drawn as inline SVG rather than an ECharts instance: 30 of these
// would otherwise be 30 canvases and 30 resize observers for 26 pixels of curve.
const props = defineProps<{ desc: ChartDesc }>()

interface Row {
  id: string
  label: string // lane name, member label, or "member · lane" when both vary
  color: string
  unit?: string
  value: number | null
  points: string // SVG polyline points, in the 0..100 × 0..26 viewBox
  area: string
}

const rows = ref<Row[]>([])

// last N points only: a sparkline this small can't show more, and the series is
// already in the shared cache the charts fill
const SPARK_POINTS = 120

function spark(values: number[]): { points: string; area: string } {
  const tail = values.slice(-SPARK_POINTS)
  if (tail.length < 2) return { points: '', area: '' }
  const lo = Math.min(...tail)
  const hi = Math.max(...tail)
  const span = hi - lo || 1
  const pts = tail.map((v, i) => {
    const x = (i / (tail.length - 1)) * 100
    const y = 24 - ((v - lo) / span) * 22
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })
  return {
    points: pts.join(' '),
    area: `${pts[0].split(',')[0]},26 ${pts.join(' ')} 100,26`,
  }
}

watchEffect(async () => {
  const members = props.desc.series
  const laneList = lanes.value
  const cells = laneList.flatMap((lane, li) =>
    members.map((m, mi) => ({ lane, li, m, mi })),
  )
  const built = await Promise.all(
    cells.map(async ({ lane, m, mi }) => {
      // a stitched lane concatenates its segments, same as the line charts
      const parts = await Promise.all(
        lane.runs.map((r) => getSeries(r, m.key).catch(() => null)),
      )
      const values = parts.flatMap((p) => p?.values ?? [])
      const last = lane.runs[lane.runs.length - 1]
      const label =
        members.length > 1 && laneList.length > 1
          ? `${m.label} · ${lane.label}`
          : members.length > 1
            ? m.label
            : lane.label
      return {
        id: `${lane.id}:${m.key}`,
        label,
        // several members in one card are identified by series colour, as in a
        // panel; one member across several lanes is identified by lane colour
        color: members.length > 1 ? seriesColor(mi) : lane.color,
        unit: resolveAxisSpec([m.key]).spec.unit,
        value:
          values.length > 0
            ? values[values.length - 1]
            : (last.stats?.[m.key]?.last ?? last.summary?.[m.key] ?? null),
        ...spark(values),
      }
    }),
  )
  rows.value = built
})
</script>

<template>
  <div class="flex flex-col gap-1.5 pb-2">
    <div v-for="r in rows" :key="r.id" class="min-w-0">
      <div class="flex items-baseline gap-2">
        <span class="text-[19px] font-mono tabular-nums text-fg leading-none">{{
          fmtMetric(r.value, r.unit)
        }}</span>
        <span
          v-if="rows.length > 1"
          class="text-[11.5px] font-mono truncate"
          :style="{ color: r.color }"
          >{{ r.label }}</span
        >
      </div>
      <svg
        v-if="r.points"
        class="block w-full h-[26px] mt-1"
        viewBox="0 0 100 26"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <polygon :points="r.area" :fill="r.color" fill-opacity="0.14" />
        <polyline
          :points="r.points"
          fill="none"
          :stroke="r.color"
          stroke-width="1.4"
          vector-effect="non-scaling-stroke"
        />
      </svg>
    </div>
    <div v-if="!rows.length" class="text-[12.5px] text-fg-dim">no data</div>
  </div>
</template>
