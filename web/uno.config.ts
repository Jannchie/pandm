import { defineConfig, presetWind3 } from 'unocss'

export default defineConfig({
  presets: [presetWind3()],
  theme: {
    colors: {
      bg: '#09090b',
      panel: '#0f0f12',
      elev: '#17171c',
      border: '#1f1f26',
      fg: {
        DEFAULT: '#f4f4f7',
        mut: '#bdbdc7',
        dim: '#909099',
      },
      accent: {
        DEFAULT: '#6e79d6',
        hi: '#8b95f6',
      },
      ok: '#4ade80',
      err: '#f87171',
      warn: '#fbbf24',
    },
    // readable sans body, monospace kept only for aligned numbers / code; nothing
    // is bundled — prefer fonts the user has installed (IBM Plex / HarmonyOS / Noto
    // first, with CJK variants), else fall back to system UI + system CJK.
    fontFamily: {
      sans: "'IBM Plex Sans', 'HarmonyOS Sans SC', 'HarmonyOS Sans', 'Noto Sans SC', 'Noto Sans', system-ui, -apple-system, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif",
      mono: "'IBM Plex Mono', 'Sarasa Mono SC', 'JetBrains Mono', 'Cascadia Code', ui-monospace, SFMono-Regular, Menlo, Consolas, 'PingFang SC', 'Microsoft YaHei', monospace",
    },
  },
  shortcuts: {
    card: 'bg-panel border border-border rounded-xl',
    btn: 'px-2.5 py-1 rounded-md text-[14px] text-fg-mut hover:text-fg hover:bg-elev transition-colors cursor-pointer select-none whitespace-nowrap',
    'btn-on': 'text-fg bg-elev',
    'input-base':
      'bg-elev border border-border rounded-lg text-[14.5px] text-fg placeholder:text-fg-dim outline-none focus:border-accent/60 transition-colors',
  },
})
