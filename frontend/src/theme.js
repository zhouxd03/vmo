// Naive UI theme overrides, derived from the active color scheme.
import { darkTheme, lightTheme } from 'naive-ui'

export { darkTheme, lightTheme }

const FONT =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Roboto, Helvetica, Arial, sans-serif'

// Naive UI parses theme colors with seemly, which understands hex/rgb(a) but
// NOT CSS color functions like color-mix(). Passing a color-mix() string here
// throws inside <Tag>/<Spin>/<Button> render. So derive hover/pressed shades as
// concrete hex by mixing in JS instead.
function hexToRgb(hex) {
  let h = String(hex).replace('#', '').trim()
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  const n = parseInt(h, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}
function rgbToHex(rgb) {
  return '#' + rgb.map((x) => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, '0')).join('')
}
// mix `a` and `b` with weight `wa` on `a` (0..1). Returns hex.
function mixHex(a, b, wa) {
  const ra = hexToRgb(a)
  const rb = hexToRgb(b)
  return rgbToHex(ra.map((x, i) => x * wa + rb[i] * (1 - wa)))
}

// Build Naive UI overrides for a given theme object (from stores/theme.js).
export function buildThemeOverrides(theme) {
  const v = theme?.vars || {}
  const accent = v['--app-accent'] || '#21fe84'
  const ink = v['--app-accent-ink'] || '#06231a'
  const alt = v['--app-accent-alt'] || '#4da3ff'
  return {
    common: {
      primaryColor: accent,
      primaryColorHover: mixHex(accent, '#ffffff', 0.82),
      primaryColorPressed: mixHex(accent, '#000000', 0.86),
      primaryColorSuppl: accent,
      infoColor: alt,
      successColor: accent,
      warningColor: '#ffc24b',
      errorColor: '#ff5d6c',
      borderRadius: '10px',
      bodyColor: v['--app-bg'] || '#0c0d0f',
      cardColor: v['--app-surface'] || '#16181d',
      modalColor: v['--app-surface'] || '#16181d',
      popoverColor: v['--app-surface-2'] || '#1c1f26',
      fontFamily: FONT,
    },
    Card: { borderRadius: '16px' },
    Button: {
      textColorPrimary: ink,
      textColorHoverPrimary: ink,
      textColorPressedPrimary: ink,
      textColorFocusPrimary: ink,
    },
  }
}

// Backwards-compatible default (emerald dark) for any non-reactive imports.
export const themeOverrides = buildThemeOverrides({
  vars: { '--app-accent': '#21fe84', '--app-accent-ink': '#06231a', '--app-accent-alt': '#4da3ff' },
})
