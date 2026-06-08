<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { runColor } from '../colors'
import { estimateEta } from '../eta'
import { fmtDuration, timeAgo } from '../fmt'
import { removeRun, selectAll, selectNone, selectRun, state, visibleRuns } from '../store'
import type { Run } from '../api'

// per-second clock so the "time left" counts down between polls (finishAt is fixed)
const now = ref(Date.now() / 1000)
let ticker: ReturnType<typeof setInterval>
onMounted(() => (ticker = setInterval(() => (now.value = Date.now() / 1000), 1000)))
onUnmounted(() => clearInterval(ticker))

// pair each run with its ETA; recomputed only when the run data changes (not every tick)
const rows = computed(() =>
  visibleRuns.value.map((run) => ({ run, eta: run.status === 'running' ? estimateEta(run) : null })),
)

const MIN_W = 200
const MAX_W = 560

// desktop-only width: on mobile the drawer keeps its fixed `w-70`/`max-w` sizing
const isDesktop = ref(window.matchMedia('(min-width: 768px)').matches)
const mq = window.matchMedia('(min-width: 768px)')
const onMq = (e: MediaQueryListEvent) => (isDesktop.value = e.matches)
mq.addEventListener('change', onMq)
onUnmounted(() => mq.removeEventListener('change', onMq))

const asideStyle = computed(() => (isDesktop.value ? { width: `${state.sidebarWidth}px` } : {}))

const dragging = ref(false)
function startResize(e: PointerEvent) {
  dragging.value = true
  const startX = e.clientX
  const startW = state.sidebarWidth
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'

  function move(ev: PointerEvent) {
    const next = startW + (ev.clientX - startX)
    state.sidebarWidth = Math.min(MAX_W, Math.max(MIN_W, next))
  }
  function up() {
    dragging.value = false
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

function confirmDelete(run: Run) {
  if (window.confirm(`Delete run "${run.name}" and its media? This cannot be undone.`)) removeRun(run.id)
}
</script>

<template>
  <!-- backdrop: only on mobile while the drawer is open -->
  <div
    v-if="state.sidebarOpen"
    class="fixed inset-0 z-40 bg-black/50 md:hidden"
    @click="state.sidebarOpen = false"
  />
  <aside
    class="fixed top-12 bottom-0 left-0 z-50 w-70 max-w-[82vw] bg-bg border-r border-border flex flex-col min-h-0 transition-transform duration-200 will-change-transform md:relative md:top-0 md:z-auto md:max-w-none md:shrink-0 md:translate-x-0! md:transition-none"
    :class="state.sidebarOpen ? 'translate-x-0' : '-translate-x-full'"
    :style="asideStyle"
  >
    <!-- search (h-9 matches the main tab bar so the border lines align) -->
    <div class="relative h-9 shrink-0 border-b border-border">
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        class="absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-dim"
      >
        <circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2" />
        <path d="M20 20l-3.5-3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
      <input v-model="state.search" placeholder="Filter runs…" class="w-full h-full bg-transparent border-none rounded-none pl-8 pr-3 text-[13px] text-fg placeholder:text-fg-dim outline-none" />
    </div>

    <!-- selection controls -->
    <div class="flex items-center px-2.5 py-1 text-[11px] text-fg-dim">
      <span>{{ state.selected.length }} of {{ visibleRuns.length }} selected</span>
      <div class="flex-1" />
      <button class="hover:text-fg-mut transition-colors" @click="selectAll">all</button>
      <span class="mx-1.5 opacity-40">·</span>
      <button class="hover:text-fg-mut transition-colors" @click="selectNone">none</button>
    </div>

    <!-- run list -->
    <div class="flex-1 min-h-0 overflow-y-auto">
      <div
        v-for="{ run, eta } in rows"
        :key="run.id"
        class="group relative flex items-center gap-2 px-2.5 py-1 cursor-pointer transition-colors"
        :class="state.selected.includes(run.id) ? 'bg-elev/70' : 'hover:bg-elev/40'"
        @click="selectRun(run.id, $event.ctrlKey || $event.metaKey)"
      >
        <!-- color dot doubles as the checkbox -->
        <span
          class="w-2.5 h-2.5 rounded-full shrink-0 transition-all"
          :style="
            state.selected.includes(run.id)
              ? { background: runColor(run.id) }
              : { boxShadow: 'inset 0 0 0 1.5px #3a3a44' }
          "
        />
        <div class="flex-1 min-w-0">
          <div class="text-[13px] truncate leading-tight" :class="state.selected.includes(run.id) ? 'text-fg' : 'text-fg-mut'">
            {{ run.name }}
          </div>
          <div class="text-[11px] text-fg-dim truncate leading-tight">
            <template v-if="run.status === 'running' && eta && eta.fraction != null">
              {{ Math.round(eta.fraction * 100) }}%<template v-if="eta.finishAt"> · ~{{ fmtDuration(eta.finishAt - now) }} left</template>
            </template>
            <template v-else>
              <template v-if="!state.project">{{ run.project }} · </template>{{ timeAgo(run.created_at) }}
            </template>
          </div>
        </div>
        <span
          v-if="run.status === 'running'"
          class="w-1.5 h-1.5 rounded-full bg-ok pulse shrink-0"
          title="running"
        />
        <svg
          v-else-if="run.status === 'crashed'"
          width="11"
          height="11"
          viewBox="0 0 24 24"
          fill="none"
          class="text-err/80 shrink-0"
          title="crashed"
        >
          <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
        </svg>
        <button
          class="opacity-0 group-hover:opacity-100 text-fg-dim hover:text-err transition-all shrink-0 cursor-pointer"
          title="Delete run"
          @click.stop="confirmDelete(run)"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
            <path
              d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>

        <!-- progress bar pinned to the row's bottom edge -->
        <div
          v-if="run.status === 'running' && eta && eta.fraction != null"
          class="absolute left-0 bottom-0 h-0.5 rounded-r-full transition-[width] duration-700 ease-out pointer-events-none"
          :style="{ width: `${Math.max(2, eta.fraction * 100)}%`, background: runColor(run.id) }"
        />
      </div>

      <div v-if="state.ready && visibleRuns.length === 0" class="px-2 py-8 text-center text-[12px] text-fg-dim">
        {{ state.search ? 'No runs match the filter' : 'No runs yet' }}
      </div>
    </div>

    <!-- desktop resize handle: thin hit-area on the right edge, accent line on hover/drag -->
    <div
      class="hidden md:block absolute top-0 right-0 bottom-0 w-1.5 translate-x-1/2 cursor-col-resize group/resize z-10"
      :class="{ 'is-dragging': dragging }"
      title="Drag to resize"
      @pointerdown.prevent="startResize"
    >
      <span
        class="absolute inset-y-0 left-1/2 -translate-x-1/2 w-px bg-border transition-colors group-hover/resize:bg-accent-hi"
        :class="dragging ? '!bg-accent-hi' : ''"
      />
    </div>
  </aside>
</template>
