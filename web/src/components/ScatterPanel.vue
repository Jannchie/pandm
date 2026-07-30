<script setup lang="ts">
import { ScatterChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { LegacyGridContainLabel } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { runColor } from '../colors'
import { CHART_FONT, CHART_INK, fmtMetric } from '../fmt'
import {
  metricSpec,
  scatterKeys as keys,
  scatterPoints as points,
  selectRun,
  state,
  visibleRuns,
} from '../store'

// One point per run: pick a metric for each axis and read whether two quantities
// moved together across the runs you have. "Did explained_var rising come with
// avg_rank improving?" is a question about runs, not about steps, and answering it
// used to mean writing SQL by hand.
//
// The keys and points themselves come from the store — the tab bar labels this tab
// with the point count, so it can't wait for this component to mount.

echarts.use([
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegacyGridContainLabel,
  CanvasRenderer,
])

const AGGS = ['last', 'min', 'max'] as const

const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
let resizeObs: ResizeObserver | null = null

function render() {
  if (!chart || !keys.value.length) return
  const xSpec = metricSpec(state.scatterX)
  const ySpec = metricSpec(state.scatterY)
  const data = points.value.map((p) => ({
    value: [p.x, p.y],
    name: p.run.name,
    runId: p.run.id,
    // the compare selection reads as emphasis: selected runs are labelled and
    // larger, the rest are context
    symbolSize: state.selected.includes(p.run.id) ? 13 : 8,
    itemStyle: {
      color: runColor(p.run.id),
      opacity: state.selected.includes(p.run.id) ? 1 : 0.55,
      borderColor: 'rgba(0,0,0,0.45)',
      borderWidth: 1,
    },
    label: {
      show: state.selected.includes(p.run.id),
      position: 'top' as const,
      color: CHART_INK.mut,
      fontSize: 11,
      fontFamily: CHART_FONT,
      formatter: p.run.name,
    },
  }))
  chart.setOption(
    {
      textStyle: { fontFamily: CHART_FONT },
      animationDuration: 200,
      grid: { left: 28, right: 24, top: 16, bottom: 24, containLabel: true },
      xAxis: {
        name: state.scatterX,
        nameLocation: 'middle',
        nameGap: 28,
        nameTextStyle: {
          color: CHART_INK.mut,
          fontSize: 12,
          fontFamily: CHART_FONT,
        },
        type: 'value',
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: CHART_INK.dim,
          fontSize: 12,
          formatter: (v: number) => fmtMetric(v, xSpec?.unit),
        },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
      },
      yAxis: {
        name: state.scatterY,
        nameLocation: 'middle',
        nameGap: 46,
        nameTextStyle: {
          color: CHART_INK.mut,
          fontSize: 12,
          fontFamily: CHART_FONT,
        },
        type: 'value',
        scale: true,
        axisLabel: {
          color: CHART_INK.dim,
          fontSize: 12,
          formatter: (v: number) => fmtMetric(v, ySpec?.unit),
        },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(23,23,28,0.95)',
        borderColor: 'rgba(255,255,255,0.08)',
        padding: [6, 10],
        textStyle: {
          color: CHART_INK.fg,
          fontSize: 13,
          fontFamily: CHART_FONT,
        },
        extraCssText:
          'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.5);backdrop-filter:blur(8px)',
        formatter: (p: { data: { name: string; value: [number, number] } }) =>
          `<div>${p.data.name}</div>` +
          `<div style="color:${CHART_INK.dim};font-size:12px">${state.scatterX} ${fmtMetric(p.data.value[0], xSpec?.unit)}</div>` +
          `<div style="color:${CHART_INK.dim};font-size:12px">${state.scatterY} ${fmtMetric(p.data.value[1], ySpec?.unit)}</div>`,
      },
      series: [{ type: 'scatter', data, cursor: 'pointer' }],
    },
    { notMerge: true },
  )
}

onMounted(() => {
  chart = echarts.init(el.value!)
  resizeObs = new ResizeObserver(() => chart?.resize())
  resizeObs.observe(el.value!)
  // clicking a point selects that run, so the scatter is a way *into* its curves
  chart.on('click', (p: unknown) => {
    const runId = (p as { data?: { runId?: string } }).data?.runId
    if (runId) selectRun(runId, false)
  })
  render()
})

onBeforeUnmount(() => {
  resizeObs?.disconnect()
  chart?.dispose()
  chart = null
})

watch(
  () => [
    points.value,
    state.selected.join(','),
    state.scatterX,
    state.scatterY,
  ],
  render,
  { deep: true },
)
</script>

<template>
  <!-- the layout is always mounted (never behind a v-if): the chart element has to
       exist before echarts.init, and on first paint the runs haven't loaded yet -->
  <div class="h-full flex flex-col p-4 gap-3 min-h-0">
    <div
      v-if="keys.length"
      class="flex flex-wrap items-center gap-2 text-[13px] shrink-0"
    >
      <span class="text-fg-dim">x</span>
      <select v-model="state.scatterX" class="input-base px-2 py-0.5 font-mono">
        <option v-for="k in keys" :key="k" :value="k">{{ k }}</option>
      </select>
      <span class="text-fg-dim ml-1">y</span>
      <select v-model="state.scatterY" class="input-base px-2 py-0.5 font-mono">
        <option v-for="k in keys" :key="k" :value="k">{{ k }}</option>
      </select>
      <div class="flex items-center bg-elev rounded-lg p-0.5 ml-1">
        <button
          v-for="a in AGGS"
          :key="a"
          class="px-2 py-0.5 rounded-md transition-colors"
          :class="
            state.scatterAgg === a
              ? 'bg-panel text-fg shadow-sm'
              : 'text-fg-dim hover:text-fg-mut'
          "
          :title="`plot each run's ${a} value`"
          @click="state.scatterAgg = a"
        >
          {{ a }}
        </button>
      </div>
      <span class="text-fg-dim ml-auto tabular-nums"
        >{{ points.length }} of {{ visibleRuns.length }} runs</span
      >
    </div>
    <div class="card flex-1 min-h-0 p-3 relative">
      <div ref="el" class="w-full h-full min-h-0" />
      <div
        v-if="!keys.length"
        class="absolute inset-0 flex items-center justify-center text-[14.5px] text-fg-dim"
      >
        No metrics logged yet
      </div>
    </div>
  </div>
</template>
