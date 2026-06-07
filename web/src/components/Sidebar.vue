<script setup lang="ts">
import { runColor } from '../colors'
import { timeAgo } from '../fmt'
import { removeRun, selectAll, selectNone, state, toggleRun, visibleRuns } from '../store'
import type { Run } from '../api'

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
    class="fixed top-12 bottom-0 left-0 z-50 w-70 max-w-[82vw] bg-bg border-r border-border flex flex-col min-h-0 transition-transform duration-200 will-change-transform md:static md:z-auto md:max-w-none md:shrink-0 md:translate-x-0!"
    :class="state.sidebarOpen ? 'translate-x-0' : '-translate-x-full'"
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
        v-for="run in visibleRuns"
        :key="run.id"
        class="group flex items-center gap-2 px-2.5 py-1 cursor-pointer transition-colors"
        :class="state.selected.includes(run.id) ? 'bg-elev/70' : 'hover:bg-elev/40'"
        @click="toggleRun(run.id)"
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
            <template v-if="!state.project">{{ run.project }} · </template>{{ timeAgo(run.created_at) }}
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
      </div>

      <div v-if="state.ready && visibleRuns.length === 0" class="px-2 py-8 text-center text-[12px] text-fg-dim">
        {{ state.search ? 'No runs match the filter' : 'No runs yet' }}
      </div>
    </div>
  </aside>
</template>
