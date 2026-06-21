<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { state } from '../store'

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    state.lightbox = null
    state.expandedChart = null
  }
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="state.lightbox"
        class="fixed inset-0 z-60 bg-black/80 backdrop-blur-sm flex flex-col items-center justify-center gap-3 p-4 sm:p-8 cursor-zoom-out"
        @click="state.lightbox = null"
      >
        <img
          :src="state.lightbox.url"
          class="max-w-full max-h-[82vh] rounded-lg shadow-2xl"
        />
        <div class="text-center">
          <div class="text-[14.5px] text-fg">
            {{ state.lightbox.title }}
          </div>
          <div class="text-[13px] text-fg-dim mt-0.5">
            {{ state.lightbox.sub }}
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
