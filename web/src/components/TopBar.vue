<script setup lang="ts">
import { computed, ref } from 'vue'
import { rotateApiKey } from '../api'
import { runColor } from '../colors'
import { anyRunning, removeProject, selectAll, selectNone, selectRun, setProject, signOut, state, toggleLive } from '../store'

const projOpen = ref(false)
const runsOpen = ref(false)
const userOpen = ref(false)
const copied = ref(false)

function pickProject(project: string) {
  projOpen.value = false
  setProject(project)
}

function confirmDeleteProject(project: string, runs: number) {
  if (window.confirm(`Delete project "${project}" and all ${runs} run(s), including media? This cannot be undone.`))
    removeProject(project)
}

const runsLabel = computed(() => {
  if (state.selected.length === 1) {
    const run = state.runs.find((r) => r.id === state.selected[0])
    if (run) return run.name
  }
  return `${state.selected.length} of ${state.runs.length} runs`
})

async function copyKey() {
  if (!state.auth.user) return
  await navigator.clipboard.writeText(state.auth.user.api_key)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

async function rotateKey() {
  if (!state.auth.user) return
  state.auth.user.api_key = await rotateApiKey()
}
</script>

<template>
  <header class="h-12 shrink-0 border-b border-border flex items-center px-2 sm:px-4 gap-1">
    <!-- mobile drawer toggle -->
    <button
      class="md:hidden -ml-0.5 mr-0.5 p-1.5 text-fg-mut hover:text-fg transition-colors cursor-pointer"
      title="Toggle runs"
      @click="state.sidebarOpen = !state.sidebarOpen"
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
        <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
    </button>

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
      <span class="font-semibold tracking-tight text-[17px]">pandm</span>
    </div>

    <!-- breadcrumb: project -->
    <span class="text-fg-dim/60 text-[14.5px] select-none">/</span>
    <div class="relative">
      <button
        class="flex items-center gap-1.5 pl-2 pr-1.5 py-1 text-[14.5px] hover:bg-elev transition-colors cursor-pointer"
        :class="state.project ? 'text-fg' : 'text-fg-dim'"
        @click="projOpen = !projOpen"
      >
        <span class="max-w-40 truncate">{{ state.project || 'no project' }}</span>
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" class="text-fg-dim shrink-0">
          <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
      </button>

      <div v-if="projOpen" class="fixed inset-0 z-40" @click="projOpen = false" />
      <div
        v-if="projOpen"
        class="absolute left-0 top-full mt-1 w-56 max-h-90 overflow-y-auto bg-panel border border-border shadow-2xl z-50 py-1"
      >
        <div
          v-for="p in state.projects"
          :key="p.project"
          class="group flex items-center px-3 py-1.5 text-[14px] transition-colors cursor-pointer"
          :class="p.project === state.project ? 'text-fg bg-elev/70' : 'text-fg-mut hover:bg-elev/40 hover:text-fg'"
          @click="pickProject(p.project)"
        >
          <span class="truncate">{{ p.project }}</span>
          <span class="ml-auto pl-3 text-[12.5px] text-fg-dim shrink-0 group-hover:hidden">{{ p.runs }}</span>
          <button
            class="ml-auto pl-3 hidden group-hover:block text-fg-dim hover:text-err transition-colors shrink-0 cursor-pointer"
            title="Delete project"
            @click.stop="confirmDeleteProject(p.project, p.runs)"
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
        <div v-if="state.projects.length === 0" class="px-3 py-4 text-center text-[13.5px] text-fg-dim">
          No projects yet
        </div>
      </div>
    </div>

    <!-- breadcrumb: runs -->
    <span class="hidden sm:inline text-fg-dim/60 text-[14.5px] select-none">/</span>
    <div class="relative hidden sm:block">
      <button
        class="flex items-center gap-1.5 pl-2 pr-1.5 py-1 text-[14.5px] hover:bg-elev transition-colors cursor-pointer"
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
        <div class="flex items-center px-3 py-1 text-[12.5px] text-fg-dim">
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
          @click="selectRun(run.id, $event.ctrlKey || $event.metaKey)"
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
            class="text-[14px] truncate"
            :class="state.selected.includes(run.id) ? 'text-fg' : 'text-fg-mut'"
          >{{ run.name }}</span>
          <span v-if="run.status === 'running'" class="ml-auto w-1.5 h-1.5 rounded-full bg-ok pulse shrink-0" />
        </div>
        <div v-if="state.runs.length === 0" class="px-3 py-4 text-center text-[13.5px] text-fg-dim">No runs yet</div>
      </div>
    </div>

    <div class="flex-1" />

    <!-- status + auto-refresh switch -->
    <div class="flex items-center gap-2 text-[13.5px] text-fg-dim">
      <template v-if="state.offline">
        <span class="w-1.5 h-1.5 rounded-full bg-err" />
        <span class="text-err">offline</span>
      </template>
      <template v-else-if="!state.live">
        <span class="hidden sm:inline">paused</span>
      </template>
      <template v-else-if="anyRunning">
        <span class="w-1.5 h-1.5 rounded-full bg-ok pulse" />
        <span class="hidden sm:inline">live</span>
      </template>
      <template v-else>
        <span class="hidden sm:inline">{{ state.runs.length }} runs</span>
      </template>
      <button
        class="relative w-7 h-4 rounded-full transition-colors cursor-pointer ml-1"
        :class="state.live ? 'bg-ok/50' : 'bg-elev'"
        :title="state.live ? 'auto-refresh on' : 'auto-refresh off'"
        @click="toggleLive"
      >
        <span
          class="absolute top-0.5 w-3 h-3 rounded-full bg-fg transition-all"
          :class="state.live ? 'left-3.5' : 'left-0.5'"
        />
      </button>
    </div>

    <!-- account -->
    <div v-if="state.auth.mode === 'user' && state.auth.user" class="relative ml-3">
      <button class="flex items-center cursor-pointer" @click="userOpen = !userOpen">
        <img
          v-if="state.auth.user.avatar_url"
          :src="state.auth.user.avatar_url"
          class="w-6 h-6 rounded-full border border-border"
        />
        <span v-else class="w-6 h-6 rounded-full bg-elev text-[12.5px] flex items-center justify-center text-fg-mut">
          {{ state.auth.user.login.slice(0, 1).toUpperCase() }}
        </span>
      </button>

      <div v-if="userOpen" class="fixed inset-0 z-40" @click="userOpen = false" />
      <div v-if="userOpen" class="absolute right-0 top-full mt-1 w-56 bg-panel border border-border shadow-2xl z-50 py-1">
        <div class="px-3 py-2 border-b border-border">
          <div class="text-[14px] text-fg">{{ state.auth.user.name || state.auth.user.login }}</div>
          <div class="text-[12.5px] text-fg-dim">@{{ state.auth.user.login }}</div>
        </div>
        <button class="w-full text-left px-3 py-1.5 text-[14px] text-fg-mut hover:bg-elev/40 hover:text-fg transition-colors cursor-pointer" @click="copyKey">
          {{ copied ? 'Copied!' : 'Copy API key' }}
        </button>
        <button class="w-full text-left px-3 py-1.5 text-[14px] text-fg-mut hover:bg-elev/40 hover:text-fg transition-colors cursor-pointer" @click="rotateKey">
          Rotate API key
        </button>
        <button class="w-full text-left px-3 py-1.5 text-[14px] text-fg-mut hover:bg-elev/40 hover:text-err transition-colors cursor-pointer" @click="signOut">
          Sign out
        </button>
      </div>
    </div>
  </header>
</template>
