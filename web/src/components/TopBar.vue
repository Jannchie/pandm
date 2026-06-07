<script setup lang="ts">
import { computed, ref } from 'vue'
import { runColor } from '../colors'
import { anyRunning, selectAll, selectNone, setProject, state, toggleRun } from '../store'

const runsOpen = ref(false)

const runsLabel = computed(() => {
  if (state.selected.length === 1) {
    const run = state.runs.find((r) => r.id === state.selected[0])
    if (run) return run.name
  }
  return `${state.selected.length} of ${state.runs.length} runs`
})
</script>

<template>
  <header class="h-12 shrink-0 border-b border-border flex items-center px-4 gap-1">
    <!-- logo -->
    <div class="flex items-center gap-2 select-none mr-2">
      <svg width="18" height="18" viewBox="0 0 32 32" fill="none">
        <rect width="32" height="32" rx="8" fill="#17171c" />
        <path
          d="M7 22 L13 13 L18 17 L25 8"
          stroke="#8b95f6"
          stroke-width="3"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
      <span class="font-semibold tracking-tight text-[15px]">pandm</span>
    </div>

    <!-- breadcrumb: project -->
    <span class="text-fg-dim/60 text-[13px] select-none">/</span>
    <div class="relative">
      <select
        :value="state.project"
        class="appearance-none bg-transparent pl-2 pr-6 py-1 text-[13px] text-fg hover:bg-elev transition-colors cursor-pointer outline-none"
        @change="setProject(($event.target as HTMLSelectElement).value)"
      >
        <option value="">All projects</option>
        <option v-for="p in state.projects" :key="p.project" :value="p.project">
          {{ p.project }} ({{ p.runs }})
        </option>
      </select>
      <svg
        width="11"
        height="11"
        viewBox="0 0 24 24"
        fill="none"
        class="absolute right-1.5 top-1/2 -translate-y-1/2 pointer-events-none text-fg-dim"
      >
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
    </div>

    <!-- breadcrumb: runs -->
    <span class="text-fg-dim/60 text-[13px] select-none">/</span>
    <div class="relative">
      <button
        class="flex items-center gap-1.5 pl-2 pr-1.5 py-1 text-[13px] hover:bg-elev transition-colors cursor-pointer"
        :class="state.selected.length ? 'text-fg' : 'text-fg-dim'"
        @click="runsOpen = !runsOpen"
      >
        <span class="max-w-50 truncate">{{ runsLabel }}</span>
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" class="text-fg-dim shrink-0">
          <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
      </button>

      <div v-if="runsOpen" class="fixed inset-0 z-40" @click="runsOpen = false" />
      <div
        v-if="runsOpen"
        class="absolute left-0 top-full mt-1 w-72 max-h-90 overflow-y-auto bg-panel border border-border shadow-2xl z-50 py-1"
      >
        <div class="flex items-center px-3 py-1 text-[11px] text-fg-dim">
          <span>{{ state.selected.length }} of {{ state.runs.length }} selected</span>
          <div class="flex-1" />
          <button class="hover:text-fg-mut transition-colors cursor-pointer" @click="selectAll">all</button>
          <span class="mx-1.5 opacity-40">·</span>
          <button class="hover:text-fg-mut transition-colors cursor-pointer" @click="selectNone">none</button>
        </div>
        <div
          v-for="run in state.runs"
          :key="run.id"
          class="flex items-center gap-2 px-3 py-1 cursor-pointer transition-colors"
          :class="state.selected.includes(run.id) ? 'bg-elev/70' : 'hover:bg-elev/40'"
          @click="toggleRun(run.id)"
        >
          <span
            class="w-2 h-2 rounded-full shrink-0"
            :style="
              state.selected.includes(run.id)
                ? { background: runColor(run.id) }
                : { boxShadow: 'inset 0 0 0 1.5px #3a3a44' }
            "
          />
          <span
            class="text-[12.5px] truncate"
            :class="state.selected.includes(run.id) ? 'text-fg' : 'text-fg-mut'"
          >{{ run.name }}</span>
          <span v-if="run.status === 'running'" class="ml-auto w-1.5 h-1.5 rounded-full bg-ok pulse shrink-0" />
        </div>
        <div v-if="state.runs.length === 0" class="px-3 py-4 text-center text-[12px] text-fg-dim">No runs yet</div>
      </div>
    </div>

    <div class="flex-1" />

    <!-- status -->
    <div class="flex items-center gap-2 text-[12px] text-fg-dim">
      <template v-if="state.offline">
        <span class="w-1.5 h-1.5 rounded-full bg-err" />
        <span class="text-err">offline</span>
      </template>
      <template v-else-if="anyRunning">
        <span class="w-1.5 h-1.5 rounded-full bg-ok pulse" />
        <span>live</span>
      </template>
      <template v-else>
        <span>{{ state.runs.length }} runs</span>
      </template>
    </div>
  </header>
</template>
