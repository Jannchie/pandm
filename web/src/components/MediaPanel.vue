<script setup lang="ts">
import { computed, reactive, watchEffect } from 'vue'
import type { MediaItem, Run } from '../api'
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
</script>

<template>
  <div v-if="keys.length" class="p-4 flex flex-col gap-6">
    <MediaGroup v-for="key in keys" :key="key" :media-key="key" :entries="entriesFor(key)" />
  </div>
  <div v-else class="h-full flex items-center justify-center text-[13px] text-fg-dim">
    No images yet — call run.log_image("samples", img)
  </div>
</template>
