<script setup lang="ts">
import type { Run } from '../api'
import { computed } from 'vue'
import { runDuration } from '../api'
import { runColor } from '../colors'
import { estimateEta } from '../eta'
import { fmtClock, fmtDuration, fmtMetric, fmtStep } from '../fmt'
import { gpuPrice } from '../gpuPrice'
import { clock, selectedRuns, state } from '../store'

// One status strip per selected run, above the tabs: where it is (step, elapsed,
// progress, ETA), what it costs, and the primary metrics' latest values — the
// glance a training-run stream page gives before any chart.

/** Cluster-wide GPU count: world_size under torchrun/SLURM (one process per GPU),
 *  else what this node saw. 0 when the run recorded nothing. */
function gpus(run: Run): number {
  return run.system?.world_size ?? run.system?.gpu_count ?? 0
}

const cards = computed(() =>
  selectedRuns.value.map((run) => {
    const secs = runDuration(run, clock.now)
    const n = gpus(run)
    // the viewer's own rate wins; else price the detected GPU model; else no cost
    const listed = gpuPrice(run.system?.gpu_names)
    const rate = state.gpuHourRate || listed?.perGpuHour || null
    const eta = run.status === 'running' ? estimateEta(run) : null
    const kpis = Object.entries(run.metric_meta)
      .filter(([, m]) => m.importance === 'primary')
      .map(([key, m]) => ({
        key,
        label: m.series ?? key,
        value: fmtMetric(run.stats[key]?.last ?? run.summary[key], m.unit),
      }))
    return {
      run,
      color: runColor(run.id),
      secs,
      gpus: n,
      gpuName: run.system?.gpu_names?.[0],
      rate,
      rateSource: state.gpuHourRate ? 'manual' : listed?.source,
      cost: n && rate != null ? (n * secs * rate) / 3600 : null,
      eta,
      kpis,
    }
  }),
)

const total = computed(() =>
  cards.value.reduce((sum, c) => sum + (c.cost ?? 0), 0),
)

// cents matter while a run is minutes old; whole dollars once it's real money
const money = (v: number) =>
  `$${v.toLocaleString('en-US', v < 100 ? { minimumFractionDigits: 2, maximumFractionDigits: 2 } : { maximumFractionDigits: 0 })}`
</script>

<template>
  <div v-if="cards.length" class="flex flex-col gap-3">
    <div v-for="(c, i) in cards" :key="c.run.id" class="card min-w-0">
      <!-- identity + progress -->
      <div class="flex items-center gap-4 p-3 flex-wrap">
        <div class="flex items-center gap-2 min-w-0">
          <span
            class="w-2 h-2 rounded-full shrink-0"
            :class="c.run.status === 'running' && 'pulse'"
            :style="{ background: c.color }"
          />
          <span class="font-mono text-[14px] font-medium text-fg truncate">{{
            c.run.name
          }}</span>
          <span
            class="text-[12.5px] shrink-0"
            :class="c.run.status === 'crashed' ? 'text-err/80' : 'text-fg-dim'"
            >{{ c.run.status }}</span
          >
        </div>
        <span
          v-if="c.run.progress != null"
          class="font-mono text-[14px] text-fg tabular-nums shrink-0"
          >step {{ fmtStep(Math.floor(c.run.progress)) }}</span
        >
        <span class="font-mono text-[12.5px] text-fg-mut tabular-nums shrink-0">
          {{ fmtDuration(c.secs) }}
          <span class="text-fg-dim"
            >· started {{ fmtClock(c.run.created_at) }}</span
          >
        </span>
        <template v-if="c.eta && c.eta.fraction != null">
          <div class="flex-1 min-w-[120px] h-1 rounded-full bg-border">
            <div
              class="h-full rounded-full transition-[width] duration-700 ease-out"
              :style="{
                width: `${Math.max(1, c.eta.fraction * 100)}%`,
                background: c.color,
              }"
            />
          </div>
          <span
            class="font-mono text-[12.5px] text-fg-mut tabular-nums shrink-0"
          >
            {{ Math.round(c.eta.fraction * 100) }}%<template
              v-if="c.run.progress_total != null"
            >
              of {{ fmtStep(c.run.progress_total) }}</template
            ><template v-if="c.eta.finishAt">
              · {{ fmtDuration(c.eta.finishAt - clock.now) }} left</template
            >
          </span>
        </template>
      </div>

      <!-- kpi tiles: only what this run actually has — no GPU snapshot, no
           cost/gpu tiles; no primary metrics, no metric tiles -->
      <div
        v-if="c.gpus || c.kpis.length"
        class="grid border-t border-border"
        style="grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))"
      >
        <template v-if="c.gpus">
          <div
            v-if="c.cost != null"
            class="p-3 border-r border-border last:border-r-0"
          >
            <div
              class="flex items-center gap-1 text-[12.5px] text-fg-dim truncate"
            >
              cost so far ·
              <!-- the rate is the AWS list price for the detected GPU unless the
                   viewer types their own; one pref, so one input (first card) -->
              <input
                v-if="i === 0"
                :value="state.gpuHourRate || ''"
                :placeholder="String(c.rate!.toFixed(2))"
                type="number"
                min="0"
                step="0.1"
                title="$ per GPU-hour — leave blank for the AWS list price"
                class="w-14 bg-transparent font-mono tabular-nums text-fg-mut placeholder:text-fg-dim border-b border-dashed border-border outline-none focus:border-accent/60"
                @input="
                  state.gpuHourRate = Number(
                    ($event.target as HTMLInputElement).value,
                  )
                "
              /><span v-else class="font-mono text-fg-mut">{{
                c.rate!.toFixed(2)
              }}</span
              >$/GPU·h
              <span v-if="c.rateSource" class="truncate"
                >· {{ c.rateSource }}</span
              >
            </div>
            <div
              class="font-mono text-[19px] text-fg tabular-nums leading-none mt-1"
            >
              {{ money(c.cost) }}
            </div>
          </div>
          <div class="p-3 border-r border-border last:border-r-0">
            <div class="text-[12.5px] text-fg-dim truncate">
              gpus<template v-if="c.gpuName"> · {{ c.gpuName }}</template>
            </div>
            <div
              class="font-mono text-[19px] text-fg tabular-nums leading-none mt-1"
            >
              {{ c.gpus }}
            </div>
          </div>
        </template>
        <div
          v-for="k in c.kpis"
          :key="k.key"
          class="p-3 border-r border-border last:border-r-0 min-w-0"
        >
          <div class="text-[12.5px] text-fg-dim truncate" :title="k.key">
            {{ k.label }}
          </div>
          <div
            class="font-mono text-[19px] text-fg tabular-nums leading-none mt-1"
          >
            {{ k.value }}
          </div>
        </div>
      </div>
    </div>
    <div
      v-if="cards.length > 1 && total > 0"
      class="self-end font-mono text-[12.5px] text-fg-dim tabular-nums"
    >
      total cost <span class="text-fg-mut">{{ money(total) }}</span>
    </div>
  </div>
</template>
