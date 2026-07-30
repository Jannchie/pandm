<script setup lang="ts">
import type { Run } from '../api'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { runState } from '../api'
import { runColor } from '../colors'
import { estimateEta } from '../eta'
import { fmtDuration, timeAgo } from '../fmt'
import {
  askConfirm,
  clearMarks,
  clock,
  markRuns,
  removeRuns,
  selectAll,
  selectNone,
  selectRun,
  state,
  visibleRuns,
} from '../store'

// the shared 1 s clock: "time left" counts down between polls (finishAt is fixed),
// and a run that goes quiet crosses into `stale` with no new data to trigger it
const now = computed(() => clock.now)

// pair each run with its ETA; recomputed only when the run data changes (not every
// tick). Staleness is read per render instead — it depends on the clock, and folding
// it in here would re-estimate every ETA once a second.
const rows = computed(() =>
  visibleRuns.value.map((run) => ({
    run,
    eta: run.status === 'running' ? estimateEta(run) : null,
  })),
)

// the state to display: `crashed` with no finished_at means nobody wrote a verdict,
// the process just vanished — see api.runStale
const stateOf = (run: Run) => runState(run)

const MIN_W = 200
const MAX_W = 560

// desktop-only width: on mobile the drawer keeps its fixed `w-70`/`max-w` sizing
const isDesktop = ref(window.matchMedia('(min-width: 768px)').matches)
const mq = window.matchMedia('(min-width: 768px)')
const onMq = (e: MediaQueryListEvent) => (isDesktop.value = e.matches)
mq.addEventListener('change', onMq)
onUnmounted(() => mq.removeEventListener('change', onMq))

const asideStyle = computed(() =>
  isDesktop.value ? { width: `${state.sidebarWidth}px` } : {},
)

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

// ---- marquee (rubber-band) selection over the run list --------------------
// Drag across empty space to mark a range of runs; the mark is a transient
// bulk-action set (state.marked), kept separate from the compare selection.
const listRef = ref<HTMLElement | null>(null)
// y coordinates live in the list's *content* space (clientY - box.top +
// scrollTop) so the anchor sticks to the rows it was dropped on: scrolling
// stretches the band instead of sliding it off the rows already caught.
const marquee = ref<{
  x0: number
  y0: number
  x1: number
  y1: number
} | null>(null)
const listScrollTop = ref(0)

// content-space band; unclamped, so rows scrolled out of view still count
const marqueeBand = computed(() => {
  const m = marquee.value
  if (!m) return null
  return { top: Math.min(m.y0, m.y1), bottom: Math.max(m.y0, m.y1) }
})

// screen-space rectangle for the overlay, clamped to the list's visible box
const marqueeRect = computed(() => {
  const m = marquee.value
  const band = marqueeBand.value
  if (!m || !band || !listRef.value) return null
  const box = listRef.value.getBoundingClientRect()
  const originY = box.top - listScrollTop.value
  const left = Math.max(box.left, Math.min(m.x0, m.x1))
  const right = Math.min(box.right, Math.max(m.x0, m.x1))
  const top = Math.max(box.top, originY + band.top)
  const bottom = Math.min(box.bottom, originY + band.bottom)
  if (right <= left || bottom <= top) return null
  return { left, top, width: right - left, height: bottom - top }
})

// clientY → content-space y within the list
function contentY(clientY: number): number {
  const el = listRef.value
  if (!el) return clientY
  return clientY - el.getBoundingClientRect().top + el.scrollTop
}

const DRAG_THRESHOLD = 5 // px before a press becomes a marquee (vs a click)
let pressStart: { x: number; y: number; shift: boolean } | null = null
let marqueeBase: string[] = [] // marks to keep when shift-extending
let lastPointerY = 0
let autoScroll = 0
let didMarquee = false // set on a completed drag, to swallow the trailing click

function idsInMarquee(): string[] {
  const el = listRef.value
  const band = marqueeBand.value
  if (!el || !band) return []
  const boxTop = el.getBoundingClientRect().top
  const st = el.scrollTop
  const hits: string[] = []
  for (const row of el.querySelectorAll<HTMLElement>('[data-run-id]')) {
    const b = row.getBoundingClientRect()
    const top = b.top - boxTop + st
    if (top + b.height >= band.top && top <= band.bottom)
      hits.push(row.dataset.runId!)
  }
  return hits
}

