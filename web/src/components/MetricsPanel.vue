<script setup lang="ts">
import { computed, reactive, watchEffect } from 'vue'
import { getMetricKeys, selectedRuns, state } from '../store'
import MetricChart from './MetricChart.vue'

const keysByRun = reactive<Record<string, string[]>>({})

watchEffect(() => {
  for (const run of selectedRuns.value) {
    getMetricKeys(run)
      .then((ks) => {
        keysByRun[run.id] = ks.map((k) => k.key)
      })
      .catch(() => {})
  }
})

const unionKeys = computed(() => {
  const set = new Set<string>()
  for (const run of selectedRuns.value) {
    for (const k of keysByRun[run.id] ?? []) set.add(k)
  }
  return [...set].sort()
})
</script>

<template>
  <div
    v-if="unionKeys.length"
    class="grid gap-3 p-4"
    :style="{
      gridTemplateColumns: state.columns
        ? `repeat(${state.columns}, minmax(0, 1fr))`
        : 'repeat(auto-fill, minmax(340px, 1fr))',
    }"
  >
    <div v-for="key in unionKeys" :key="key" class="card group p-3 pb-1 min-w-0">
      <div class="flex items-center mb-1">
        <span class="text-[12.5px] text-fg-mut font-medium truncate font-mono">{{ key }}</span>
        <div class="flex-1" />
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
      <!-- aspect-ratio (not fixed height) so charts scale with the column width -->
      <div class="aspect-video">
        <MetricChart :metric-key="key" />
      </div>
    </div>
  </div>

  <div v-else class="h-full flex items-center justify-center text-[13px] text-fg-dim">
    No metrics yet — call run.log({"loss": …})
  </div>

  <!-- expanded chart overlay -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="state.expandedChart"
        class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-10"
        @click.self="state.expandedChart = null"
      >
        <div class="card w-full max-w-5xl p-4 pb-2 shadow-2xl">
          <div class="flex items-center mb-2">
            <span class="text-[13px] text-fg font-medium font-mono">{{ state.expandedChart }}</span>
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
