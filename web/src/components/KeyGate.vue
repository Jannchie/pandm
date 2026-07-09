<script setup lang="ts">
import { ref } from 'vue'
import { submitKey } from '../store'

const key = ref('')
const busy = ref(false)
const failed = ref(false)

async function submit() {
  if (!key.value.trim() || busy.value) return
  busy.value = true
  failed.value = !(await submitKey(key.value.trim()))
  busy.value = false
}
</script>

<template>
  <div class="h-full flex flex-col items-center justify-center gap-6 bg-bg">
    <div class="flex items-center gap-3 select-none">
      <svg width="36" height="36" viewBox="0 0 32 32" fill="none">
        <rect width="32" height="32" rx="8" fill="#17171c" />
        <path
          d="M7 22 L13 13 L18 17 L25 8"
          stroke="#8b95f6"
          stroke-width="3"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
      <span class="font-semibold tracking-tight text-[30px]">pandm</span>
    </div>

    <p class="text-[14.5px] text-fg-dim">
      This server requires an API key to view experiments
    </p>

    <form class="flex items-center gap-2" @submit.prevent="submit">
      <input
        v-model="key"
        type="password"
        placeholder="API key"
        autofocus
        class="w-64 px-3 py-2 bg-elev border border-border text-[14px] text-fg font-mono outline-none focus:border-accent-hi/60 transition-colors"
        :class="failed && 'border-red-400/60'"
      />
      <button
        type="submit"
        :disabled="busy"
        class="px-4 py-2 bg-elev border border-border text-[14px] text-fg hover:border-fg-dim/50 transition-colors select-none disabled:opacity-50"
      >
        Unlock
      </button>
    </form>
    <p v-if="failed" class="text-[13px] text-red-400 -mt-3">
      Invalid key — check with whoever runs this server
    </p>
  </div>
</template>
