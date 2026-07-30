<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import CliApprove from './components/CliApprove.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import KeyGate from './components/KeyGate.vue'
import Lightbox from './components/Lightbox.vue'
import LoginGate from './components/LoginGate.vue'
import MediaPanel from './components/MediaPanel.vue'
import MetricsPanel from './components/MetricsPanel.vue'
import ScatterPanel from './components/ScatterPanel.vue'
import Sidebar from './components/Sidebar.vue'
import TablePanel from './components/TablePanel.vue'
import TopBar from './components/TopBar.vue'
import { fmtStep } from './fmt'
import {
  bootstrap,
  groupedSelection,
  selectedRuns,
  state,
  tabCounts,
} from './store'

const TABS = [
  { id: 'metrics', label: 'Metrics' },
  { id: 'media', label: 'Media' },
  { id: 'table', label: 'Table' },
  { id: 'scatter', label: 'Scatter' },
] as const

onMounted(() => bootstrap())

// the slider fires per 0.01 step while dragging; each write to state.smoothing
// recomputes EMA + redraws every chart, so debounce the global write and keep
// only the label instant
const smoothing = ref(state.smoothing)
let smoothTimer = 0
watch(smoothing, (v) => {
  clearTimeout(smoothTimer)
  smoothTimer = window.setTimeout(() => (state.smoothing = v), 120)
})
</script>

