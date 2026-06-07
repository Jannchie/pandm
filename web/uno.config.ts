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
        DEFAULT: '#e8e8ec',
        mut: '#9a9aa6',
        dim: '#6c6c78',
      },
      accent: {
        DEFAULT: '#6e79d6',
        hi: '#8b95f6',
      },
      ok: '#4ade80',
      err: '#f87171',
      warn: '#fbbf24',
    },
  },
  shortcuts: {
    card: 'bg-panel border border-border rounded-xl',
    btn: 'px-2.5 py-1 rounded-md text-[12.5px] text-fg-mut hover:text-fg hover:bg-elev transition-colors cursor-pointer select-none whitespace-nowrap',
    'btn-on': 'text-fg bg-elev',
    'input-base':
      'bg-elev border border-border rounded-lg text-[13px] text-fg placeholder:text-fg-dim outline-none focus:border-accent/60 transition-colors',
  },
})
