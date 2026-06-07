<script setup lang="ts">
import { computed, reactive, ref, watch, watchEffect } from 'vue'
import type { MediaItem, Run } from '../api'
import { fmtStep } from '../fmt'
import { getMedia, selectedRuns } from '../store'
import MediaGroup from './MediaGroup.vue'

const mediaByRun = reactive<Record<string, MediaItem[]>>({})

watchEffect(() => {
  for (const run of selectedRuns.value) {
    getMedia(run)
      .then((items) => {
        mediaByRun[run.id] = items
      })
      .catch(() => {})
  }
})

const keys = computed(() => {
  const set = new Set<string>()
  for (const run of selectedRuns.value) {
    for (const item of mediaByRun[run.id] ?? []) set.add(item.key)
  }
  return [...set].sort()
})

function entriesFor(key: string): { run: Run; items: MediaItem[] }[] {
  return selectedRuns.value
    .map((run) => ({ run, items: (mediaByRun[run.id] ?? []).filter((i) => i.key === key) }))
    .filter((e) => e.items.length > 0)
}

// ----------------------------------------------- panel-wide step slider
// the union of every group's steps; dragging snaps each group to its nearest

const allSteps = computed(() => {
  const set = new Set<number>()
  for (const run of selectedRuns.value) {
    for (const item of mediaByRun[run.id] ?? []) set.add(item.step)
  }
  return [...set].sort((a, b) => a - b)
})

const gIdx = ref(Math.max(0, allSteps.value.length - 1))
const dragged = ref(false) // only drive the groups once the user touches the slider

watch(
  () => allSteps.value.length,
  (len, oldLen) => {
    if (gIdx.value >= (oldLen ?? 0) - 1) gIdx.value = Math.max(0, len - 1)
    gIdx.value = Math.min(gIdx.value, Math.max(0, len - 1))
  },
)

const globalStep = computed(() => (dragged.value ? (allSteps.value[gIdx.value] ?? null) : null))
</script>

<template>
  <div v-if="keys.length" class="p-4 flex flex-col gap-6">
    <div
      v-if="keys.length > 1 && allSteps.length > 1"
      class="flex items-center gap-2.5 -mb-2"
    >
      <span class="text-[11px] text-fg-dim whitespace-nowrap">all keys</span>
      <input
        v-model.number="gIdx"
        type="range"
        :min="0"
        :max="allSteps.length - 1"
        step="1"
        class="flex-1 max-w-120"
        @input="dragged = true"
      />
      <span class="text-[11px] text-fg-dim tabular-nums whitespace-nowrap">
        step {{ fmtStep(allSteps[gIdx] ?? 0) }}
      </span>
    </div>
    <MediaGroup v-for="key in keys" :key="key" :media-key="key" :entries="entriesFor(key)" :global-step="globalStep" />
  </div>
  <div v-else class="h-full flex items-center justify-center text-[13px] text-fg-dim">
    No images yet — call run.log_image("samples", img)
  </div>
</template>
