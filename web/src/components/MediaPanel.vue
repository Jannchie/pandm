<script setup lang="ts">
import { computed, reactive, ref, watch, watchEffect } from 'vue'
import type { MediaItem, Run } from '../api'
import { runColor } from '../colors'
import { fmtStep } from '../fmt'
import { getMedia, selectedRuns, state } from '../store'

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

// one flat grid over every (run, key) pair, driven by a single step slider —
// per-key sections boxed each image into its own row and needed N sliders

const steps = computed(() => {
  const set = new Set<number>()
  for (const run of selectedRuns.value) {
    for (const item of mediaByRun[run.id] ?? []) set.add(item.step)
  }
  return [...set].sort((a, b) => a - b)
})

const idx = ref(Math.max(0, steps.value.length - 1))

// keep following the newest step while the user sits at the end of the slider
watch(
  () => steps.value.length,
  (len, oldLen) => {
    if (idx.value >= (oldLen ?? 0) - 1) idx.value = Math.max(0, len - 1)
    idx.value = Math.min(idx.value, Math.max(0, len - 1))
  },
)

const targetStep = computed(() => steps.value[idx.value] ?? 0)

// one section per selected run; headers only show once more than one is picked,
// so a single run stays a plain flat grid
const grouped = computed(() => selectedRuns.value.length > 1)

const groups = computed(() => {
  return selectedRuns.value
    .map((run) => {
      const byKey = new Map<string, MediaItem[]>()
      for (const item of mediaByRun[run.id] ?? []) {
        byKey.set(item.key, [...(byKey.get(item.key) ?? []), item])
      }
      const items: MediaItem[] = []
      for (const list of byKey.values()) {
        // nearest logged step for this (run, key)
        let nearest: number | null = null
        for (const item of list) {
          if (nearest === null || Math.abs(item.step - targetStep.value) < Math.abs(nearest - targetStep.value)) {
            nearest = item.step
          }
        }
        for (const item of list) {
          if (item.step === nearest) items.push(item)
        }
      }
      items.sort((a, b) => a.key.localeCompare(b.key))
      return { run, items }
    })
    .filter((g) => g.items.length)
})

function open(run: Run, item: MediaItem) {
  state.lightbox = {
    url: item.url,
    title: `${run.name} · ${item.key}`,
    sub: `step ${fmtStep(item.step)}${item.caption ? ` · ${item.caption}` : ''}`,
  }
}
</script>

<template>
  <div v-if="groups.length" class="p-4 flex flex-col gap-3">
    <div v-if="steps.length > 1" class="flex items-center gap-2.5">
      <input v-model.number="idx" type="range" :min="0" :max="steps.length - 1" step="1" class="flex-1 max-w-120" />
      <span class="text-[12.5px] text-fg-dim tabular-nums whitespace-nowrap">step {{ fmtStep(targetStep) }}</span>
    </div>

    <div class="flex flex-col gap-4">
      <section v-for="group in groups" :key="group.run.id" class="flex flex-col gap-2">
        <!-- per-run header, shown only when comparing more than one run -->
        <div v-if="grouped" class="flex items-center gap-1.5 px-0.5">
          <span class="w-2.5 h-2.5 rounded-full shrink-0" :style="{ background: runColor(group.run.id) }" />
          <span class="text-[14.5px] text-fg font-medium truncate">{{ group.run.name }}</span>
          <span class="text-[12.5px] text-fg-dim tabular-nums">{{ group.items.length }}</span>
        </div>

        <div
          class="grid gap-3 mobile-1col"
          :style="{
            gridTemplateColumns: state.columns
              ? `repeat(${state.columns}, minmax(0, 1fr))`
              : 'repeat(auto-fit, minmax(min(220px, 100%), 1fr))',
          }"
        >
          <figure
            v-for="item in group.items"
            :key="item.key + item.filename"
            class="card overflow-hidden cursor-zoom-in hover:border-fg-dim/40 transition-colors"
            @click="open(group.run, item)"
          >
            <img :src="item.url" class="w-full aspect-[4/3] object-contain bg-black/40" loading="lazy" />
            <figcaption class="px-2.5 py-2">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="w-2 h-2 rounded-full shrink-0" :style="{ background: runColor(group.run.id) }" />
                <span class="text-[13px] text-fg truncate font-mono">{{ item.key }}</span>
                <span class="ml-auto text-[12px] text-fg-dim tabular-nums shrink-0">{{ fmtStep(item.step) }}</span>
              </div>
              <!-- run name lives in the section header once grouped; drop it here to avoid repeating it -->
              <div v-if="grouped ? !!item.caption : true" class="text-[12.5px] text-fg-mut truncate mt-0.5">
                <template v-if="grouped">{{ item.caption }}</template>
                <template v-else>{{ group.run.name }}<template v-if="item.caption"> · {{ item.caption }}</template></template>
              </div>
            </figcaption>
          </figure>
        </div>
      </section>
    </div>
  </div>
  <div v-else class="h-full flex items-center justify-center text-[14.5px] text-fg-dim">
    No images yet — call run.log_image("samples", img)
  </div>
</template>
