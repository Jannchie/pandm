<script setup lang="ts">
import { fmtMetric } from '../fmt'
import { alarmBound, alarms, state } from '../store'

// The metrics whose only value is the moment they break (a truncation rate that must
// stay 0, an OOM counter) collapse to one badge each instead of one forever-flat
// chart each. A tripped badge turns red, sorts to the front, and opens the curve —
// which is the one time you want to see it.
</script>

<template>
  <div v-if="alarms.length" class="flex flex-wrap items-center gap-1.5">
    <button
      v-for="a in alarms"
      :key="a.key"
      class="flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-0.5 text-[12.5px] border transition-colors"
      :class="
        a.violated
          ? 'bg-err/12 border-err/45 text-err font-medium cursor-pointer hover:bg-err/20'
          : 'bg-elev/60 border-border text-fg-dim cursor-default'
      "
      :title="
        (a.violated ? 'violated: ' : 'holding: ') +
        a.key +
        ' must be ' +
        alarmBound(a.spec) +
        (a.run ? ` · ${a.run.name}` : '') +
        (a.violated ? ' — click to open the curve' : '')
      "
      @click="a.violated && (state.expandedChart = a.chartId)"
    >
      <span
        class="w-1.5 h-1.5 rounded-full shrink-0"
        :class="a.violated ? 'bg-err' : 'bg-ok/70'"
      />
      <span class="font-mono truncate max-w-60">{{ a.label }}</span>
      <span class="font-mono tabular-nums"
        >{{ a.value === null ? '–' : fmtMetric(a.value, a.unit) }}
      </span>
      <span v-if="a.violated" class="text-[11px] opacity-80">▲</span>
    </button>
  </div>
</template>
