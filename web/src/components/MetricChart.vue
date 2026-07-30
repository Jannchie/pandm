<script setup lang="ts">
import type { MetricSpec, Run, Series } from '../api'
import type { ChartDesc, ChartSeriesDesc, Lane } from '../store'
import { BarChart, CustomChart, LineChart, ScatterChart } from 'echarts/charts'
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
import { seriesColor } from '../colors'
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
import { getSeries, lanes, resolveAxisSpec, state } from '../store'

echarts.use([
  BarChart,
  CustomChart,
  LineChart,
  ScatterChart,
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

// Click a legend entry to isolate that line; click it again to bring the rest back.
// "Ten lines" stops being a problem the moment you can look at one of them — which
// is why this replaces ECharts' default per-entry hide: hiding nine is not a
// workflow. Isolation is ours rather than the legend's so a line's band and
// smoothing ghost (helper series, absent from the legend) follow it.
const isolated = ref<string | null>(null)

// a chart pinned to one lane (keepPanels drawing the same panel per run) draws only
// that lane; everything else draws all of them
const chartLanes = computed<Lane[]>(() =>
  props.desc.lane ? [props.desc.lane] : lanes.value,
)

const sig = computed(() =>
  [
    chartLanes.value
      .map((l) => l.runs.map((r) => `${r.id}:${r.updated_at}`).join('+'))
      .join('|'),
    props.desc.id,
    props.desc.series.map((s) => s.key).join(','),
    state.xAxis,
    state.xRange ? state.xRange.join(',') : '',
    state.logScale,
    state.smoothing,
    isolated.value ?? '',
  ].join('~'),
)

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

const zip = (xs: number[], ys: number[]) =>
  xs.map((x, i) => [x, ys[i]] as [number, number])
// step -> raw step; time -> absolute ms (echarts time axis); rtime -> seconds
// elapsed since the lane started, so runs launched at different wall-clock times
// line up on a common relative axis. For a stitched lane that origin is the *first*
// segment's start, so the segments stay in order (the idle gap between a finish and
// the next resume rides along — it really did pass).
const xOf = (d: Series, run: Run, origin: number) =>
  state.xAxis === 'step'
    ? d.steps
    : state.xAxis === 'rtime'
      ? d.ts.map((t) => t - origin)
      : d.ts.map((t) => t * 1000)

/** One lane's series for one key, with the lane's segments read back to back. */
async function fetchLane(
  lane: Lane,
  key: string,
): Promise<{ xs: number[]; ys: number[] } | null> {
  const origin = lane.runs[0].created_at
  const parts = await Promise.all(
    lane.runs.map((r) =>
      getSeries(r, key)
        .then((d) => ({ r, d }))
        .catch(() => null),
    ),
  )
  const xs: number[] = []
  const ys: number[] = []
  for (const p of parts) {
    if (!p || p.d.steps.length === 0) continue
    xs.push(...xOf(p.d, p.r, origin))
    ys.push(...p.d.values)
  }
  return xs.length ? { xs, ys } : null
}

interface Cell {
  lane: Lane
  s: ChartSeriesDesc
  sIdx: number
}

// a bar's height is the metric's latest value (stats.last), or its author-written
// summary scalar when the value lives there (run.summary) rather than in a series.
// A stitched lane answers with its latest segment that has the key at all.
function latestOf(lane: Lane, key: string): number | null {
  for (let i = lane.runs.length - 1; i >= 0; i--) {
    const r = lane.runs[i]
    const v = r.stats?.[key]?.last ?? r.summary?.[key]
    if (v !== null && v !== undefined) return v
  }
  return null
}

// which y-axis a panel member draws against (define_metric axis="right")
const sideOf = (s: ChartSeriesDesc) => (s.axis === 'right' ? 1 : 0)

// Category bars (define_metric kind="bar"): one bar per series, height = its
// latest value (run.stats[key].last — already in hand, no series fetch). One lane
// colours by series; several draw grouped bars coloured by lane. Bar is the one
// chart that ignores option A's single-run-only panel rule (a one-category bar is
// meaningless), so it always groups.
function renderBar() {
  if (!chart) return
  const desc = props.desc
  const laneList = chartLanes.value
  const { spec } = resolveAxisSpec(desc.series.map((s) => s.key))
  const grouped = laneList.length > 1
  const categories = desc.series.map((s) => s.label)

  const series: object[] = grouped
    ? laneList.map((lane) => ({
        name: lane.label,
        type: 'bar',
        data: desc.series.map((s) => latestOf(lane, s.key)),
        itemStyle: { color: lane.color, borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 26,
      }))
    : [
        {
          type: 'bar',
          data: desc.series.map((s, i) => ({
            value: laneList[0] ? latestOf(laneList[0], s.key) : null,
            itemStyle: { color: seriesColor(i), borderRadius: [3, 3, 0, 0] },
          })),
          barWidth: '54%',
        },
      ]

  applyOption({
    animationDuration: 200,
    animationDurationUpdate: 250,
    textStyle: { fontFamily: CHART_FONT },
    legend: grouped ? legendOption() : { show: false },
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
      scale: Object.keys(spec).length === 0, // a declared spec owns the range; else fit the data
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
      ...tooltipChrome(),
      valueFormatter: (v: number) => fmtMetric(v, spec?.unit),
    },
    series,
  })
}

const tooltipChrome = () => ({
  backgroundColor: 'rgba(23,23,28,0.95)',
  borderColor: 'rgba(255,255,255,0.08)',
  padding: [6, 10] as [number, number],
  textStyle: { color: CHART_INK.fg, fontSize: 13, fontFamily: CHART_FONT },
  extraCssText:
    'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.5);backdrop-filter:blur(8px)',
})

// A legend is shown whenever the chart draws more than one line — identity by
// colour alone made a ten-line chart unreadable, and the legend is also the handle
// for hover-highlight and click-to-isolate.
function legendOption(names?: string[]) {
  return {
    show: true,
    type: 'scroll' as const,
    top: 0,
    ...(names ? { data: names } : {}),
    textStyle: { color: CHART_INK.mut, fontSize: 11, fontFamily: CHART_FONT },
    icon: 'roundRect',
    itemWidth: 10,
    itemHeight: 3,
    itemGap: 10,
    inactiveColor: CHART_INK.faint,
    selectorLabel: { show: false },
  }
}

// setOption with notMerge wipes user interaction state. Legend *selection* is no
// longer carried across redraws: isolation lives in `isolated` and is re-applied by
// filtering the series, which keeps the helper series honest.
function applyOption(option: echarts.EChartsCoreOption) {
  chart?.setOption(option, { notMerge: true })
}

/** state.logScale is the page-wide override; scale="log" is the metric's own
 *  declaration. Either one puts this axis on a log scale. */
const isLog = (spec: MetricSpec | null | undefined) =>
  state.logScale || spec?.scale === 'log'

async function update() {
  const my = ++token
  const laneList = chartLanes.value
  const desc = props.desc
  if (desc.kind === 'bar') {
    // bars draw from stats.last/summary (no fetch), but still deserve the same
    // bail-out: a poll that brought no new values must not replay the bar animation
    const renderKey = JSON.stringify(
      laneList.map((l) => [
        l.id,
        desc.series.map((s) =>
          l.runs.map(
            (r) => r.stats?.[s.key]?.last ?? r.summary?.[s.key] ?? null,
          ),
        ),
      ]),
    )
    if (renderKey === lastRenderKey) return
    lastRenderKey = renderKey
    renderBar()
    return
  }
  const bySeries = desc.colorBy === 'series'

  // A panel's axis is defined by its members, not by whichever member sorted first.
  // Members declaring axis="right" get their own scale — the deliberate escape hatch
  // for two related keys an order of magnitude apart, where the smaller one would
  // otherwise be a line flat against the axis. It is opt-in per member and never
  // inferred: two y-scales are easy to misread, so the author has to ask for it.
  const axisSpecs: (MetricSpec | null)[] = [0, 1].map((side) => {
    const keys = desc.series.filter((s) => sideOf(s) === side).map((s) => s.key)
    return keys.length ? resolveAxisSpec(keys).spec : null
  })
  const twoAxes = !!axisSpecs[1]
  const spec = axisSpecs[0] ?? axisSpecs[1] ?? ({} as MetricSpec)
  const unitOf = new Map(
    desc.series.map((s) => [s.key, axisSpecs[sideOf(s)]?.unit]),
  )

  // fetch every (lane, series) mean, plus band bounds where a series declares them
  const cells: Cell[] = []
  for (const lane of laneList)
    desc.series.forEach((s, sIdx) => cells.push({ lane, s, sIdx }))
  const fetched = await Promise.all(
    cells.map(async (c) => {
      const mean = await fetchLane(c.lane, c.s.key)
      // a shaded CI only makes sense on the linear scale its axis is drawing
      const drawBand = !!c.s.band && !isLog(axisSpecs[sideOf(c.s)])
      if (!mean || !c.s.band || !drawBand)
        return { c, mean, lo: null, hi: null }
      const [lo, hi] = await Promise.all([
        fetchLane(c.lane, c.s.band.lo),
        fetchLane(c.lane, c.s.band.hi),
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
    isolated.value,
    fetched.map(({ c, mean, lo, hi }) => [
      c.lane.id,
      c.s.key,
      mean?.xs.length ?? 0,
      mean?.xs[mean.xs.length - 1] ?? -1,
      lo?.xs.length ?? 0,
      hi?.xs.length ?? 0,
    ]),
  ])
  if (renderKey === lastRenderKey) return
  lastRenderKey = renderKey

  // baseline reference line (e.g. 0.5 chance level), drawn once per axis
  const markLineFor = (side: number) => {
    const s = axisSpecs[side]
    if (s?.baseline === undefined) return undefined
    return {
      silent: true,
      symbol: 'none' as const,
      animation: false,
      data: [
        {
          yAxis: s.baseline,
          label: {
            formatter: fmtMetric(s.baseline, s.unit),
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
  }
  const markLinePlaced = [false, false]

  // Zoomed-in y-rescale: while an x-window is active, fit the y-axis to the data
  // *inside* that window instead of the whole series, so a magnified span fills the
  // panel. Log axes keep their own auto range; a define_metric-pinned end is left
  // untouched below. We fold the same values we draw (smoothed line + band bounds),
  // skipping points outside the window — no interpolation, so a window with no
  // vertex inside falls back to the full-data fit. Tracked per axis.
  const win = state.xRange
  const yLo = [Infinity, Infinity]
  const yHi = [-Infinity, -Infinity]
  const foldWindow = (side: number, xa: number[], ya: number[]) => {
    for (let j = 0; j < xa.length; j++) {
      const x = xa[j]
      const y = ya[j]
      if (x < win![0] || x > win![1] || !Number.isFinite(y)) continue
      if (y < yLo[side]) yLo[side] = y
      if (y > yHi[side]) yHi[side] = y
    }
  }

  const series: object[] = []
  const legendNames: string[] = []
  for (const { c, mean, lo, hi } of fetched) {
    if (!mean) continue
    const color = bySeries ? seriesColor(c.sIdx) : c.lane.color
    const name = bySeries ? c.s.label : c.lane.label
    const side = sideOf(c.s)
    if (!legendNames.includes(name)) legendNames.push(name)
    // isolation: the picked line (and only its own helpers) survives
    if (isolated.value && name !== isolated.value) continue
    const log = isLog(axisSpecs[side])
    const autoRange = !!win && !log

    // shaded confidence band: one filled polygon tracing lo forward then hi back,
    // tinted to match its mean line (per-lane in comparison view, per-series in a
    // panel). A custom series (not stacked areas) so it renders correctly on
    // value/time axes, where line stacking misplaces the fill. lo/hi are co-logged
    // with the mean, so they're index-aligned; if they desync we skip the band.
    if (lo && hi && lo.ys.length > 0 && lo.ys.length === hi.ys.length) {
      const pts: [number, number, number][] = lo.xs.map((x, j) => [
        x,
        lo.ys[j],
        hi.ys[j],
      ])
      if (autoRange) {
        foldWindow(side, lo.xs, lo.ys)
        foldWindow(side, lo.xs, hi.ys)
      }
      series.push({
        name: `__band__${name}`,
        type: 'custom',
        silent: true,
        animation: false,
        z: 0,
        clip: true,
        yAxisIndex: side,
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

    let xs = mean.xs
    let ys = mean.ys
    if (log) {
      const keep = ys.map((v) => v > 0)
      xs = xs.filter((_, j) => keep[j])
      ys = ys.filter((_, j) => keep[j])
    }
    if (state.smoothing > 0 && ys.length > 1) {
      // ghost of the raw signal behind the smoothed line, wandb-style
      series.push({
        name: `__ghost__${name}`,
        type: 'line',
        yAxisIndex: side,
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
    if (autoRange) foldWindow(side, xs, ys)
    const scatter = c.s.kind === 'scatter'
    series.push({
      name,
      type: scatter ? 'scatter' : 'line',
      yAxisIndex: side,
      data: zip(xs, ys),
      ...(scatter
        ? { symbolSize: 6 }
        : { showSymbol: false, symbol: 'circle', symbolSize: 5 }),
      lineStyle: {
        color,
        width: 1.6,
        ...(twoAxes && side === 1 ? { type: 'dashed' } : {}),
      },
      itemStyle: { color },
      // hovering one line (or its legend entry) dims the rest — half the "too many
      // lines" problem is not being able to follow one of them
      emphasis: { focus: 'series' },
      blur: { lineStyle: { opacity: 0.12 }, itemStyle: { opacity: 0.12 } },
      z: 2,
      ...(markLineFor(side) && !markLinePlaced[side]
        ? ((markLinePlaced[side] = true), { markLine: markLineFor(side) })
        : {}),
    })
  }

  // padded window fit: a 5% margin keeps the extreme points off the frame edge;
  // a flat line (span 0) pads by its magnitude so it doesn't collapse to a sliver.
  const winFit = (side: number) => {
    if (!win || isLog(axisSpecs[side]) || yLo[side] > yHi[side]) return null
    const span = yHi[side] - yLo[side]
    const pad = span > 0 ? span * 0.05 : Math.abs(yHi[side]) * 0.05 || 1
    return { min: yLo[side] - pad, max: yHi[side] + pad }
  }

  // a define_metric-declared bound sets the axis *default*, but an active zoom
  // window overrides it — so you can still magnify inside a pinned range and read
  // the detail. Unzoomed, the declared bound (or full-data auto) stands.
  const yAxisFor = (side: number) => {
    const s = axisSpecs[side]
    const log = isLog(s)
    const fit = winFit(side)
    // a declared spec owns the range (so a small run can't look identical to a big
    // one); log mode and an active zoom window each keep their own auto range
    const declared = !log && !!s && Object.keys(s).length > 0
    return {
      ...axisName(s?.y_label, 'y'),
      type: log ? 'log' : 'value',
      scale: !declared,
      min: fit ? fit.min : declared && s?.min !== undefined ? s.min : undefined,
      max: fit ? fit.max : declared && s?.max !== undefined ? s.max : undefined,
      position: side === 1 ? ('right' as const) : ('left' as const),
      axisLine: {
        show: side === 1,
        lineStyle: { color: 'rgba(255,255,255,0.12)' },
      },
      axisLabel: {
        color: CHART_INK.dim,
        fontSize: 12,
        formatter: (v: number) => fmtMetric(v, s?.unit),
      },
      splitLine: {
        show: side === 0,
        lineStyle: { color: 'rgba(255,255,255,0.05)' },
      },
      splitNumber: 4,
    }
  }

  const multiLine = legendNames.length > 1

  applyOption({
    animationDuration: 200,
    animationDurationUpdate: 250,
    textStyle: { fontFamily: CHART_FONT },
    legend: multiLine ? legendOption(legendNames) : { show: false },
    grid: {
      left: spec?.y_label ? 6 + axisNameGap : 6,
      right: twoAxes ? 10 : 14,
      top: multiLine ? 28 : 12,
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
    yAxis: twoAxes ? [yAxisFor(0), yAxisFor(1)] : yAxisFor(0),
    tooltip: {
      trigger: 'axis',
      ...tooltipChrome(),
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
            seriesIndex: number
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
        // in a panel the legend label names the metric, so the tooltip is also
        // where each member's own description belongs
        const noteOf = (label: string) =>
          bySeries
            ? desc.series.find((s) => s.label === label)?.description
            : undefined
        const unitFor = (label: string) =>
          bySeries
            ? unitOf.get(desc.series.find((s) => s.label === label)?.key ?? '')
            : spec?.unit
        const body = rows
          .slice()
          .sort((a, b) => b.value[1] - a.value[1])
          .map((p) => {
            const note = noteOf(p.seriesName)
            return (
              `<div style="display:flex;align-items:center;gap:6px;margin-top:3px;min-width:150px">` +
              `<span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:${p.color}"></span>` +
              `<span style="color:${CHART_INK.mut};max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.seriesName)}</span>` +
              `<span style="margin-left:auto;padding-left:12px;font-family:${CHART_FONT}">${fmtMetric(p.value[1], unitFor(p.seriesName))}</span></div>` +
              (note
                ? `<div style="color:${CHART_INK.dim};font-size:11px;margin-left:13px;max-width:260px;white-space:normal">${esc(note)}</div>`
                : '')
            )
          })
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
  // legend click = isolate / restore. ECharts has already toggled its own selection
  // by the time this fires, so put it back and let `isolated` do the filtering.
  chart.on('legendselectchanged', (params: unknown) => {
    const name = (params as { name: string }).name
    const selected = (params as { selected: Record<string, boolean> }).selected
    isolated.value = isolated.value === name ? null : name
    chart?.setOption({
      legend: {
        selected: Object.fromEntries(
          Object.keys(selected).map((n) => [n, true]),
        ),
      },
    })
  })
  chart.getZr().on('dblclick', () => {
    state.xRange = null // double-click anywhere clears the shared zoom
    isolated.value = null
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
