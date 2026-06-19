<script setup lang="ts">
import type { ChartDesc, ChartSeriesDesc } from '../store'
import { computed, watchEffect } from 'vue'
import { runColor } from '../colors'
import { fmtMetric } from '../fmt'
import { bestRunFor, ensureHistogramKeys, histogramKeysByRun, metricSpec, selectedRuns, state } from '../store'
import HistogramChart from './HistogramChart.vue'
import MetricChart from './MetricChart.vue'

// the runs polling already carries per-key stats — their keys are exactly the
// metric keys, so chart discovery needs no per-run /metrics requests
const unionKeys = computed(() => {
  const set = new Set<string>()
  for (const run of selectedRuns.value) {
    for (const k of Object.keys(run.stats)) set.add(k)
    // bar metrics may carry no time series (their height is the latest / summary
    // scalar), so surface keys declared kind="bar" even when stats has nothing
    for (const [k, spec] of Object.entries(run.metric_meta ?? {})) {
      if (spec?.kind === 'bar') set.add(k)
    }
  }
  return [...set].sort()
})

// Build the chart descriptors from the union of keys + their declared specs.
// Three steps: fold band triples, drop the consumed _lo/_hi keys, group panels.
const charts = computed<ChartDesc[]>(() => {
  const keys = unionKeys.value
  const present = new Set(keys)
  const multiRun = selectedRuns.value.length > 1
  // resolve each key's spec once — metricSpec() scans the selected runs, and the
  // steps below would otherwise look the same key up several times per rebuild
  const specOf = new Map(keys.map((k) => [k, metricSpec(k)]))

  // 1. band detection — map each mean key to its lo/hi bounds, and remember which
  // keys are bounds so they don't also render as their own charts. Always explicit:
  // band=true pairs the _lo/_hi siblings, band={lo,hi} names them. (No silent
  // suffix magic — bare _lo/_hi keys stay ordinary charts unless a band is declared.)
  const consumed = new Set<string>()
  const bandOf = new Map<string, { lo: string, hi: string }>()
  for (const k of keys) {
    const band = specOf.get(k)?.band
    let lo: string | undefined
    let hi: string | undefined
    if (band && typeof band === 'object') {
      lo = band.lo
      hi = band.hi
    }
    else if (band === true) {
      lo = `${k}_lo`
      hi = `${k}_hi`
    }
    if (lo && hi && present.has(lo) && present.has(hi)) {
      bandOf.set(k, { lo, hi })
      consumed.add(lo)
      consumed.add(hi)
    }
  }

  const seriesFor = (k: string): ChartSeriesDesc => ({
    key: k,
    label: specOf.get(k)?.series ?? k,
    band: bandOf.get(k),
    kind: specOf.get(k)?.kind ?? 'line',
  })

  // 2/3. group primary keys by panel. Option A: panels only collapse to one chart
  // in single-run view; with several runs selected each key falls back to its own
  // chart (coloured by run) so run-comparison still works. Bar is the exception —
  // a single-category bar is meaningless, so bar panels always group (grouped bars).
  const panels = new Map<string, string[]>()
  const out: ChartDesc[] = []
  for (const k of keys) {
    if (consumed.has(k))
      continue
    const spec = specOf.get(k)
    const isBar = spec?.kind === 'bar'
    if (spec?.panel && (!multiRun || isBar)) {
      if (!panels.has(spec.panel))
        panels.set(spec.panel, [])
      panels.get(spec.panel)!.push(k)
    }
    else {
      out.push({ id: `key:${k}`, title: k, kind: spec?.kind ?? 'line', series: [seriesFor(k)], colorBy: 'run' })
    }
  }
  for (const [panel, members] of panels) {
    const kind = specOf.get(members[0])?.kind ?? 'line'
    out.push({
      id: `panel:${panel}`,
      title: panel,
      panel,
      kind,
      series: members.map(seriesFor),
      colorBy: multiRun ? 'run' : 'series',
    })
  }

  // 4. histograms (run.log_histogram) join the same chart model so they get the
  // identical title / description / expand treatment — but a heatmap is single-run,
  // so each (run, key) is its own descriptor pinned to that run. They sort into the
  // same prefix sections as their sibling metrics (dist/* lands under "dist").
  for (const run of selectedRuns.value) {
    for (const key of histogramKeysByRun[run.id] ?? []) {
      out.push({
        id: `hist:${run.id}:${key}`,
        title: key,
        kind: 'histogram',
        series: [{ key, label: key, kind: 'line' }],
        colorBy: 'run',
        run,
      })
    }
  }
  return out
})

// keep the wandb/tb-style sections (train/*, val/*, …): a chart sits under the
// prefix of its primary key, so a "reward" panel lands in the reward section.
const sections = computed(() => {
  const byPrefix = new Map<string, ChartDesc[]>()
  for (const c of charts.value) {
    const probe = c.series[0]?.key ?? c.title
    const slash = probe.indexOf('/')
    const name = slash > 0 ? probe.slice(0, slash) : ''
    if (!byPrefix.has(name))
      byPrefix.set(name, [])
    byPrefix.get(name)!.push(c)
  }
  return [...byPrefix.entries()]
    .sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)))
    .map(([name, items]) => ({ name, items }))
})

