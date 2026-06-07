<script setup lang="ts">
import { ref } from 'vue'
import { approveCli } from '../api'
import { dismissCli, state } from '../store'

const status = ref<'idle' | 'busy' | 'done' | 'failed'>('idle')

async function approve() {
  if (!state.cliCode) return
  status.value = 'busy'
  status.value = (await approveCli(state.cliCode)) ? 'done' : 'failed'
  if (status.value === 'done') setTimeout(dismissCli, 1500)
}
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div class="bg-panel border border-border p-6 w-90 max-w-full flex flex-col gap-4 shadow-2xl">
        <div class="text-[14px] font-medium">Approve CLI sign-in?</div>
        <p class="text-[12.5px] text-fg-mut leading-relaxed">
          A terminal running <span class="font-mono text-fg">pandm login</span> is asking for access with code
          <span class="font-mono text-accent-hi">{{ state.cliCode }}</span
          >. Only approve if this is you.
        </p>
        <div v-if="status === 'done'" class="text-[12.5px] text-ok">Approved — you can close this tab.</div>
        <div v-else-if="status === 'failed'" class="text-[12.5px] text-err">
          Code expired or invalid — run pandm login again.
        </div>
        <div class="flex gap-2 justify-end">
          <button class="btn" @click="dismissCli">Cancel</button>
          <button
            v-if="status === 'idle' || status === 'busy'"
            class="px-3 py-1 text-[12.5px] bg-accent text-white hover:bg-accent-hi transition-colors cursor-pointer disabled:opacity-50"
            :disabled="status === 'busy'"
            @click="approve"
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
