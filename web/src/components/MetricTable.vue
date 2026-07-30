<script setup lang="ts">
import type { ChartDesc } from '../store'
import { computed } from 'vue'
import { fmtMetric } from '../fmt'
import { lanes, metricSpec, selectedRuns } from '../store'

// A panel of one row per entity (an opponent, a bucket, a seat) × one column per
// metric. Denominator-ish data — per-opponent sample counts, per-bucket win rates —
// is a table by nature; as eight lines on one chart it reads as noise and the counts
// can't be read off at all.
//
// Shape comes from the members' declarations: define_metric(row=) names the entity,
// series= names the column. Members arrive in the order define_metric was called
// (MetricsPanel sorts them), so both axes read the way the author wrote them — and
// nobody has to rename a key to reorder a column.
const props = defineProps<{ desc: ChartDesc }>()

// the run whose values fill the table: a cell holds one number, so a table is
// per-lane (a multi-run comparison draws one table each) and reads the lane's latest
// segment — a stitched group's newest run is the one whose values are current
const run = computed(() => {
  const lane = props.desc.lane ?? lanes.value[0]
  return lane?.runs[lane.runs.length - 1] ?? selectedRuns.value[0]
})

const model = computed(() => {
  const cols: string[] = []
  const rowOrder: string[] = []
  // row -> column -> key
  const cells = new Map<string, Map<string, string>>()
  for (const s of props.desc.series) {
    const row = s.row ?? s.key
    const col = s.row ? s.label : 'value' // no row= declared: degenerate one-column table
    if (!cols.includes(col)) cols.push(col)
    if (!cells.has(row)) {
      cells.set(row, new Map())
      rowOrder.push(row)
    }
    cells.get(row)!.set(col, s.key)
  }
  const r = run.value
  return {
    cols,
    rows: rowOrder.map((row) => ({
      row,
      values: cols.map((col) => {
        const key = cells.get(row)?.get(col)
        if (!key) return { text: '', title: '' }
        const v = r?.stats?.[key]?.last ?? r?.summary?.[key] ?? null
        return {
          text: v === null ? '–' : fmtMetric(v, metricSpec(key)?.unit),
          title: key,
        }
      }),
    })),
  }
})
</script>

<template>
  <div class="overflow-x-auto pb-2">
    <table class="w-full text-[13px] border-collapse">
      <thead>
        <tr>
          <th
            class="text-left font-normal text-[11.5px] text-fg-dim pb-1 pr-3"
          />
          <th
            v-for="c in model.cols"
            :key="c"
            class="text-right font-normal text-[11.5px] text-fg-dim pb-1 pl-3 whitespace-nowrap"
          >
            {{ c }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="r in model.rows"
          :key="r.row"
          class="border-t border-border/70"
        >
          <td class="py-1 pr-3 text-fg-mut truncate max-w-40" :title="r.row">
            {{ r.row }}
          </td>
          <td
            v-for="(v, i) in r.values"
            :key="i"
            class="py-1 pl-3 text-right font-mono tabular-nums text-fg"
            :title="v.title"
          >
            {{ v.text }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