<template>
  <div v-if="state.auth.mode === 'loading'" class="h-full bg-bg" />
  <LoginGate v-else-if="state.auth.mode === 'anon'" />
  <KeyGate v-else-if="state.auth.mode === 'key'" />
  <div v-else class="h-full flex flex-col bg-bg">
    <TopBar />
    <div class="flex flex-1 min-h-0">
      <Sidebar />
      <main class="flex-1 min-w-0 flex flex-col">
        <!-- tab bar + per-tab controls -->
        <div
          class="flex flex-col md:flex-row md:h-[37px] md:items-center border-b border-border shrink-0"
        >
          <!-- tabs: own row on mobile -->
          <div
            class="flex items-center px-2 h-9 shrink-0 border-b border-border md:border-b-0"
          >
            <button
              v-for="t in TABS"
              :key="t.id"
              class="relative h-full px-2.5 flex items-center gap-1.5 text-[14px] transition-colors cursor-pointer shrink-0"
              :class="
                state.tab === t.id ? 'text-fg' : 'text-fg-dim hover:text-fg-mut'
              "
              @click="state.tab = t.id"
            >
              {{ t.label }}
              <!-- how much this tab holds for the current selection; absent rather
                   than 0 when it holds nothing — the tab's own empty state says why -->
              <span
                v-if="tabCounts[t.id]"
                class="px-1.25 py-px rounded-full text-[11px] leading-none tabular-nums transition-colors"
                :class="
                  state.tab === t.id
                    ? 'bg-accent-hi/15 text-accent-hi'
                    : 'bg-elev text-fg-dim'
                "
                >{{ tabCounts[t.id] }}</span
              >
              <span
                v-if="state.tab === t.id"
                class="absolute inset-x-2 bottom-0 h-px bg-accent-hi"
              />
            </button>
          </div>

          <!-- controls: own row on mobile, right-aligned on desktop -->
          <div
            class="items-center px-2 min-h-9 md:h-9 flex-wrap gap-y-1 md:flex-nowrap md:flex md:flex-1 md:min-w-0 md:overflow-x-auto"
            :class="
              state.tab === 'metrics' || state.tab === 'media'
                ? 'flex'
                : 'hidden'
            "
          >
            <!-- pushes controls right on desktop; on mobile they left-align and wrap -->
            <div class="hidden md:block flex-1 min-w-2" />

            <div
              v-if="state.tab === 'media' && state.mediaSteps.length > 1"
              class="flex items-center gap-2 mr-3 shrink-0"
              title="media step"
            >
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                class="text-fg-dim"
              >
                <path
                  d="M4 12h16M8 6l-4 6 4 6M16 6l4 6-4 6"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              <input
                v-model.number="state.mediaIdx"
                type="range"
                :min="0"
                :max="state.mediaSteps.length - 1"
                step="1"
                class="w-32 md:w-40"
              />
              <span
                class="text-[12.5px] text-fg-dim tabular-nums whitespace-nowrap"
                >step {{ fmtStep(state.mediaSteps[state.mediaIdx] ?? 0) }}</span
              >
            </div>

            <!-- the wrapper's static md:flex beats a dynamic `hidden`, so each
                 control still has to opt out of the tabs it means nothing on -->
            <div
              v-if="state.tab === 'metrics' || state.tab === 'media'"
              class="hidden md:flex items-center gap-2 mr-3 shrink-0"
              title="columns"
            >
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                class="text-fg-dim"
              >
                <rect
                  x="3.5"
                  y="5"
                  width="17"
                  height="14"
                  stroke="currentColor"
                  stroke-width="2"
                />
                <path
                  d="M9.5 5v14M14.5 5v14"
                  stroke="currentColor"
                  stroke-width="2"
                />
              </svg>
              <input
                v-model.number="state.columns"
                type="range"
                min="0"
                max="6"
                step="1"
                class="w-16"
              />
              <span class="text-[12.5px] text-fg-dim w-7 tabular-nums">{{
                state.columns || 'auto'
              }}</span>
            </div>

            <template v-if="state.tab === 'metrics'">
              <!-- 107 keys in one run is normal; a filter is not a luxury -->
              <div class="relative mr-3 shrink-0">
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  class="absolute left-2 top-1/2 -translate-y-1/2 text-fg-dim pointer-events-none"
                >
                  <circle
                    cx="11"
                    cy="11"
                    r="7"
                    stroke="currentColor"
                    stroke-width="2"
                  />
                  <path
                    d="M20 20l-3.5-3.5"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                  />
                </svg>
                <input
                  v-model="state.metricSearch"
                  placeholder="Filter metrics…"
                  class="input-base w-36 md:w-44 h-6.5 pl-7 pr-6 !text-[13px]"
                />
                <button
                  v-if="state.metricSearch"
                  class="absolute right-1.5 top-1/2 -translate-y-1/2 text-fg-dim hover:text-fg transition-colors cursor-pointer"
                  title="clear"
                  @click="state.metricSearch = ''"
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M6 6l12 12M18 6L6 18"
                      stroke="currentColor"
                      stroke-width="2.5"
                      stroke-linecap="round"
                    />
                  </svg>
                </button>
              </div>
              <div
                class="flex items-center gap-2 mr-3 shrink-0"
                title="smoothing"
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  class="text-fg-dim"
                >
                  <path
                    d="M3 17C7 17 8 7 12 7s5 10 9 10"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                  />
                </svg>
                <input
                  v-model.number="smoothing"
                  type="range"
                  min="0"
                  max="0.99"
                  step="0.01"
                  class="w-24"
                />
                <span class="text-[12.5px] text-fg-dim w-7 tabular-nums">{{
                  smoothing.toFixed(2)
                }}</span>
              </div>
              <div
                class="flex items-center bg-elev rounded-lg p-0.5 mr-2 shrink-0"
              >
                <button
                  v-for="x in ['step', 'time', 'rtime'] as const"
                  :key="x"
                  class="px-2 py-0.5 rounded-md text-[13px] transition-colors capitalize"
                  :class="
                    state.xAxis === x
                      ? 'bg-panel text-fg shadow-sm'
                      : 'text-fg-dim hover:text-fg-mut'
                  "
                  :title="
                    x === 'rtime'
                      ? 'elapsed since each run started'
                      : `x-axis: ${x}`
                  "
                  @click="state.xAxis = x"
                >
                  {{ x === 'rtime' ? 'elapsed' : x }}
                </button>
              </div>
              <button
                class="btn font-mono text-[13px] shrink-0"
                :class="{ 'btn-on !text-accent-hi': state.logScale }"
                title="log y-axis on every chart (a metric can also declare scale='log')"
                @click="state.logScale = !state.logScale"
              >
                log
              </button>
              <!-- one training split across resumed runs is one curve -->
              <button
                v-if="groupedSelection"
                class="btn font-mono text-[13px] shrink-0"
                :class="{ 'btn-on !text-accent-hi': state.stitchGroups }"
                title="stitch same-group runs into one continuous curve"
                @click="state.stitchGroups = !state.stitchGroups"
              >
                ▤ stitch
              </button>
              <!-- comparing runs normally dissolves panels; keep them side by side -->
              <button
                v-if="selectedRuns.length > 1"
                class="btn font-mono text-[13px] shrink-0"
                :class="{ 'btn-on !text-accent-hi': state.keepPanels }"
                title="keep panels whole when comparing runs (one copy per run)"
                @click="state.keepPanels = !state.keepPanels"
              >
                ⧉ panels
              </button>
              <button
                v-if="state.xRange"
                class="btn font-mono text-[13px] shrink-0 !text-accent-hi"
                title="reset x zoom (or double-click any chart)"
                @click="state.xRange = null"
              >
                ⤢ reset
              </button>
            </template>
          </div>
        </div>

        <!-- panel -->
        <div class="flex-1 min-h-0 overflow-y-auto">
          <div
            v-if="
              state.ready &&
              selectedRuns.length === 0 &&
              state.tab !== 'scatter'
            "
            class="h-full flex flex-col items-center justify-center gap-3 text-fg-dim"
          >
            <svg
              width="36"
              height="36"
              viewBox="0 0 32 32"
              fill="none"
              class="opacity-40"
            >
              <path
                d="M5 24 L12 13 L18 18 L27 6"
                stroke="currentColor"
                stroke-width="2.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <p class="text-[14.5px]">
              {{
                state.runs.length === 0
                  ? 'No runs yet — call pandm.init() in your training script'
                  : 'Select runs in the sidebar to compare them'
              }}
            </p>
          </div>
          <MetricsPanel v-else-if="state.tab === 'metrics'" />
          <MediaPanel v-else-if="state.tab === 'media'" />
          <ScatterPanel v-else-if="state.tab === 'scatter'" />
          <TablePanel v-else />
        </div>
      </main>
    </div>
    <Lightbox />
    <ConfirmDialog />
    <CliApprove v-if="state.cliCode && state.auth.mode === 'user'" />
  </div>
</template>
