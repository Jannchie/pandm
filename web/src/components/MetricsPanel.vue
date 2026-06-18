<script setup lang="ts">
import { computed } from 'vue'
import { runColor } from '../colors'
import { fmtMetric } from '../fmt'
import { bestRunFor, metricSpec, selectedRuns, state } from '../store'
import MetricChart from './MetricChart.vue'

// the runs polling already carries per-key stats — their keys are exactly the
// metric keys, so chart discovery needs no per-run /metrics requests
const unionKeys = computed(() => {
  const set = new Set<string>()
  for (const run of selectedRuns.value) {
    for (const k of Object.keys(run.stats)) set.add(k)
  }
  return [...set].sort()
})

// group by the prefix before the first '/' (train/*, val/*, …) like wandb/tb.
// keys without a slash (lr, epoch) collect into an unlabelled section shown first.
const groups = computed(() => {
  const byPrefix = new Map<string, string[]>()
  for (const key of unionKeys.value) {
    const slash = key.indexOf('/')
    const name = slash > 0 ? key.slice(0, slash) : ''
    if (!byPrefix.has(name))
      byPrefix.set(name, [])
    byPrefix.get(name)!.push(key)
  }
  return [...byPrefix.entries()]
    .sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)))
    .map(([name, keys]) => ({ name, keys }))
})

const gridStyle = computed(() => ({
  gridTemplateColumns: state.columns
    ? `repeat(${state.columns}, minmax(0, 1fr))`
    : 'repeat(auto-fill, minmax(min(340px, 100%), 1fr))',
}))

// header badge for a declared metric: the latest value (percent-aware), and when a
// goal is declared across several runs, the leading run's value + colour + a ★.
// Driven by the stats already in hand (stats[key].last) — no extra fetch.
// one-line metric note (define_metric(description=...)), shown under the key
const descs = computed(() => {
  const out: Record<string, string> = {}
  for (const key of unionKeys.value) {
    const d = metricSpec(key)?.description
    if (d) out[key] = d
  }
  return out
})

const badges = computed(() => {
  const out: Record<string, { value: string; color: string; star: boolean } | null> = {}
  const multi = selectedRuns.value.length > 1
  for (const key of unionKeys.value) {
    const spec = metricSpec(key)
    if (!spec || (!spec.goal && multi)) {
      out[key] = null // no spec, or ambiguous which run is "best"
      continue
    }
    const best = bestRunFor(key, spec.goal ?? 'max')
    out[key] = best
      ? { value: fmtMetric(best.value, spec.unit), color: runColor(best.run.id), star: !!spec.goal && multi }
      : null
  }
  return out
})
</script>

<template>
  <div v-if="unionKeys.length" class="p-4 flex flex-col gap-5">
    <section v-for="grp in groups" :key="grp.name || '_'" class="flex flex-col gap-2">
      <h3
        v-if="grp.name"
        class="text-[11px] text-fg-dim font-semibold uppercase tracking-wide px-0.5"
      >
        {{ grp.name }}
      </h3>
      <div class="grid gap-3 mobile-1col" :style="gridStyle">
        <div v-for="key in grp.keys" :key="key" class="card group p-3 pb-1 min-w-0">
          <div class="flex items-center mb-1">
            <span class="text-[12.5px] text-fg font-medium truncate font-mono">{{ key }}</span>
            <div class="flex-1" />
            <span
              v-if="badges[key]"
              class="flex items-center gap-0.5 text-[11px] font-mono mr-1 shrink-0 tabular-nums"
              :style="{ color: badges[key]!.color }"
              :title="badges[key]!.star ? 'leading run' : 'latest'"
            >
              <span v-if="badges[key]!.star">★</span>{{ badges[key]!.value }}
            </span>
            <button
              class="opacity-0 group-hover:opacity-100 text-fg-dim hover:text-fg transition-all p-1 -m-1 cursor-pointer"
              title="expand"
              @click="state.expandedChart = key"
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
          <p v-if="descs[key]" class="text-[11px] text-fg-dim leading-snug mb-1 -mt-0.5 line-clamp-2">
            {{ descs[key] }}
          </p>
          <!-- aspect-ratio (not fixed height) so charts scale with the column width -->
          <div class="aspect-video">
            <MetricChart :metric-key="key" />
          </div>
        </div>
      </div>
    </section>
  </div>

  <div v-else class="h-full flex items-center justify-center text-[13px] text-fg-dim">
    No metrics yet — call run.log({"loss": …})
  </div>

  <!-- expanded chart overlay -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="state.expandedChart"
        class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 sm:p-10"
        @click.self="state.expandedChart = null"
      >
        <div class="card w-full max-w-5xl p-4 pb-2 shadow-2xl">
          <div class="flex items-center mb-2">
            <div class="min-w-0">
              <span class="text-[13px] text-fg font-medium font-mono">{{ state.expandedChart }}</span>
              <p v-if="state.expandedChart && descs[state.expandedChart]" class="text-[11px] text-fg-dim leading-snug truncate">
                {{ descs[state.expandedChart] }}
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
            <MetricChart :metric-key="state.expandedChart" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