function applyMarquee() {
  // Set-union dedups and, when marqueeBase is empty, yields exactly the hits
  markRuns([...new Set([...marqueeBase, ...idsInMarquee()])])
}

function onListPointerDown(e: PointerEvent) {
  didMarquee = false // clear any stale suppression from an earlier drag
  // marquee is a mouse/pen drag; on touch a drag scrolls the list, so leave
  // touch to tap-select and the dot checkbox
  if (e.button !== 0 || e.pointerType === 'touch') return
  // ignore presses that land on the dot / trash / other controls
  if ((e.target as HTMLElement).closest('[data-nomarquee]')) return
  pressStart = { x: e.clientX, y: e.clientY, shift: e.shiftKey }
  lastPointerY = e.clientY
  listScrollTop.value = listRef.value?.scrollTop ?? 0
  window.addEventListener('pointermove', onWinPointerMove)
  window.addEventListener('pointerup', onWinPointerUp)
}

function onWinPointerMove(e: PointerEvent) {
  lastPointerY = e.clientY
  if (!marquee.value && pressStart) {
    const moved = Math.hypot(e.clientX - pressStart.x, e.clientY - pressStart.y)
    if (moved < DRAG_THRESHOLD) return
    // cross the threshold → begin marquee
    marqueeBase = pressStart.shift ? [...state.marked] : []
    marquee.value = {
      x0: pressStart.x,
      y0: contentY(pressStart.y),
      x1: e.clientX,
      y1: contentY(e.clientY),
    }
    document.body.style.userSelect = 'none'
    startAutoScroll()
  }
  if (marquee.value) {
    marquee.value.x1 = e.clientX
    marquee.value.y1 = contentY(e.clientY)
    listScrollTop.value = listRef.value?.scrollTop ?? 0
    applyMarquee()
  }
}

// wheel/trackpad scrolling mid-drag moves the content under a still cursor:
// re-anchor the loose end to where the pointer now points and re-run the hits
function onListScroll() {
  const el = listRef.value
  if (!el) return
  listScrollTop.value = el.scrollTop
  if (!marquee.value) return
  marquee.value.y1 = contentY(lastPointerY)
  applyMarquee()
}

function onWinPointerUp() {
  window.removeEventListener('pointermove', onWinPointerMove)
  window.removeEventListener('pointerup', onWinPointerUp)
  stopAutoScroll()
  if (marquee.value) {
    didMarquee = true // suppress the click that fires right after this pointerup
    marquee.value = null
    document.body.style.userSelect = ''
  }
  pressStart = null
}

// swallow the click synthesized after a drag so it doesn't single-select a row
function onListClickCapture(e: MouseEvent) {
  if (didMarquee) {
    didMarquee = false
    e.stopPropagation()
    e.preventDefault()
  }
}

// keep dragging past the visible edge by scrolling the list toward the cursor
function startAutoScroll() {
  if (autoScroll) return
  autoScroll = window.setInterval(() => {
    const el = listRef.value
    if (!el) return
    const box = el.getBoundingClientRect()
    const edge = 28
    let dy = 0
    if (lastPointerY < box.top + edge) dy = lastPointerY - (box.top + edge)
    else if (lastPointerY > box.bottom - edge)
      dy = lastPointerY - (box.bottom - edge)
    if (dy !== 0) {
      el.scrollTop += Math.max(-24, Math.min(24, dy * 0.5))
      onListScroll()
    }
  }, 16)
}
function stopAutoScroll() {
  if (autoScroll) {
    clearInterval(autoScroll)
    autoScroll = 0
  }
}

// ---- bulk delete (row trash, marquee marks, or the compare selection) -------
async function confirmAndRemove(ids: string[]) {
  if (!ids.length) return
  const many = ids.length > 1
  const names = ids
    .map((id) => state.runs.find((r) => r.id === id)?.name ?? id)
    .slice(0, 5)
  const more = ids.length > 5 ? `\n…and ${ids.length - 5} more` : ''
  const ok = await askConfirm({
    title: `Delete ${ids.length} run${many ? 's' : ''}?`,
    body: `${names.join('\n')}${more}\n\n${many ? 'They' : 'It'} and ${many ? 'their' : 'its'} media will be removed. This cannot be undone.`,
    confirmLabel: 'Delete',
    danger: true,
  })
  if (ok) removeRuns(ids)
}

