<script setup>
import { NIcon } from 'naive-ui'
import { CheckmarkOutline } from '@vicons/ionicons5'
import { useThemeStore, THEMES } from '../stores/theme'

const themeStore = useThemeStore()
</script>

<template>
  <div class="theme-picker">
    <div class="tp-head">
      <h3>配色方案</h3>
      <p>切换全局主题，立即生效并自动记忆（下次打开沿用）。</p>
    </div>
    <div class="tp-grid">
      <button
        v-for="t in THEMES"
        :key="t.key"
        class="tp-card"
        :class="{ active: themeStore.key === t.key }"
        @click="themeStore.setTheme(t.key)"
      >
        <div class="tp-preview" :style="{ background: t.swatch[0] }">
          <span class="tp-bar" :style="{ background: t.swatch[1] }" />
          <span class="tp-dot" :style="{ background: t.swatch[1] }" />
          <span class="tp-line" />
          <span class="tp-line short" />
          <span v-if="themeStore.key === t.key" class="tp-check" :style="{ background: t.swatch[1] }">
            <n-icon :component="CheckmarkOutline" :size="14" />
          </span>
        </div>
        <div class="tp-name">{{ t.name }}</div>
      </button>
    </div>
  </div>
</template>

<style scoped>
.tp-head h3 { margin: 0 0 4px; font-size: 16px; }
.tp-head p { margin: 0 0 18px; color: var(--app-text-secondary); font-size: 13px; }
.tp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 16px;
}
.tp-card {
  padding: 10px;
  border: 1px solid var(--app-border);
  border-radius: var(--r-card);
  background: var(--app-surface);
  cursor: pointer;
  text-align: left;
  transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s;
}
.tp-card:hover { transform: translateY(-2px); border-color: var(--app-border-strong); }
.tp-card.active {
  border-color: var(--app-accent);
  box-shadow: 0 0 0 2px var(--app-accent-soft);
}
.tp-preview {
  position: relative;
  height: 92px;
  border-radius: 10px;
  overflow: hidden;
  padding: 12px;
  border: 1px solid var(--app-border);
}
.tp-bar {
  position: absolute; top: 0; left: 0; width: 40px; height: 100%;
  opacity: 0.22;
}
.tp-dot {
  display: block; width: 16px; height: 16px; border-radius: 6px; margin-bottom: 10px;
}
.tp-line {
  display: block; height: 7px; width: 70%; border-radius: 4px;
  background: color-mix(in srgb, #808080 45%, transparent); margin-bottom: 7px;
}
.tp-line.short { width: 45%; }
.tp-check {
  position: absolute; top: 8px; right: 8px;
  width: 22px; height: 22px; border-radius: 50%;
  display: grid; place-items: center; color: #fff;
}
.tp-name { margin-top: 10px; font-weight: 600; font-size: 13px; }
</style>
