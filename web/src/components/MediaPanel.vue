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

const cards = computed(() => {
  const out: { run: Run; item: MediaItem }[] = []
  for (const run of selectedRuns.value) {
    const byKey = new Map<string, MediaItem[]>()
    for (const item of mediaByRun[run.id] ?? []) {
      byKey.set(item.key, [...(byKey.get(item.key) ?? []), item])
    }
    for (const items of byKey.values()) {
      // nearest logged step for this (run, key)
      let nearest: number | null = null
      for (const item of items) {
        if (nearest === null || Math.abs(item.step - targetStep.value) < Math.abs(nearest - targetStep.value)) {
          nearest = item.step
        }
      }
      for (const item of items) {
        if (item.step === nearest) out.push({ run, item })
      }
    }
  }
  return out.sort((a, b) => a.item.key.localeCompare(b.item.key) || a.run.name.localeCompare(b.run.name))
})

function open(card: { run: Run; item: MediaItem }) {
  state.lightbox = {
    url: card.item.url,
    title: `${card.run.name} · ${card.item.key}`,
    sub: `step ${fmtStep(card.item.step)}${card.item.caption ? ` · ${card.item.caption}` : ''}`,
  }
}
</script>

<template>
  <div v-if="cards.length" class="p-4 flex flex-col gap-3">
    <div v-if="steps.length > 1" class="flex items-center gap-2.5">
      <input v-model.number="idx" type="range" :min="0" :max="steps.length - 1" step="1" class="flex-1 max-w-120" />
      <span class="text-[11px] text-fg-dim tabular-nums whitespace-nowrap">step {{ fmtStep(targetStep) }}</span>
    </div>

    <div
      class="grid gap-3"
      :style="{
        gridTemplateColumns: state.columns
          ? `repeat(${state.columns}, minmax(0, 1fr))`
          : 'repeat(auto-fit, minmax(220px, 1fr))',
      }"
    >
      <figure
        v-for="card in cards"
        :key="card.run.id + card.item.key + card.item.filename"
        class="card overflow-hidden cursor-zoom-in hover:border-fg-dim/40 transition-colors"
        @click="open(card)"
      >
        <img :src="card.item.url" class="w-full aspect-[4/3] object-contain bg-black/40" loading="lazy" />
        <figcaption class="px-2.5 py-2">
          <div class="flex items-center gap-1.5 min-w-0">
            <span class="w-2 h-2 rounded-full shrink-0" :style="{ background: runColor(card.run.id) }" />
            <span class="text-[11.5px] text-fg-mut truncate font-mono">{{ card.item.key }}</span>
            <span class="ml-auto text-[10.5px] text-fg-dim tabular-nums shrink-0">{{ fmtStep(card.item.step) }}</span>
          </div>
          <div class="text-[11px] text-fg-dim truncate mt-0.5">
            {{ card.run.name }}<template v-if="card.item.caption"> · {{ card.item.caption }}</template>
          </div>
        </figcaption>
      </figure>
    </div>
  </div>
  <div v-else class="h-full flex items-center justify-center text-[13px] text-fg-dim">
    No images yet — call run.log_image("samples", img)
  </div>
</template>
