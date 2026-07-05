<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fmtStep } from '../fmt'
import { state } from '../store'

const item = computed(() => {
  const lb = state.lightbox
  return lb ? (lb.items[lb.idx] ?? null) : null
})

const sub = computed(() =>
  item.value
    ? `step ${fmtStep(item.value.step)}${item.value.caption ? ` · ${item.value.caption}` : ''}`
    : '',
)

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    state.lightbox = null
    state.expandedChart = null
  }
  const lb = state.lightbox
  if (!lb) return
  if (e.key === 'ArrowLeft') lb.idx = Math.max(0, lb.idx - 1)
  if (e.key === 'ArrowRight') lb.idx = Math.min(lb.items.length - 1, lb.idx + 1)
}

// stepping shouldn't flash: warm the browser cache for the neighbours
watch(
  () => [state.lightbox, state.lightbox?.idx] as const,
  ([lb]) => {
    if (!lb) return
    for (const n of [lb.items[lb.idx - 1], lb.items[lb.idx + 1]]) {
      if (n) new Image().src = n.url
    }
  },
)

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

const overlay = ref<HTMLElement | null>(null)
const scale = ref(1)
const tx = ref(0)
const ty = ref(0)
const dragging = ref(false)

const MIN_SCALE = 0.25
const MAX_SCALE = 12

// a fresh viewer starts back at fit-to-screen; sliding between steps keeps the
// current pan/zoom so the same crop can be compared across steps
watch(
  () => state.lightbox,
  () => {
    scale.value = 1
    tx.value = 0
    ty.value = 0
  },
)

// zoom keeping the screen point (cx, cy) fixed; transform-origin is the
// overlay center, so work in coordinates relative to it
function zoomAt(cx: number, cy: number, next: number) {
  const el = overlay.value
  if (!el) return
  next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next))
  const rect = el.getBoundingClientRect()
  const px = cx - rect.left - rect.width / 2
  const py = cy - rect.top - rect.height / 2
  const k = next / scale.value
  tx.value = px - (px - tx.value) * k
  ty.value = py - (py - ty.value) * k
  scale.value = next
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  zoomAt(e.clientX, e.clientY, scale.value * Math.exp(-e.deltaY * 0.002))
}

function onDblclick(e: MouseEvent) {
  if (scale.value > 1.01 || scale.value < 0.99) reset()
  else zoomAt(e.clientX, e.clientY, 2.5)
}

function reset() {
  scale.value = 1
  tx.value = 0
  ty.value = 0
}

// pointer events cover mouse drag and touch pan/pinch alike
const pointers = new Map<number, { x: number; y: number }>()
let pressedOnBackdrop = false
let moved = false
let travel = 0
let lastMid = { x: 0, y: 0 }
let lastDist = 0

function mid() {
  const pts = [...pointers.values()]
  const x = pts.reduce((s, p) => s + p.x, 0) / pts.length
  const y = pts.reduce((s, p) => s + p.y, 0) / pts.length
  return { x, y }
}

function dist() {
  const pts = [...pointers.values()]
  if (pts.length < 2) return 0
  return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y)
}

function onPointerDown(e: PointerEvent) {
  if (pointers.size === 0) {
    pressedOnBackdrop = e.target === overlay.value
    moved = false
    travel = 0
  }
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
  overlay.value?.setPointerCapture(e.pointerId)
  lastMid = mid()
  lastDist = dist()
  dragging.value = true
}

function onPointerMove(e: PointerEvent) {
  if (!pointers.has(e.pointerId)) return
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
  const m = mid()
  travel += Math.hypot(m.x - lastMid.x, m.y - lastMid.y)
  if (travel > 4) moved = true
  tx.value += m.x - lastMid.x
  ty.value += m.y - lastMid.y
  if (pointers.size >= 2) {
    const d = dist()
    if (lastDist > 0 && d > 0) {
      moved = true
      zoomAt(m.x, m.y, scale.value * (d / lastDist))
    }
    lastDist = d
  }
  lastMid = m
}

function onPointerUp(e: PointerEvent) {
  if (!pointers.has(e.pointerId)) return
  pointers.delete(e.pointerId)
  lastMid = pointers.size ? mid() : lastMid
  lastDist = dist()
  if (pointers.size === 0) {
    dragging.value = false
    // a plain tap on the backdrop (no pan happened) closes the modal
    if (pressedOnBackdrop && !moved) state.lightbox = null
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="state.lightbox"
        ref="overlay"
        class="fixed inset-0 z-60 bg-black/85 backdrop-blur-sm overflow-hidden select-none touch-none"
        :class="dragging ? 'cursor-grabbing' : 'cursor-grab'"
        @wheel.prevent="onWheel"
        @dblclick="onDblclick"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
      >
        <div
          class="absolute inset-0 flex items-center justify-center p-4 sm:p-10 pointer-events-none"
        >
          <img
            v-if="item"
            :src="item.url"
            class="max-w-full max-h-full object-contain rounded-lg shadow-2xl will-change-transform"
            draggable="false"
            :style="{
              transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
            }"
          />
        </div>

        <button
          class="absolute top-3 right-3 w-9 h-9 rounded-lg bg-black/50 hover:bg-black/70 text-fg-dim hover:text-fg flex items-center justify-center transition-colors cursor-pointer"
          title="Close (Esc)"
          @pointerdown.stop
          @click="state.lightbox = null"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path
              d="M4 4l8 8M12 4l-8 8"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
            />
          </svg>
        </button>

        <button
          v-if="Math.abs(scale - 1) > 0.01"
          class="absolute top-3 left-3 h-9 px-3 rounded-lg bg-black/50 hover:bg-black/70 text-[12.5px] text-fg-dim hover:text-fg tabular-nums transition-colors cursor-pointer"
          title="Reset zoom"
          @pointerdown.stop
          @click="reset"
        >
          {{ Math.round(scale * 100) }}%
        </button>

        <div
          class="absolute bottom-0 inset-x-0 pb-4 pt-10 px-4 flex flex-col items-center gap-2 bg-gradient-to-t from-black/70 to-transparent pointer-events-none"
        >
          <!-- step slider: its own pointer island, so dragging it never pans -->
          <div
            v-if="state.lightbox.items.length > 1"
            class="flex items-center gap-2.5 w-full max-w-100 pointer-events-auto cursor-default"
            @pointerdown.stop
            @dblclick.stop
          >
            <input
              v-model.number="state.lightbox.idx"
              type="range"
              :min="0"
              :max="state.lightbox.items.length - 1"
              step="1"
              class="flex-1"
            />
          </div>
          <div class="text-center">
            <div class="text-[14.5px] text-fg">
              {{ state.lightbox.title }}
            </div>
            <div class="text-[13px] text-fg-dim mt-0.5">{{ sub }}</div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
