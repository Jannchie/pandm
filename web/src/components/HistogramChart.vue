<script setup lang="ts">
import type { Run } from '../api'
import { HeatmapChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CHART_FONT, CHART_INK, fmtNum, fmtStep } from '../fmt'
import { getHistogram } from '../store'

echarts.use([
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
])

const props = defineProps<{ run: Run; metricKey: string }>()

// Axis title (define_metric x_label/y_label) — containLabel ignores the name, so
// the grid is padded for it below.
function axisName(label: string | undefined, axis: 'x' | 'y') {
  if (!label) return {}
  return {
    name: label,
    nameLocation: 'middle' as const,
    nameGap: axis === 'y' ? 40 : 26,
    nameTextStyle: {
      color: CHART_INK.mut,
      fontSize: 12,
      fontFamily: CHART_FONT,
    },
  }
}

const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
let resizeObs: ResizeObserver | null = null
let token = 0

const sig = computed(
  () => `${props.run.id}:${props.run.updated_at}~${props.metricKey}`,
)

async function update() {
  const my = ++token
  const h = await getHistogram(props.run, props.metricKey).catch(() => null)
  if (my !== token || !chart) return
  if (!h || h.steps.length === 0) {
    chart.clear()
    return
  }

  const nBins = Math.max(0, ...h.counts.map((c) => c.length))
  // colour by per-column (per-step) share, so the distribution's shape stays legible
  // even when later evals draw more samples; the raw count rides along in the tooltip.
  const data: [number, number, number][] = []
  const raw: number[][] = []
  h.counts.forEach((col, i) => {
    const colMax = Math.max(1, ...col)
    raw[i] = col
    col.forEach((cnt, j) => data.push([i, j, cnt / colMax]))
  })

  // y labels: author-supplied y_ticks (categorical bins), else bin centres from the
  // most recent snapshot's edges (a reasonable proxy)
  const spec = props.run.metric_meta?.[props.metricKey]
  const edges = h.bins[h.bins.length - 1] ?? []
  const yLabels = Array.from(
    { length: nBins },
    (_, j) =>
      spec?.y_ticks?.[j] ??
      (edges[j] !== undefined && edges[j + 1] !== undefined
        ? fmtNum((edges[j] + edges[j + 1]) / 2)
        : String(j)),
  )
  const xLabels = spec?.x_ticks
    ? h.steps.map((s, i) => spec.x_ticks?.[i] ?? fmtStep(s))
    : h.steps.map((s) => fmtStep(s))

  chart.setOption(
    {
      textStyle: { fontFamily: CHART_FONT },
      animation: false,
      grid: {
        left: spec?.y_label ? 28 : 6,
        right: 10,
        top: 8,
        bottom: spec?.x_label ? 24 : 2,
        containLabel: true,
      },
      tooltip: {
        backgroundColor: 'rgba(23,23,28,0.95)',
        borderColor: 'rgba(255,255,255,0.08)',
        padding: [6, 10],
        textStyle: {
          color: CHART_INK.fg,
          fontSize: 12,
          fontFamily: CHART_FONT,
        },
        extraCssText:
          'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.5);backdrop-filter:blur(8px)',
        formatter: (p: any) => {
          const [i, j] = p.value as [number, number, number]
          const lo = edges[j]
          const hi = edges[j + 1]
          const range =
            lo !== undefined && hi !== undefined
              ? `${fmtNum(lo)} – ${fmtNum(hi)}`
              : `bin ${j}`
          return `<div style="color:${CHART_INK.dim};font-size:11px">step ${fmtStep(h.steps[i])}</div>${range}<br>count ${raw[i]?.[j] ?? 0}`
        },
      },
      xAxis: {
        ...axisName(spec?.x_label, 'x'),
        type: 'category',
        data: xLabels,
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.12)' } },
        axisTick: { show: false },
        axisLabel: { color: CHART_INK.dim, fontSize: 11, hideOverlap: true },
        splitArea: { show: false },
      },
      yAxis: {
        ...axisName(spec?.y_label, 'y'),
        type: 'category',
        data: yLabels,
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.12)' } },
        axisTick: { show: false },
        axisLabel: { color: CHART_INK.dim, fontSize: 11, hideOverlap: true },
        splitArea: { show: false },
      },
      visualMap: {
        show: false,
        min: 0,
        max: 1,
        // dark background → bright crest, a perceptual density ramp
        inRange: {
          color: [
            '#15151b',
            '#243056',
            '#3a6ea8',
            '#5fb3f5',
            '#9adb5e',
            '#facc5f',
          ],
        },
      },
      series: [
        {
          type: 'heatmap',
          data,
          progressive: 4000,
          itemStyle: { borderWidth: 0 },
          emphasis: {
            itemStyle: { borderColor: 'rgba(255,255,255,0.4)', borderWidth: 1 },
          },
        },
      ],
    },
    { notMerge: true },
  )
}

onMounted(() => {
  chart = echarts.init(el.value!)
  resizeObs = new ResizeObserver(() => chart?.resize())
  resizeObs.observe(el.value!)
  update()
})

onBeforeUnmount(() => {
  resizeObs?.disconnect()
  chart?.dispose()
  chart = null
})

watch(sig, update)
</script>

<template>
  <div ref="el" class="w-full h-full min-h-0" />
</template>
