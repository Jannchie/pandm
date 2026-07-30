<script setup lang="ts">
import type { Run, Series } from '../api'
import type { ChartDesc, ChartSeriesDesc } from '../store'
import { BarChart, CustomChart, LineChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  ToolboxComponent,
  TooltipComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
// echarts 6 made grid.containLabel opt-in; without this the axis labels get
// clipped instead of the grid shrinking to fit them.
import { LegacyGridContainLabel } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { runColor, seriesColor } from '../colors'
import {
  CHART_FONT,
  CHART_INK,
  ema,
  fmtClock,
  fmtDuration,
  fmtMetric,
  fmtNum,
  fmtStep,
} from '../fmt'
import { getSeries, metricSpec, selectedRuns, state } from '../store'

echarts.use([
  BarChart,
  CustomChart,
  LineChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  ToolboxComponent,
  DataZoomComponent,
  LegacyGridContainLabel,
  CanvasRenderer,
])

const props = defineProps<{ desc: ChartDesc }>()

// Axis title (define_metric x_label/y_label): a centred name along the axis.
// ECharts' containLabel does NOT reserve room for the name, so callers also pad
// the grid (left for y, bottom for x) by `axisNameGap` when a label is set.
const axisNameGap = 22
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
let lastRenderKey = '' // skip the (expensive) setOption when a poll brought no new data for this chart

const sig = computed(() =>
  [
    selectedRuns.value.map((r) => `${r.id}:${r.updated_at}`).join('|'),
    props.desc.id,
    props.desc.series.map((s) => s.key).join(','),
    state.xAxis,
    state.xRange ? state.xRange.join(',') : '',
    state.logScale,
    state.smoothing,
  ].join('~'),
)

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// setOption with notMerge wipes user interaction state; carry the legend's
// show/hide selection across live-data redraws. echarts matches by series name,
// so series added/removed by a poll are handled gracefully.
function applyOption(option: echarts.EChartsCoreOption) {
  if (!chart) return
  const prev = (
    chart.getOption()?.legend as
      | { selected?: Record<string, boolean> }[]
      | undefined
  )?.[0]?.selected
  chart.setOption(option, { notMerge: true })
  if (prev) chart.setOption({ legend: { selected: prev } })
}

const zip = (xs: number[], ys: number[]) =>
  xs.map((x, i) => [x, ys[i]] as [number, number])
// step -> raw step; time -> absolute ms (echarts time axis); rtime -> seconds
// elapsed since this run started, so runs launched at different wall-clock times
// line up on a common relative axis.
const xOf = (d: Series, run: Run) =>
  state.xAxis === 'step'
    ? d.steps
    : state.xAxis === 'rtime'
      ? d.ts.map((t) => t - run.created_at)
      : d.ts.map((t) => t * 1000)

interface Cell {
  run: Run
  s: ChartSeriesDesc
  sIdx: number
}

// Category bars (define_metric kind="bar"): one bar per series, height = its
// latest value (run.stats[key].last — already in hand, no series fetch). One run
// colours by series; several runs draw grouped bars coloured by run. Bar is the
// one chart that ignores option A's single-run-only panel rule (a one-category
// bar is meaningless), so it always groups.
function renderBar() {
  if (!chart) return
  const desc = props.desc
  const runs = selectedRuns.value
  const spec = metricSpec(desc.series[0].key)
  const grouped = runs.length > 1
  const categories = desc.series.map((s) => s.label)

  // a bar's height is the metric's latest value (stats.last), or its author-written
  // summary scalar when the value lives there (run.summary) rather than in a series.
  const valueOf = (run: Run, key: string) =>
    run.stats?.[key]?.last ?? run.summary?.[key] ?? null
  const series: object[] = grouped
    ? runs.map((run) => ({
        name: run.name,
        type: 'bar',
        data: desc.series.map((s) => valueOf(run, s.key)),
        itemStyle: { color: runColor(run.id), borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 26,
      }))
    : [
        {
          type: 'bar',
          data: desc.series.map((s, i) => ({
            value: runs[0] ? valueOf(runs[0], s.key) : null,
            itemStyle: { color: seriesColor(i), borderRadius: [3, 3, 0, 0] },
          })),
          barWidth: '54%',
        },
      ]

  applyOption({
    animationDuration: 200,
    animationDurationUpdate: 250,
    textStyle: { fontFamily: CHART_FONT },
    legend: grouped
      ? {
          show: true,
          type: 'scroll',
          top: 0,
          textStyle: {
            color: CHART_INK.mut,
            fontSize: 11,
            fontFamily: CHART_FONT,
          },
          icon: 'roundRect',
          itemWidth: 10,
          itemHeight: 3,
          itemGap: 10,
          inactiveColor: CHART_INK.faint,
        }
      : { show: false },
    grid: {
      left: spec?.y_label ? 6 + axisNameGap : 6,
      right: 14,
      top: grouped ? 28 : 12,
      bottom: spec?.x_label ? 2 + axisNameGap : 2,
      containLabel: true,
    },
    xAxis: {
      ...axisName(spec?.x_label, 'x'),
      type: 'category',
      data: spec?.x_ticks
        ? categories.map((c, i) => spec.x_ticks?.[i] ?? c)
        : categories,
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.12)' } },
      axisTick: { show: false },
      axisLabel: {
        color: CHART_INK.mut,
        fontSize: 11,
        interval: 0,
        hideOverlap: true,
      },
    },
    yAxis: {
      ...axisName(spec?.y_label, 'y'),
      type: 'value',
      scale: !spec,
      min: spec?.min,
      max: spec?.max,
      axisLabel: {
        color: CHART_INK.dim,
        fontSize: 12,
        formatter: (v: number) => fmtMetric(v, spec?.unit),
      },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
      splitNumber: 4,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: 'rgba(23,23,28,0.95)',
      borderColor: 'rgba(255,255,255,0.08)',
      padding: [6, 10],
      textStyle: { color: CHART_INK.fg, fontSize: 13, fontFamily: CHART_FONT },
      extraCssText:
        'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.5);backdrop-filter:blur(8px)',
      valueFormatter: (v: number) => fmtMetric(v, spec?.unit),
    },
    series,
  })
}

async function update() {
  const my = ++token
  const runs = selectedRuns.value
  const desc = props.desc
  if (desc.kind === 'bar') {
    // bars draw from stats.last/summary (no fetch), but still deserve the same
    // bail-out: a poll that brought no new values must not replay the bar animation
    const renderKey = JSON.stringify(
      runs.map((r) => [
        r.id,
        desc.series.map(
          (s) => r.stats?.[s.key]?.last ?? r.summary?.[s.key] ?? null,
        ),
      ]),
    )
    if (renderKey === lastRenderKey) return
    lastRenderKey = renderKey
    renderBar()
    return
  }
  const bySeries = desc.colorBy === 'series'
  const spec = metricSpec(desc.series[0].key)
  const drawBands = !state.logScale // a shaded CI only makes sense on a linear scale

  // fetch every (run, series) mean, plus band bounds where a series declares them
  const cells: Cell[] = []
  for (const run of runs)
    desc.series.forEach((s, sIdx) => cells.push({ run, s, sIdx }))
  const fetched = await Promise.all(
    cells.map(async (c) => {
      const mean = await getSeries(c.run, c.s.key).catch(() => null)
      if (!mean || !c.s.band || !drawBands)
        return {
          c,
          mean,
          lo: null as Series | null,
          hi: null as Series | null,
        }
      const [lo, hi] = await Promise.all([
        getSeries(c.run, c.s.band.lo).catch(() => null),
        getSeries(c.run, c.s.band.hi).catch(() => null),
      ])
      return { c, mean, lo, hi }
    }),
  )
  if (my !== token || !chart) return

  // `sig` flips every poll because a running run's updated_at bumps each tick, so
  // update() re-runs — but the tail fetch often adds nothing for *this* chart's
  // keys. Fingerprint the data + render-affecting settings and bail before the
  // costly setOption when nothing actually changed.
  const renderKey = JSON.stringify([
    state.xAxis,
    state.xRange,
    state.logScale,
    state.smoothing,
    fetched.map(({ c, mean, lo, hi }) => [
      c.run.id,
      c.s.key,
      mean?.steps.length ?? 0,
      mean?.steps[mean.steps.length - 1] ?? -1,
      lo?.steps.length ?? 0,
      hi?.steps.length ?? 0,
    ]),
  ])
  if (renderKey === lastRenderKey) return
  lastRenderKey = renderKey

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
                color: CHART_INK.mut,
                fontSize: 11,
              },
              lineStyle: {
                color: 'rgba(255,255,255,0.22)',
                type: 'dashed' as const,
                width: 1,
              },
            },
          ],
        }
      : undefined
  let markLinePlaced = false

  // Zoomed-in y-rescale: while an x-window is active, fit the y-axis to the data
  // *inside* that window instead of the whole series, so a magnified span fills the
  // panel. Log mode keeps its own auto range; a define_metric-pinned end is left
  // untouched below. We fold the same values we draw (smoothed line + band bounds),
  // skipping points outside the window — no interpolation, so a window with no
  // vertex inside falls back to the full-data fit.
  const win = state.xRange
  const autoRange = !!win && !state.logScale
  let yLo = Infinity
  let yHi = -Infinity
  const foldWindow = (xa: number[], ya: number[]) => {
    for (let j = 0; j < xa.length; j++) {
      const x = xa[j]
      const y = ya[j]
      if (x < win![0] || x > win![1] || !Number.isFinite(y)) continue
      if (y < yLo) yLo = y
      if (y > yHi) yHi = y
    }
  }

  const series: object[] = []
  const legendNames: string[] = []
  for (const { c, mean, lo, hi } of fetched) {
    if (!mean || mean.steps.length === 0) continue
    const color = bySeries ? seriesColor(c.sIdx) : runColor(c.run.id)
    const name = bySeries ? c.s.label : c.run.name

    // shaded confidence band: one filled polygon tracing lo forward then hi back,
    // tinted to match its mean line (per-run in comparison view, per-series in a
    // panel). A custom series (not stacked areas) so it renders correctly on
    // value/time axes, where line stacking misplaces the fill. lo/hi are co-logged
    // with the mean, so they're index-aligned; if they desync we skip the band.
    if (
      lo &&
      hi &&
      lo.values.length > 0 &&
      lo.values.length === hi.values.length
    ) {
      const bx = xOf(lo, c.run)
      const pts: [number, number, number][] = bx.map((x, j) => [
        x,
        lo.values[j],
        hi.values[j],
      ])
      if (autoRange) {
        foldWindow(bx, lo.values)
        foldWindow(bx, hi.values)
      }
      series.push({
        name: `__band__${name}`,
        type: 'custom',
        silent: true,
        animation: false,
        z: 0,
        clip: true,
        encode: { x: 0, y: [1, 2] }, // x from dim 0, y extent from lo+hi
        data: pts,
        renderItem: (
          params: { dataIndex: number },
          api: { coord: (v: number[]) => number[] },
        ) => {
          if (params.dataIndex !== 0) return undefined // draw the whole band once
          const ring: number[][] = []
          for (let j = 0; j < pts.length; j++)
            ring.push(api.coord([pts[j][0], pts[j][1]]))
          for (let j = pts.length - 1; j >= 0; j--)
            ring.push(api.coord([pts[j][0], pts[j][2]]))
          return {
            type: 'polygon',
            shape: { points: ring },
            style: { fill: color, opacity: 0.16 },
          }
        },
      })
    }

    let xs = xOf(mean, c.run)
    let ys = mean.values
    if (state.logScale) {
      const keep = ys.map((v) => v > 0)
      xs = xs.filter((_, j) => keep[j])
      ys = ys.filter((_, j) => keep[j])
    }
    if (state.smoothing > 0 && ys.length > 1) {
      // ghost of the raw signal behind the smoothed line, wandb-style
      series.push({
        name: `__ghost__${name}`,
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
    if (autoRange) foldWindow(xs, ys)
    legendNames.push(name)
    series.push({
      name,
      type: 'line',
      data: zip(xs, ys),
      showSymbol: false,
      symbol: 'circle',
      symbolSize: 5,
      lineStyle: { color, width: 1.6 },
      itemStyle: { color },
      emphasis: { focus: 'none' },
      z: 2,
      ...(markLine && !markLinePlaced
        ? ((markLinePlaced = true), { markLine })
        : {}),
    })
  }

  // padded window fit: a 5% margin keeps the extreme points off the frame edge;
  // a flat line (span 0) pads by its magnitude so it doesn't collapse to a sliver.
  const winFit =
    autoRange && yLo <= yHi
      ? (() => {
          const span = yHi - yLo
          const pad = span > 0 ? span * 0.05 : Math.abs(yHi) * 0.05 || 1
          return { min: yLo - pad, max: yHi + pad }
        })()
      : null
  // a define_metric-declared bound sets the axis *default*, but an active zoom
  // window overrides it — so you can still magnify inside a pinned range and read
  // the detail. Unzoomed, the declared bound (or full-data auto) stands.
  const pinMin = fixed && spec?.min !== undefined
  const pinMax = fixed && spec?.max !== undefined

  applyOption({
    animationDuration: 200,
    animationDurationUpdate: 250,
    textStyle: { fontFamily: CHART_FONT },
    // a panel legend names the lines; single-key charts keep the lean top margin
    legend: bySeries
      ? {
          show: true,
          type: 'scroll',
          top: 0,
          data: legendNames,
          textStyle: {
            color: CHART_INK.mut,
            fontSize: 11,
            fontFamily: CHART_FONT,
          },
          icon: 'roundRect',
          itemWidth: 10,
          itemHeight: 3,
          itemGap: 10,
          inactiveColor: CHART_INK.faint,
        }
      : { show: false },
    grid: {
      left: spec?.y_label ? 6 + axisNameGap : 6,
      right: 14,
      top: bySeries ? 28 : 12,
      bottom: spec?.x_label ? 2 + axisNameGap : 2,
      containLabel: true,
    },
    // drag-select to zoom x: the toolbox dataZoom feature's brush controller powers
    // the rubber-band select; its `datazoom` event lifts the picked range into
    // state.xRange (below), which every chart renders via the inside dataZoom's
    // startValue/endValue — so one drag zooms them all. filterMode 'none' clips the
    // axis without dropping points, keeping bands/ghosts aligned.
    //
    // The toolbox MUST render (show:true) — ToolboxView.render bails immediately
    // when show is false, so the feature (and the brush controller that
    // `takeGlobalCursor` arms below) is never built and the drag does nothing.
    // Render it off-screen instead of hiding it: the drag behaviour lives on, while
    // the unwanted icon stays out of sight and out of reach.
    toolbox: {
      show: true,
      left: -9999,
      top: -9999,
      feature: {
        dataZoom: { yAxisIndex: 'none', filterMode: 'none' },
      },
    },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: 0,
        filterMode: 'none',
        zoomOnMouseWheel: false, // inside is only the zoom *target*; drag-select drives it
        moveOnMouseMove: false,
        moveOnMouseWheel: false,
        ...(state.xRange
          ? { startValue: state.xRange[0], endValue: state.xRange[1] }
          : { start: 0, end: 100 }),
      },
    ],
    xAxis: {
      ...axisName(spec?.x_label, 'x'),
      type: state.xAxis === 'time' ? 'time' : 'value',
      min: 'dataMin',
      max: 'dataMax',
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: CHART_INK.dim,
        fontSize: 12,
        formatter:
          state.xAxis === 'step'
            ? (v: number) => fmtNum(v)
            : state.xAxis === 'rtime'
              ? (v: number) => fmtDuration(v)
              : undefined,
      },
      splitLine: { show: false },
    },
    yAxis: {
      ...axisName(spec?.y_label, 'y'),
      type: state.logScale ? 'log' : 'value',
      scale: !fixed, // a declared range pins the axis; otherwise fit the data
      // zoom window fit wins (override even a declared bound); else the declared
      // pin; else full-data auto.
      min: winFit ? winFit.min : pinMin ? spec!.min : undefined,
      max: winFit ? winFit.max : pinMax ? spec!.max : undefined,
      axisLabel: {
        color: CHART_INK.dim,
        fontSize: 12,
        formatter: (v: number) => fmtMetric(v, spec?.unit),
      },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
      splitNumber: 4,
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(23,23,28,0.95)',
      borderColor: 'rgba(255,255,255,0.08)',
      padding: [6, 10],
      textStyle: { color: CHART_INK.fg, fontSize: 13, fontFamily: CHART_FONT },
      extraCssText:
        'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.5);backdrop-filter:blur(8px)',
      axisPointer: {
        type: 'line',
        lineStyle: { color: 'rgba(255,255,255,0.15)' },
      },
      formatter: (params: unknown) => {
        const rows = (
          params as {
            seriesName: string
            color: string
            value: [number, number]
          }[]
        ).filter(
          (p) => !p.seriesName.startsWith('__'), // hide ghost + band helper series
        )
        if (!rows.length) return ''
        const x = rows[0].value[0]
        const head =
          state.xAxis === 'step'
            ? `step ${fmtStep(x)}`
            : state.xAxis === 'rtime'
              ? `+${fmtDuration(x)}`
              : fmtClock(x / 1000)
        const body = rows
          .slice()
          .sort((a, b) => b.value[1] - a.value[1])
          .map(
            (p) =>
              `<div style="display:flex;align-items:center;gap:6px;margin-top:3px;min-width:150px">` +
              `<span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:${p.color}"></span>` +
              `<span style="color:${CHART_INK.mut};max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.seriesName)}</span>` +
              `<span style="margin-left:auto;padding-left:12px;font-family:${CHART_FONT}">${fmtMetric(p.value[1], spec?.unit)}</span></div>`,
          )
          .join('')
        return `<div style="font-size:12px;color:${CHART_INK.dim}">${head}</div>${body}`
      },
    },
    series,
  })
  // re-arm drag-to-zoom: a notMerge redraw drops the active selection cursor
  chart.dispatchAction({
    type: 'takeGlobalCursor',
    key: 'dataZoomSelect',
    dataZoomSelectActive: true,
  })
}

onMounted(() => {
  chart = echarts.init(el.value!)
  resizeObs = new ResizeObserver(() => chart?.resize())
  resizeObs.observe(el.value!)
  // drag-select a span -> lift it into the shared state.xRange so every chart zooms.
  // toolbox area-select reports absolute values in event.batch; fall back to the
  // dataZoom component's startValue/endValue if a build delivers them only there.
  chart.on('datazoom', (params: unknown) => {
    const batch = (
      params as { batch?: { startValue?: number; endValue?: number }[] }
    ).batch?.[0]
    const dz = (
      chart?.getOption().dataZoom as
        | { startValue?: number; endValue?: number }[]
        | undefined
    )?.[0]
    const sv = batch?.startValue ?? dz?.startValue
    const ev = batch?.endValue ?? dz?.endValue
    if (sv == null || ev == null || sv === ev) return
    const next: [number, number] = sv < ev ? [sv, ev] : [ev, sv]
    if (
      !state.xRange ||
      state.xRange[0] !== next[0] ||
      state.xRange[1] !== next[1]
    )
      state.xRange = next
  })
  chart.getZr().on('dblclick', () => {
    state.xRange = null // double-click anywhere clears the shared zoom
  })
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