const gridStyle = computed(() => ({
  gridTemplateColumns: state.columns
    ? `repeat(${state.columns}, minmax(0, 1fr))`
    : 'repeat(auto-fill, minmax(min(340px, 100%), 1fr))',
}))

// A single-key chart keeps its key's description + leading-run badge; a panel chart
// shows just its name (its lines carry their own legend). Keyed by chart id so the
// template reads each once instead of recomputing per access.
const descs = computed(() => {
  const out: Record<string, string | undefined> = {}
  for (const c of charts.value) out[c.id] = c.panel ? undefined : metricSpec(c.series[0].key)?.description
  return out
})

const badges = computed(() => {
  const out: Record<string, { value: string, color: string, star: boolean } | null> = {}
  const multi = selectedRuns.value.length > 1
  for (const c of charts.value) {
    const spec = c.panel || c.kind === 'histogram' ? null : metricSpec(c.series[0].key)
    if (!spec || (!spec.goal && multi)) {
      out[c.id] = null // panel, no spec, or ambiguous which run is "best"
      continue
    }
    const best = bestRunFor(c.series[0].key, spec.goal ?? 'max')
    out[c.id] = best
      ? { value: fmtMetric(best.value, spec.unit), color: runColor(best.run.id), star: !!spec.goal && multi }
      : null
  }
  return out
})

const expanded = computed(() => charts.value.find((c) => c.id === state.expandedChart) ?? null)

// histogram keys don't ride in run.stats, so discover them per run with a dedicated
// request; the result feeds histogramKeysByRun, which the charts computed folds into
// the unified chart list above.
watchEffect(() => {
  for (const run of selectedRuns.value) ensureHistogramKeys(run)
})
</script>

<template>
  <div v-if="charts.length" class="p-4 flex flex-col gap-5">
    <section v-for="grp in sections" :key="grp.name || '_'" class="flex flex-col gap-2">
      <h3
        v-if="grp.name"
        class="text-[12.5px] text-fg-dim font-semibold uppercase tracking-wide px-0.5"
      >
        {{ grp.name }}
      </h3>
      <div class="grid gap-3 mobile-1col" :style="gridStyle">
        <div v-for="c in grp.items" :key="c.id" class="card group p-3 pb-1 min-w-0">
          <div class="flex items-center mb-1">
            <span class="text-[14px] text-fg font-medium truncate font-mono">{{ c.title }}</span>
            <span
              v-if="c.kind === 'histogram' && selectedRuns.length > 1"
              class="text-[12px] font-mono truncate shrink-0 ml-2"
              :style="{ color: runColor(c.run!.id) }"
            >{{ c.run!.name }}</span>
            <div class="flex-1" />
            <span
              v-if="badges[c.id]"
              class="flex items-center gap-0.5 text-[12.5px] font-mono mr-1 shrink-0 tabular-nums"
              :style="{ color: badges[c.id]!.color }"
              :title="badges[c.id]!.star ? 'leading run' : 'latest'"
            >
              <span v-if="badges[c.id]!.star">★</span>{{ badges[c.id]!.value }}
            </span>
            <button
              class="opacity-0 group-hover:opacity-100 text-fg-dim hover:text-fg transition-all p-1 -m-1 cursor-pointer"
              title="expand"
              @click="state.expandedChart = c.id"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                <path
                  d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
            </button>
          </div>
          <p v-if="descs[c.id]" class="text-[12.5px] text-fg-dim leading-snug mb-1 -mt-0.5 line-clamp-2">
            {{ descs[c.id] }}
          </p>
          <!-- aspect-ratio (not fixed height) so charts scale with the column width -->
          <div class="aspect-video">
            <HistogramChart v-if="c.kind === 'histogram'" :run="c.run!" :metric-key="c.series[0].key" />
            <MetricChart v-else :desc="c" />
          </div>
        </div>
      </div>
    </section>
  </div>

  <div v-else class="h-full flex items-center justify-center text-[14.5px] text-fg-dim">
    No metrics yet — call run.log({"loss": …})
  </div>

  <!-- expanded chart overlay -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="expanded"
        class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 sm:p-10"
        @click.self="state.expandedChart = null"
      >
        <div class="card w-full max-w-5xl p-4 pb-2 shadow-2xl">
          <div class="flex items-center mb-2">
            <div class="min-w-0">
              <span class="text-[14.5px] text-fg font-medium font-mono">{{ expanded.title }}</span>
              <span
                v-if="expanded.kind === 'histogram' && selectedRuns.length > 1"
                class="text-[12.5px] font-mono ml-2"
                :style="{ color: runColor(expanded.run!.id) }"
              >{{ expanded.run!.name }}</span>
              <p v-if="descs[expanded.id]" class="text-[12.5px] text-fg-dim leading-snug truncate">
                {{ descs[expanded.id] }}
              </p>
            </div>
            <div class="flex-1" />
            <button class="text-fg-dim hover:text-fg transition-colors cursor-pointer" @click="state.expandedChart = null">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              </svg>
            </button>
          </div>
          <div class="h-[64vh]">
            <HistogramChart v-if="expanded.kind === 'histogram'" :run="expanded.run!" :metric-key="expanded.series[0].key" />
            <MetricChart v-else :desc="expanded" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
