// Theme / color-scheme store.
// All visual color comes from CSS custom properties (see styles/tokens.css).
// A theme is just a set of base variable overrides + a Naive UI mode/accent.
// Switching a theme writes those vars onto <html> and persists the choice.
import { defineStore } from 'pinia'

// Each theme overrides only the *base* tokens; derived tokens (accent-soft,
// accent-strong, scrollbars, glow…) are computed from these via color-mix.
export const THEMES = [
  {
    key: 'emerald',
    name: '荧光绿 · 暗',
    mode: 'dark',
    swatch: ['#0c0d0f', '#21fe84'],
    vars: {
      '--app-bg': '#0c0d0f', '--app-bg-soft': '#121418', '--app-surface': '#16181d',
      '--app-surface-2': '#1c1f26', '--app-elevated': '#21242c',
      '--app-text-primary': '#ffffff', '--app-text-secondary': '#b6bcc6', '--app-text-muted': '#7b828d',
      '--app-accent': '#21fe84', '--app-accent-alt': '#4da3ff', '--app-accent-ink': '#06231a',
      '--app-border': 'color-mix(in srgb, #ffffff 9%, transparent)',
      '--app-border-strong': 'color-mix(in srgb, #ffffff 16%, transparent)',
    },
  },
  {
    key: 'ocean',
    name: '海蓝 · 暗',
    mode: 'dark',
    swatch: ['#0a0e16', '#4da3ff'],
    vars: {
      '--app-bg': '#0a0e16', '--app-bg-soft': '#0f1623', '--app-surface': '#131c2b',
      '--app-surface-2': '#1a2536', '--app-elevated': '#213047',
      '--app-text-primary': '#f2f6ff', '--app-text-secondary': '#aebed6', '--app-text-muted': '#71829c',
      '--app-accent': '#4da3ff', '--app-accent-alt': '#41e0d0', '--app-accent-ink': '#04121f',
      '--app-border': 'color-mix(in srgb, #9cc4ff 12%, transparent)',
      '--app-border-strong': 'color-mix(in srgb, #9cc4ff 22%, transparent)',
    },
  },
  {
    key: 'violet',
    name: '暮紫 · 暗',
    mode: 'dark',
    swatch: ['#0e0b16', '#a988ff'],
    vars: {
      '--app-bg': '#0e0b16', '--app-bg-soft': '#150f22', '--app-surface': '#1b1430',
      '--app-surface-2': '#241a3f', '--app-elevated': '#2e2150',
      '--app-text-primary': '#f6f2ff', '--app-text-secondary': '#c2b6d6', '--app-text-muted': '#897f9c',
      '--app-accent': '#a988ff', '--app-accent-alt': '#ff8bd0', '--app-accent-ink': '#160a2b',
      '--app-border': 'color-mix(in srgb, #c9b3ff 12%, transparent)',
      '--app-border-strong': 'color-mix(in srgb, #c9b3ff 22%, transparent)',
    },
  },
  {
    key: 'amber',
    name: '蜜橙 · 暗',
    mode: 'dark',
    swatch: ['#120f0a', '#ff9d42'],
    vars: {
      '--app-bg': '#120f0a', '--app-bg-soft': '#1a150d', '--app-surface': '#211a11',
      '--app-surface-2': '#2b2216', '--app-elevated': '#382c1c',
      '--app-text-primary': '#fff7ec', '--app-text-secondary': '#d8c7ad', '--app-text-muted': '#9c8b71',
      '--app-accent': '#ff9d42', '--app-accent-alt': '#ffd24b', '--app-accent-ink': '#2b1604',
      '--app-border': 'color-mix(in srgb, #ffd9a8 12%, transparent)',
      '--app-border-strong': 'color-mix(in srgb, #ffd9a8 22%, transparent)',
    },
  },
  {
    key: 'paper',
    name: '简约 · 浅',
    mode: 'light',
    swatch: ['#f4f6fa', '#11a36a'],
    vars: {
      '--app-bg': '#eef1f6', '--app-bg-soft': '#f4f6fa', '--app-surface': '#ffffff',
      '--app-surface-2': '#f3f5f9', '--app-elevated': '#ffffff',
      '--app-text-primary': '#12161d', '--app-text-secondary': '#444b57', '--app-text-muted': '#7b828d',
      '--app-accent': '#11a36a', '--app-accent-alt': '#2f7df0', '--app-accent-ink': '#ffffff',
      '--app-border': 'color-mix(in srgb, #0c0d0f 12%, transparent)',
      '--app-border-strong': 'color-mix(in srgb, #0c0d0f 22%, transparent)',
    },
  },
]

const STORAGE_KEY = 'bas:theme'

export const useThemeStore = defineStore('theme', {
  state: () => ({ key: 'emerald' }),
  getters: {
    theme: (s) => THEMES.find((t) => t.key === s.key) || THEMES[0],
    mode() { return this.theme.mode },
    accent() { return this.theme.vars['--app-accent'] },
    accentInk() { return this.theme.vars['--app-accent-ink'] },
  },
  actions: {
    apply() {
      const root = document.documentElement
      const t = this.theme
      Object.entries(t.vars).forEach(([k, v]) => root.style.setProperty(k, v))
      root.dataset.theme = t.key
      root.dataset.mode = t.mode
      // Composited layers that use backdrop-filter (sidebar / titlebar / glass
      // cards) don't always repaint when only CSS custom properties change, so
      // the new theme can look "stuck" until the next reflow. Nudge a root-level
      // filter for one frame to force a full repaint without a visible flash.
      root.style.filter = 'opacity(0.999)'
      requestAnimationFrame(() => { root.style.filter = '' })
    },
    setTheme(key) {
      if (!THEMES.some((t) => t.key === key)) return
      this.key = key
      this.apply()
      try { localStorage.setItem(STORAGE_KEY, key) } catch { /* ignore */ }
    },
    init() {
      let saved = null
      try { saved = localStorage.getItem(STORAGE_KEY) } catch { /* ignore */ }
      if (saved && THEMES.some((t) => t.key === saved)) this.key = saved
      this.apply()
    },
  },
})
