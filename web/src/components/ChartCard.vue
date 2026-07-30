<script setup lang="ts">
import type { ChartDesc } from '../store'
import { computed } from 'vue'
import { runColor } from '../colors'
import { fmtMetric } from '../fmt'
import {
  bestAcross,
  metricSpec,
  resolveAxisSpec,
  selectedRuns,
  state,
} from '../store'
import HistogramChart from './HistogramChart.vue'
import MetricChart from './MetricChart.vue'
import MetricTable from './MetricTable.vue'
import StatCard from './StatCard.vue'

const props = defineProps<{
  desc: ChartDesc
  // the fullscreen copy: taller body, no expand button (the overlay has a close)
  overlay?: boolean
}>()

// Panel charts used to show no description at all — so a run could carry 74
// carefully written notes and display none of them. A panel now synthesises one
// from its members: a single shared note reads once, several read as
// "member: note · member: note", and the untruncated list rides in the title
// attribute (the chart's own tooltip names each line as you hover it).
const notes = computed(() =>
  props.desc.series
    .filter((s) => s.description)
    .map((s) => ({ label: s.label, text: s.description! })),
)

const description = computed(() => {
  const d = props.desc
  if (!d.panel) return metricSpec(d.series[0].key)?.description
  const list = notes.value
  if (!list.length) return undefined
  const distinct = new Set(list.map((n) => n.text))
  if (distinct.size === 1) return list[0].text
  return list.map((n) => `${n.label}: ${n.text}`).join(' · ')
})

const descriptionTitle = computed(() =>
  notes.value.length > 1
    ? notes.value.map((n) => `${n.label}: ${n.text}`).join('\n')
    : undefined,
)

// The latest value, or — when the metric declares a goal — the leading line with a
// ★. A panel gets one too now: with several members it names which member leads.
const badge = computed(() => {
  const d = props.desc
  // a stat card and a table already print their values full size; a heatmap has no
  // single value to lead with
  if (d.kind === 'histogram' || d.kind === 'table' || d.kind === 'stat')
    return null
  const keys = d.series.map((s) => s.key)
  const { spec } = resolveAxisSpec(keys)
  const contested = selectedRuns.value.length > 1 || keys.length > 1
  if (!spec.goal) {
    // no declared direction: a single value is unambiguous, a field of them isn't
    if (contested || !metricSpec(keys[0])) return null
    const best = bestAcross(keys, 'max')
    return best
      ? {
          value: fmtMetric(best.value, spec.unit),
          label: '',
          color: runColor(best.run.id),
          star: false,
        }
      : null
  }
  const best = bestAcross(keys, spec.goal)
  if (!best) return null
  return {
    value: fmtMetric(best.value, spec.unit),
    label:
      keys.length > 1
        ? (d.series.find((s) => s.key === best.key)?.label ?? '')
        : '',
    color: runColor(best.run.id),
    star: contested,
  }
})

// which run this card is pinned to, when it is: a histogram heatmap is inherently
// single-run, and a kept panel is drawn once per lane
const pinned = computed(
  () => props.desc.run ?? props.desc.lane?.runs[0] ?? null,
)
const showPinned = computed(
  () => !!pinned.value && (selectedRuns.value.length > 1 || !!props.desc.lane),
)
const pinnedLabel = computed(
  () => props.desc.lane?.label ?? pinned.value?.name ?? '',
)
</script>

<template>
  <div
    class="card group min-w-0 flex flex-col"
    :class="overlay ? 'p-4 pb-2 shadow-2xl' : 'p-3 pb-1'"
  >
    <div class="flex items-center" :class="overlay ? 'mb-2' : 'mb-1'">
      <span
        class="text-fg font-medium truncate font-mono"
        :class="overlay ? 'text-[14.5px]' : 'text-[14px]'"
        >{{ desc.title }}</span
      >
      <span
        v-if="showPinned"
        class="text-[12px] font-mono truncate shrink-0 ml-2"
        :style="{ color: desc.lane?.color ?? runColor(pinned!.id) }"
        >{{ pinnedLabel }}</span
      >
      <div class="flex-1" />
      <!-- members disagree on an axis-defining field: the panel had to pick one,
           so say so instead of quietly flattening the loser against the axis -->
      <span
        v-if="desc.conflicts?.length"
        class="text-[12px] text-warn shrink-0 mr-1.5 cursor-help"
        :title="`panel members declare different ${desc.conflicts.join(', ')} — the first declaration is used for the axis. Give a member axis=&quot;right&quot; to split them.`"
        >⚠</span
      >
      <span
        v-if="badge"
        class="flex items-center gap-1 text-[12.5px] font-mono mr-1 shrink-0 tabular-nums"
        :style="{ color: badge.color }"
        :title="badge.star ? 'leading' : 'latest'"
      >
        <span v-if="badge.star">★</span
        ><span v-if="badge.label" class="text-fg-dim">{{ badge.label }}</span
        >{{ badge.value }}
      </span>
      <button
        v-if="!overlay"
        class="opacity-0 group-hover:opacity-100 text-fg-dim hover:text-fg transition-all p-1 -m-1 cursor-pointer"
        title="expand"
        @click="state.expandedChart = desc.id"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
          <path
            d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
    </div>
    <p
      v-if="description"
      class="text-[12.5px] text-fg-dim leading-snug mb-1 -mt-0.5"
      :class="overlay ? 'truncate' : 'line-clamp-2'"
      :title="descriptionTitle"
    >
      {{ description }}
    </p>
    <!-- aspect-ratio (not fixed height) so charts scale with the column width.
         mt-auto pins the chart to the card's bottom: grid stretches same-row
         cards to equal height, so a card without a subtitle keeps its chart
         bottom-aligned (and thus top-aligned too — charts are equal size) with
         its neighbours that do have one. -->
    <div
      class="shrink-0 mt-auto"
      :class="
        overlay
          ? 'h-[64vh]'
          : desc.kind === 'stat' || desc.kind === 'table'
            ? ''
            : 'aspect-video'
      "
    >
      <HistogramChart
        v-if="desc.kind === 'histogram'"
        :run="desc.run!"
        :metric-key="desc.series[0].key"
      />
      <StatCard v-else-if="desc.kind === 'stat'" :desc="desc" />
      <MetricTable v-else-if="desc.kind === 'table'" :desc="desc" />
      <MetricChart v-else :desc="desc" />
    </div>
  </div>
</template>
