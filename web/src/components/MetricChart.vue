<script setup lang="ts">
import { LineChart } from 'echarts/charts'
import { GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { runColor } from '../colors'
import { ema, fmtClock, fmtMetric, fmtNum, fmtStep } from '../fmt'
import { getSeries, metricSpec, selectedRuns, state } from '../store'

echarts.use([LineChart, GridComponent, MarkLineComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{ metricKey: string }>()

const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
let resizeObs: ResizeObserver | null = null
let token = 0

const sig = computed(() =>
  [
    selectedRuns.value.map((r) => `${r.id}:${r.updated_at}`).join('|'),
    props.metricKey,
    state.xAxis,
    state.logScale,
    state.smoothing,
  ].join('~'),
)

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

const zip = (xs: number[], ys: number[]) => xs.map((x, i) => [x, ys[i]] as [number, number])

async function update() {
  const my = ++token
  const runs = selectedRuns.value
  const spec = metricSpec(props.metricKey)
  const results = await Promise.all(runs.map((r) => getSeries(r, props.metricKey).catch(() => null)))
  if (my !== token || !chart) return

  // a fixed axis only makes sense on a linear scale; log mode keeps its auto range
  const fixed = spec && !state.logScale
  // baseline reference line (e.g. 0.5 chance level), drawn once on the first series
  const markLine =
    spec?.baseline !== undefined
      ? {
          silent: true,
          symbol: 'none' as const,
          animation: false,
          data: [
            {
              yAxis: spec.baseline,
              label: {
                formatter: fmtMetric(spec.baseline, spec.unit),
                position: 'insideEndTop' as const,
                color: '#8f8f9a',
                fontSize: 9,
              },
              lineStyle: { color: 'rgba(255,255,255,0.22)', type: 'dashed' as const, width: 1 },
            },
          ],
        }
      : undefined
  let markLinePlaced = false

  const series: object[] = []
  runs.forEach((run, i) => {
    const d = results[i]
    if (!d || d.steps.length === 0) return
    const color = runColor(run.id)
    let xs = state.xAxis === 'step' ? d.steps : d.ts.map((t) => t * 1000)
    let ys = d.values
    if (state.logScale) {
      const keep = ys.map((v) => v > 0)
      xs = xs.filter((_, j) => keep[j])
      ys = ys.filter((_, j) => keep[j])
    }
    if (state.smoothing > 0 && ys.length > 1) {
      // ghost of the raw signal behind the smoothed line, wandb-style
      series.push({
        name: `__ghost__${run.id}`,
        type: 'line',
        data: zip(xs, ys),
        showSymbol: false,
        silent: true,
        lineStyle: { color, width: 1, opacity: 0.18 },
        itemStyle: { color },
        emphasis: { disabled: true },
        animation: false,
        z: 1,
      })
      ys = ema(ys, state.smoothing)
    }
    series.push({
      name: run.name,
      id: run.id,
      type: 'line',
      data: zip(xs, ys),
      showSymbol: false,
      symbol: 'circle',
      symbolSize: 5,
      lineStyle: { color, width: 1.6 },
      itemStyle: { color },
      emphasis: { focus: 'none' },
      z: 2,
      ...(markLine && !markLinePlaced ? ((markLinePlaced = true), { markLine }) : {}),
    })
  })

  chart.setOption(
    {
      animationDuration: 200,
      animationDurationUpdate: 250,
      grid: { left: 6, right: 14, top: 12, bottom: 2, containLabel: true },
      xAxis: {
        type: state.xAxis === 'time' ? 'time' : 'value',
        min: 'dataMin',
        max: 'dataMax',
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: '#5b5b66',
          fontSize: 10,
          formatter: state.xAxis === 'step' ? (v: number) => fmtNum(v) : undefined,
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: state.logScale ? 'log' : 'value',
        scale: !fixed, // a declared range pins the axis; otherwise fit the data
        min: fixed && spec.min !== undefined ? spec.min : undefined,
        max: fixed && spec.max !== undefined ? spec.max : undefined,
        axisLabel: { color: '#5b5b66', fontSize: 10, formatter: (v: number) => fmtMetric(v, spec?.unit) },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
        splitNumber: 4,
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(23,23,28,0.95)',
        borderColor: 'rgba(255,255,255,0.08)',
        padding: [6, 10],
        textStyle: { color: '#e8e8ec', fontSize: 11 },
        extraCssText: 'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.5);backdrop-filter:blur(8px)',
        axisPointer: { type: 'line', lineStyle: { color: 'rgba(255,255,255,0.15)' } },
        formatter: (params: unknown) => {
          const rows = (params as { seriesName: string; color: string; value: [number, number] }[]).filter(
            (p) => !p.seriesName.startsWith('__ghost__'),
          )
          if (!rows.length) return ''
          const x = rows[0].value[0]
          const head = state.xAxis === 'step' ? `step ${fmtStep(x)}` : fmtClock(x / 1000)
          const body = rows
            .slice()
            .sort((a, b) => b.value[1] - a.value[1])
            .map(
              (p) =>
                `<div style="display:flex;align-items:center;gap:6px;margin-top:3px;min-width:150px">` +
                `<span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:${p.color}"></span>` +
                `<span style="color:#8f8f9a;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.seriesName)}</span>` +
                `<span style="margin-left:auto;padding-left:12px;font-family:ui-monospace,SFMono-Regular,monospace">${fmtMetric(p.value[1], spec?.unit)}</span></div>`,
            )
            .join('')
          return `<div style="font-size:10.5px;color:#5b5b66">${head}</div>${body}`
        },
      },
      series,
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
