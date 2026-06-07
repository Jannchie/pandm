<script setup lang="ts">
import { onMounted } from 'vue'
import CliApprove from './components/CliApprove.vue'
import Lightbox from './components/Lightbox.vue'
import LoginGate from './components/LoginGate.vue'
import MediaPanel from './components/MediaPanel.vue'
import MetricsPanel from './components/MetricsPanel.vue'
import Sidebar from './components/Sidebar.vue'
import TablePanel from './components/TablePanel.vue'
import TopBar from './components/TopBar.vue'
import { bootstrap, selectedRuns, state } from './store'

const TABS = [
  { id: 'metrics', label: 'Metrics' },
  { id: 'media', label: 'Media' },
  { id: 'table', label: 'Table' },
] as const

onMounted(() => bootstrap())
</script>

<template>
  <div v-if="state.auth.mode === 'loading'" class="h-full bg-bg" />
  <LoginGate v-else-if="state.auth.mode === 'anon'" />
  <div v-else class="h-full flex flex-col bg-bg">
    <TopBar />
    <div class="flex flex-1 min-h-0">
      <Sidebar />
      <main class="flex-1 min-w-0 flex flex-col">
        <!-- tab bar + per-tab controls -->
        <div class="flex items-center px-2 h-9 border-b border-border shrink-0">
          <button
            v-for="t in TABS"
            :key="t.id"
            class="relative h-full px-2.5 text-[12.5px] transition-colors cursor-pointer"
            :class="state.tab === t.id ? 'text-fg' : 'text-fg-dim hover:text-fg-mut'"
            @click="state.tab = t.id"
          >
            {{ t.label }}
            <span v-if="state.tab === t.id" class="absolute inset-x-2 bottom-0 h-px bg-accent-hi" />
          </button>

          <div class="flex-1" />

          <div v-if="state.tab !== 'table'" class="flex items-center gap-2 mr-3" title="columns">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" class="text-fg-dim">
              <rect x="3.5" y="5" width="17" height="14" stroke="currentColor" stroke-width="2" />
              <path d="M9.5 5v14M14.5 5v14" stroke="currentColor" stroke-width="2" />
            </svg>
            <input v-model.number="state.columns" type="range" min="0" max="6" step="1" class="w-16" />
            <span class="text-[11px] text-fg-dim w-7 tabular-nums">{{ state.columns || 'auto' }}</span>
          </div>

          <template v-if="state.tab === 'metrics'">
            <div class="flex items-center gap-2 mr-3" title="smoothing">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" class="text-fg-dim">
                <path
                  d="M3 17C7 17 8 7 12 7s5 10 9 10"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                />
              </svg>
              <input v-model.number="state.smoothing" type="range" min="0" max="0.99" step="0.01" class="w-24" />
              <span class="text-[11px] text-fg-dim w-7 tabular-nums">{{ state.smoothing.toFixed(2) }}</span>
            </div>
            <div class="flex items-center bg-elev rounded-lg p-0.5 mr-2">
              <button
                v-for="x in ['step', 'time'] as const"
                :key="x"
                class="px-2 py-0.5 rounded-md text-[11.5px] transition-colors capitalize"
                :class="state.xAxis === x ? 'bg-panel text-fg shadow-sm' : 'text-fg-dim hover:text-fg-mut'"
                @click="state.xAxis = x"
              >
                {{ x }}
              </button>
            </div>
            <button class="btn font-mono text-[11.5px]" :class="{ 'btn-on !text-accent-hi': state.logScale }" @click="state.logScale = !state.logScale">
              log
            </button>
          </template>
        </div>

        <!-- panel -->
        <div class="flex-1 min-h-0 overflow-y-auto">
          <div
            v-if="state.ready && selectedRuns.length === 0"
            class="h-full flex flex-col items-center justify-center gap-3 text-fg-dim"
          >
            <svg width="36" height="36" viewBox="0 0 32 32" fill="none" class="opacity-40">
              <path
                d="M5 24 L12 13 L18 18 L27 6"
                stroke="currentColor"
                stroke-width="2.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <p class="text-[13px]">
              {{ state.runs.length === 0 ? 'No runs yet — call pandm.init() in your training script' : 'Select runs in the sidebar to compare them' }}
            </p>
          </div>
          <MetricsPanel v-else-if="state.tab === 'metrics'" />
          <MediaPanel v-else-if="state.tab === 'media'" />
          <TablePanel v-else />
        </div>
      </main>
    </div>
    <Lightbox />
    <CliApprove v-if="state.cliCode && state.auth.mode === 'user'" />
  </div>
</template>
