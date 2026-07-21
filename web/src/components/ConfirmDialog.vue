<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { resolveConfirm, state } from '../store'

// autofocus the confirm button so Enter fires it and the dialog is reachable
const confirmBtn = ref<HTMLButtonElement | null>(null)
watch(
  () => state.confirm,
  (c) => {
    if (c) nextTick(() => confirmBtn.value?.focus())
  },
)

function onKey(e: KeyboardEvent) {
  if (!state.confirm) return
  if (e.key === 'Escape') {
    e.preventDefault()
    resolveConfirm(false)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    resolveConfirm(true)
  }
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="state.confirm"
        class="fixed inset-0 z-70 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
        @click.self="resolveConfirm(false)"
      >
        <div
          class="w-full max-w-96 bg-panel border border-border rounded-xl shadow-2xl p-4"
          role="alertdialog"
          aria-modal="true"
        >
          <div class="text-[15px] text-fg font-medium">
            {{ state.confirm.title }}
          </div>
          <div
            v-if="state.confirm.body"
            class="mt-2 text-[13px] text-fg-mut leading-relaxed whitespace-pre-wrap break-words"
          >
            {{ state.confirm.body }}
          </div>
          <div class="mt-4 flex justify-end gap-2">
            <button
              class="px-3 py-1.5 rounded-md text-[13.5px] text-fg-mut hover:text-fg hover:bg-elev transition-colors cursor-pointer"
              @click="resolveConfirm(false)"
            >
              Cancel
            </button>
            <button
              ref="confirmBtn"
              class="px-3 py-1.5 rounded-md text-[13.5px] transition-colors cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-panel"
              :class="
                state.confirm.danger
                  ? 'bg-err/15 text-err hover:bg-err/25 focus-visible:ring-err/60'
                  : 'bg-accent/20 text-accent-hi hover:bg-accent/30 focus-visible:ring-accent/60'
              "
              @click="resolveConfirm(true)"
            >
              {{ state.confirm.confirmLabel }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
