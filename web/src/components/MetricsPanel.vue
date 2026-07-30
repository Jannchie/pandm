<script setup lang="ts">
import type { ChartDesc } from '../store'
import { computed, watchEffect } from 'vue'
import {
  charts,
  ensureHistogramKeys,
  gridCharts,
  selectedRuns,
  state,
  visibleCharts,
} from '../store'
import AlarmBar from './AlarmBar.vue'
import ChartCard from './ChartCard.vue'

// The chart list itself lives in the store — the tab bar counts it, so it can't be
// built by this component. What's left here is purely how the page lays them out.

// importance="primary": the 3–5 charts the experiment is actually judged on, pinned
// to a row at the top at double size. Nothing else on the page decides anything.
const primaryCharts = computed(() =>
  gridCharts.value.filter((c) => c.importance === 'primary'),
)
// importance="debug": counters that matter only when something is wrong. Folded
// away by default so they stop competing with the metrics that don't.
const debugCharts = computed(() =>
  gridCharts.value.filter((c) => c.importance === 'debug'),
)

// the wandb/tb-style sections (train/*, val/*, …): a chart sits under the prefix of
// its primary key, so a "reward" panel lands in the reward section.
const sections = computed(() => {
  const byPrefix = new Map<string, ChartDesc[]>()
  for (const c of gridCharts.value) {
    if (c.importance !== 'normal') continue
    const probe = c.series[0]?.key ?? c.title
    const slash = probe.indexOf('/')
    const name = slash > 0 ? probe.slice(0, slash) : ''
    if (!byPrefix.has(name)) byPrefix.set(name, [])
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

// the pinned row is deliberately coarser than the grid below it — a primary metric
// you have to squint at defeats the point of declaring it primary
const primaryStyle = {
  gridTemplateColumns: 'repeat(auto-fill, minmax(min(430px, 100%), 1fr))',
}

const expanded = computed(
  () => charts.value.find((c) => c.id === state.expandedChart) ?? null,
)

// a chart id can vanish (a filter, a deselected run) while its overlay is open
watchEffect(() => {
  if (state.expandedChart && !expanded.value) state.expandedChart = null
})

// histogram keys don't ride in run.stats, so discover them per run with a dedicated
// request; the result feeds histogramKeysByRun, which the store's charts computed
// folds into the unified chart list.
watchEffect(() => {
  for (const run of selectedRuns.value) ensureHistogramKeys(run)
})

// the filter excluded everything — say so, rather than showing a blank page that
// looks like "this run logged nothing"
const noMatch = computed(() => visibleCharts.value.length === 0)
</script>

<template>
  <div v-if="charts.length" class="p-4 flex flex-col gap-5">
    <AlarmBar />

    <!-- ★ primary: the metrics the experiment is judged on -->
    <section v-if="primaryCharts.length" class="flex flex-col gap-2">
      <h3
        class="text-[12.5px] text-accent-hi font-semibold uppercase tracking-wide px-0.5"
      >
        ★ primary
      </h3>
      <div class="grid gap-3 mobile-1col" :style="primaryStyle">
        <ChartCard v-for="c in primaryCharts" :key="c.id" :desc="c" />
      </div>
    </section>

    <section
      v-for="grp in sections"
      :key="grp.name || '_'"
      class="flex flex-col gap-2"
    >
      <h3
        v-if="grp.name"
        class="text-[12.5px] text-fg-dim font-semibold uppercase tracking-wide px-0.5"
      >
        {{ grp.name }}
      </h3>
      <div class="grid gap-3 mobile-1col" :style="gridStyle">
        <ChartCard v-for="c in grp.items" :key="c.id" :desc="c" />
      </div>
    </section>

    <!-- debug: folded away, but one click from open -->
    <section v-if="debugCharts.length" class="flex flex-col gap-2">
      <button
        class="flex items-center gap-1.5 self-start text-[12.5px] text-fg-dim hover:text-fg-mut transition-colors cursor-pointer px-0.5"
        @click="state.showDebug = !state.showDebug"
      >
        <span class="text-accent-hi">{{ state.showDebug ? '▾' : '▸' }}</span>
        <span class="font-semibold uppercase tracking-wide">debug</span>
        <span class="tabular-nums">({{ debugCharts.length }})</span>
        <span
          v-if="!state.showDebug"
          class="font-mono truncate max-w-100 opacity-70"
        >
          {{ debugCharts.map((c) => c.title).join(' · ') }}
        </span>
      </button>
      <div
        v-if="state.showDebug"
        class="grid gap-3 mobile-1col"
        :style="gridStyle"
      >
        <ChartCard v-for="c in debugCharts" :key="c.id" :desc="c" />
      </div>
    </section>

    <div v-if="noMatch" class="text-center text-[14px] text-fg-dim py-10">
      No metric matches “{{ state.metricSearch }}”
    </div>
  </div>

  <div
    v-else
    class="h-full flex items-center justify-center text-[14.5px] text-fg-dim"
  >
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
        <div class="w-full max-w-5xl relative">
          <ChartCard :desc="expanded" overlay />
          <button
            class="absolute top-4 right-4 text-fg-dim hover:text-fg transition-colors cursor-pointer p-1"
            @click="state.expandedChart = null"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
              />
            </svg>
          </button>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
