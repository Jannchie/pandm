<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { MediaItem, Run } from '../api'
import { runColor } from '../colors'
import { fmtStep } from '../fmt'
import { state } from '../store'

const props = defineProps<{ mediaKey: string; entries: { run: Run; items: MediaItem[] }[] }>()

const steps = computed(() => {
  const set = new Set<number>()
  for (const e of props.entries) for (const item of e.items) set.add(item.step)
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
  for (const e of props.entries) {
    // nearest logged step for this run
    let nearest: number | null = null
    for (const item of e.items) {
      if (nearest === null || Math.abs(item.step - targetStep.value) < Math.abs(nearest - targetStep.value)) {
        nearest = item.step
      }
    }
    for (const item of e.items) {
      if (item.step === nearest) out.push({ run: e.run, item })
    }
  }
  return out
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
  <section>
    <div class="flex items-center gap-4 mb-2.5">
      <h3 class="text-[12.5px] text-fg-mut font-medium font-mono">{{ mediaKey }}</h3>
      <div v-if="steps.length > 1" class="flex items-center gap-2.5 flex-1 max-w-90">
        <input v-model.number="idx" type="range" :min="0" :max="steps.length - 1" step="1" class="flex-1" />
        <span class="text-[11px] text-fg-dim tabular-nums whitespace-nowrap">step {{ fmtStep(targetStep) }}</span>
      </div>
    </div>

    <div
      class="grid gap-3"
      :style="{
        gridTemplateColumns: state.columns
          ? `repeat(${state.columns}, minmax(0, 1fr))`
          : 'repeat(auto-fill, minmax(200px, 1fr))',
      }"
    >
      <figure
        v-for="card in cards"
        :key="card.run.id + card.item.filename"
        class="card overflow-hidden cursor-zoom-in hover:border-fg-dim/40 transition-colors"
        @click="open(card)"
      >
        <img :src="card.item.url" class="w-full h-40 object-contain bg-black/40" loading="lazy" />
        <figcaption class="px-2.5 py-2">
          <div class="flex items-center gap-1.5 min-w-0">
            <span class="w-2 h-2 rounded-full shrink-0" :style="{ background: runColor(card.run.id) }" />
            <span class="text-[11.5px] text-fg-mut truncate">{{ card.run.name }}</span>
            <span class="ml-auto text-[10.5px] text-fg-dim tabular-nums shrink-0">{{ fmtStep(card.item.step) }}</span>
          </div>
          <div v-if="card.item.caption" class="text-[11px] text-fg-dim truncate mt-0.5">
            {{ card.item.caption }}
          </div>
        </figcaption>
      </figure>
    </div>
  </section>
</template>
