<script setup lang="ts">
import { state } from '../store'
</script>

<template>
  <div class="h-full flex flex-col items-center justify-center gap-6 bg-bg">
    <div class="flex items-center gap-3 select-none">
      <svg width="36" height="36" viewBox="0 0 32 32" fill="none">
        <rect width="32" height="32" rx="8" fill="#17171c" />
        <path
          d="M7 22 L13 13 L18 17 L25 8"
          stroke="#8b95f6"
          stroke-width="3"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
      <span class="font-semibold tracking-tight text-[30px]">pandm</span>
    </div>

    <p class="text-[14.5px] text-fg-dim">Sign in to see your experiments</p>
    <p v-if="state.cliCode" class="text-[13.5px] text-fg-mut -mt-3">
      then approve CLI sign-in code <span class="font-mono text-accent-hi">{{ state.cliCode }}</span>
    </p>

    <a
      href="/api/auth/login"
      class="flex items-center gap-2.5 px-5 py-2 bg-elev border border-border text-[15px] text-fg hover:border-fg-dim/50 transition-colors select-none"
    >
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <path
          d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"
        />
      </svg>
      Sign in with GitHub
    </a>
  </div>
</template>
