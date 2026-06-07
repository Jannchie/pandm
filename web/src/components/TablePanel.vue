<script setup lang="ts">
import { computed } from 'vue'
import type { Run } from '../api'
import { runColor } from '../colors'
import { fmtClock, fmtDuration, fmtNum } from '../fmt'
import { selectedRuns } from '../store'

const cfgKeys = computed(() => {
  const set = new Set<string>()
  for (const run of selectedRuns.value) for (const k of Object.keys(run.config)) set.add(k)
  return [...set].sort()
})

const sumKeys = computed(() => {
  const set = new Set<string>()
  for (const run of selectedRuns.value) for (const k of Object.keys(run.summary)) set.add(k)
  return [...set].sort()
})

function cfg(run: Run, key: string): string {
  const v = run.config[key]
  if (v === undefined || v === null) return '–'
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}

function duration(run: Run): string {
  const end = run.finished_at ?? run.updated_at
  return fmtDuration(end - run.created_at)
}

const statusColor: Record<string, string> = {
  running: 'text-ok',
  finished: 'text-fg-mut',
  crashed: 'text-err',
}
</script>

<template>
  <div class="p-4">
    <div class="card overflow-x-auto">
      <table class="w-full text-[12.5px] border-collapse whitespace-nowrap">
        <thead>
          <tr class="text-fg-dim text-[10.5px] uppercase tracking-wider">
            <th colspan="4" class="text-left px-3 pt-3 pb-1 font-medium">Run</th>
            <th v-if="cfgKeys.length" :colspan="cfgKeys.length" class="text-left px-3 pt-3 pb-1 font-medium border-l border-border">
              Config
            </th>
            <th v-if="sumKeys.length" :colspan="sumKeys.length" class="text-left px-3 pt-3 pb-1 font-medium border-l border-border">
              Summary
            </th>
          </tr>
          <tr class="text-fg-dim border-b border-border">
            <th class="text-left px-3 py-2 font-medium">name</th>
            <th class="text-left px-3 py-2 font-medium">status</th>
            <th class="text-left px-3 py-2 font-medium">created</th>
            <th class="text-left px-3 py-2 font-medium">duration</th>
            <th v-for="(k, i) in cfgKeys" :key="'c' + k" class="text-left px-3 py-2 font-mono font-medium" :class="{ 'border-l border-border': i === 0 }">
              {{ k }}
            </th>
            <th v-for="(k, i) in sumKeys" :key="'s' + k" class="text-right px-3 py-2 font-mono font-medium" :class="{ 'border-l border-border': i === 0 }">
              {{ k }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="run in selectedRuns"
            :key="run.id"
            class="border-b border-border/60 last:border-b-0 hover:bg-elev/40 transition-colors"
          >
            <td class="px-3 py-2.5">
              <div class="flex items-center gap-2">
                <span class="w-2 h-2 rounded-full shrink-0" :style="{ background: runColor(run.id) }" />
                <span class="text-fg">{{ run.name }}</span>
                <span class="text-fg-dim text-[10.5px] font-mono">{{ run.id }}</span>
              </div>
            </td>
            <td class="px-3 py-2.5" :class="statusColor[run.status]">{{ run.status }}</td>
            <td class="px-3 py-2.5 text-fg-mut">{{ fmtClock(run.created_at) }}</td>
            <td class="px-3 py-2.5 text-fg-mut tabular-nums">{{ duration(run) }}</td>
            <td v-for="(k, i) in cfgKeys" :key="'c' + k" class="px-3 py-2.5 text-fg-mut font-mono text-[12px]" :class="{ 'border-l border-border': i === 0 }">
              {{ cfg(run, k) }}
            </td>
            <td v-for="(k, i) in sumKeys" :key="'s' + k" class="px-3 py-2.5 text-right text-fg font-mono text-[12px] tabular-nums" :class="{ 'border-l border-border': i === 0 }">
              {{ run.summary[k] !== undefined ? fmtNum(run.summary[k]) : '–' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