function onKeydown(e: KeyboardEvent) {
  // let the confirm dialog own the keyboard while it's open
  if (state.confirm) return
  const t = e.target as HTMLElement | null
  const inField =
    t &&
    (t.tagName === 'INPUT' ||
      t.tagName === 'TEXTAREA' ||
      t.tagName === 'SELECT' ||
      t.isContentEditable)
  if (inField) return
  // Escape drops the marquee marks
  if (e.key === 'Escape' && state.marked.length) {
    clearMarks()
    return
  }
  if (e.key !== 'Delete' && e.key !== 'Backspace') return
  // marquee marks win; otherwise fall back to the compare selection
  const targetIds = state.marked.length ? state.marked : state.selected
  if (!targetIds.length) return
  e.preventDefault()
  confirmAndRemove([...targetIds])
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  stopAutoScroll()
})
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
    <!-- search (37px = 36px row + 1px border, so content height and border line match the main tab bar) -->
    <div class="relative h-[37px] shrink-0 border-b border-border">
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        class="absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-dim"
      >
        <circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2" />
        <path
          d="M20 20l-3.5-3.5"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
        />
      </svg>
      <input
        v-model="state.search"
        placeholder="Filter runs…"
        class="w-full h-full bg-transparent border-none rounded-none pl-8 pr-3 text-[14.5px] text-fg placeholder:text-fg-dim outline-none"
      />
    </div>

    <!-- selection controls: marquee marks (bulk delete) take over the bar when set -->
    <div
      v-if="state.marked.length"
      class="flex items-center px-2.5 py-1 text-[12.5px] text-accent-hi"
    >
      <span>{{ state.marked.length }} marked</span>
      <div class="flex-1" />
      <button
        class="hover:text-err transition-colors"
        title="Delete marked runs"
        @click="confirmAndRemove([...state.marked])"
      >
        delete
      </button>
      <span class="mx-1.5 opacity-40">·</span>
      <button class="hover:text-fg-mut transition-colors" @click="clearMarks()">
        clear
      </button>
    </div>
    <div v-else class="flex items-center px-2.5 py-1 text-[12.5px] text-fg-dim">
      <span
        >{{ state.selected.length }} of {{ visibleRuns.length }} selected</span
      >
      <div class="flex-1" />
      <button class="hover:text-fg-mut transition-colors" @click="selectAll">
        all
      </button>
      <span class="mx-1.5 opacity-40">·</span>
      <button class="hover:text-fg-mut transition-colors" @click="selectNone">
        none
      </button>
    </div>

    <!-- run list -->
    <div
      ref="listRef"
      class="relative flex-1 min-h-0 overflow-y-auto"
      @pointerdown="onListPointerDown"
      @click.capture="onListClickCapture"
      @scroll="onListScroll"
    >
      <div
        v-for="{ run, eta } in rows"
        :key="run.id"
        :data-run-id="run.id"
        class="group relative flex items-center gap-2 px-2.5 py-1 cursor-pointer transition-colors"
        :class="[
          state.marked.includes(run.id)
            ? 'bg-accent/15 ring-1 ring-inset ring-accent/50'
            : state.selected.includes(run.id)
              ? 'bg-elev/70'
              : 'hover:bg-elev/40',
        ]"
        @click="
          (clearMarks(), selectRun(run.id, $event.ctrlKey || $event.metaKey))
        "
      >
        <!-- clickable dot = compare-selection checkbox (shows a check when on) -->
        <button
          data-nomarquee
          class="relative grid place-items-center w-4 h-4 rounded-full shrink-0 transition-all cursor-pointer after:absolute after:content-[''] after:-inset-1.5"
          :title="
            state.selected.includes(run.id)
              ? 'Remove from comparison'
              : 'Add to comparison'
          "
          :style="
            state.selected.includes(run.id)
              ? { background: runColor(run.id) }
              : { boxShadow: 'inset 0 0 0 1.5px #3a3a44' }
          "
          @click.stop="selectRun(run.id, true)"
        >
          <svg
            v-if="state.selected.includes(run.id)"
            width="10"
            height="10"
            viewBox="0 0 24 24"
            fill="none"
            class="text-bg"
          >
            <path
              d="M5 13l4 4L19 7"
              stroke="currentColor"
              stroke-width="3.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <div class="flex-1 min-w-0">
          <div
            class="text-[14.5px] truncate leading-tight"
            :class="state.selected.includes(run.id) ? 'text-fg' : 'text-fg-mut'"
          >
            {{ run.name }}
          </div>
          <div
            v-if="run.description"
            class="text-[12.5px] text-fg-mut/80 truncate leading-tight"
          >
            {{ run.description }}
          </div>
          <div
            v-if="run.group || run.tags.length"
            class="flex items-center gap-1 flex-wrap mt-0.5"
          >
            <span
              v-if="run.group"
              class="text-[11px] leading-tight px-1 rounded bg-elev text-fg-mut shrink-0"
              :title="`group: ${run.group}`"
              >▤ {{ run.group }}</span
            >
            <span
              v-for="t in run.tags"
              :key="t"
              class="text-[11px] leading-tight px-1 rounded bg-elev/60 text-fg-dim shrink-0"
            >
              {{ t }}
            </span>
          </div>
          <div class="text-[12.5px] text-fg-dim truncate leading-tight">
            <template v-if="stateOf(run) === 'stale'">
              <span class="text-warn"
                >no report for {{ fmtDuration(now - run.updated_at) }}</span
              >
            </template>
            <template
              v-else-if="
                stateOf(run) === 'running' && eta && eta.fraction != null
              "
            >
              {{ Math.round(eta.fraction * 100) }}%<template
                v-if="eta.finishAt"
              >
                · ~{{ fmtDuration(eta.finishAt - now) }} left</template
              >
            </template>
            <template v-else>
              <template v-if="!state.project">{{ run.project }} · </template
              >{{ timeAgo(run.created_at) }}
            </template>
          </div>
        </div>
        <span
          v-if="stateOf(run) === 'running'"
          class="w-1.5 h-1.5 rounded-full bg-ok pulse shrink-0"
          title="running"
        />
        <!-- `running` with a dead heartbeat: the process was killed without getting
             to write `crashed`, so the stored status can't be trusted on its own -->
        <span
          v-else-if="stateOf(run) === 'stale'"
          class="text-warn/80 text-[12px] leading-none font-bold shrink-0"
          title="stale — status says running but nothing has been reported for a while (OOM-killed? pod restarted?)"
          >?</span
        >
        <svg
          v-else-if="stateOf(run) === 'crashed'"
          width="11"
          height="11"
          viewBox="0 0 24 24"
          fill="none"
          class="text-err/80 shrink-0"
          title="crashed"
        >
          <path
            d="M6 6l12 12M18 6L6 18"
            stroke="currentColor"
            stroke-width="2.5"
            stroke-linecap="round"
          />
        </svg>
        <button
          data-nomarquee
          class="opacity-0 group-hover:opacity-100 text-fg-dim hover:text-err transition-all shrink-0 cursor-pointer"
          title="Delete run"
          @click.stop="confirmAndRemove([run.id])"
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
          v-if="stateOf(run) === 'running' && eta && eta.fraction != null"
          class="absolute left-0 bottom-0 h-0.5 rounded-r-full transition-[width] duration-700 ease-out pointer-events-none"
          :style="{
            width: `${Math.max(2, eta.fraction * 100)}%`,
            background: runColor(run.id),
          }"
        />
      </div>

      <div
        v-if="state.ready && visibleRuns.length === 0"
        class="px-2 py-8 text-center text-[13.5px] text-fg-dim"
      >
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

    <!-- marquee overlay: teleported to body so the aside's transform doesn't
         re-anchor this `fixed` box (which shifted it downward) -->
    <Teleport to="body">
      <div
        v-if="marqueeRect"
        class="fixed z-[60] pointer-events-none border border-accent-hi bg-accent/15 rounded-sm"
        :style="{
          left: `${marqueeRect.left}px`,
          top: `${marqueeRect.top}px`,
          width: `${marqueeRect.width}px`,
          height: `${marqueeRect.height}px`,
        }"
      />
    </Teleport>
  </aside>
</template>
