<script setup>
import { RemoveOutline, SquareOutline, CloseOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'

function call(method) {
  const api = window.pywebview && window.pywebview.api
  if (api && api[method]) api[method]()
}
</script>

<template>
  <header class="titlebar">
    <div class="brand">
      <span class="logo-dot" />
      <span class="brand-name">连续性批量创作</span>
      <span class="brand-sub">Batch Anime Studio</span>
    </div>
    <div class="spacer" />
    <div class="win-controls">
      <button class="win-btn" title="最小化" @click="call('minimize')">
        <n-icon :component="RemoveOutline" />
      </button>
      <button class="win-btn" title="最大化" @click="call('toggle_maximize')">
        <n-icon :component="SquareOutline" />
      </button>
      <button class="win-btn close" title="关闭" @click="call('close')">
        <n-icon :component="CloseOutline" />
      </button>
    </div>
  </header>
</template>

<style scoped>
.titlebar {
  height: var(--titlebar-h);
  display: flex;
  align-items: center;
  padding: 0 8px 0 16px;
  background: color-mix(in srgb, var(--app-bg-soft) 88%, transparent);
  border-bottom: 1px solid var(--app-border);
  -webkit-app-region: drag;
  user-select: none;
}
.brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.logo-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--app-accent);
  box-shadow: 0 0 10px var(--app-accent);
  align-self: center;
}
.brand-name {
  font-weight: 700;
  letter-spacing: 0.5px;
}
.brand-sub {
  color: var(--app-text-muted);
  font-size: 11px;
  letter-spacing: 1px;
}
.spacer {
  flex: 1;
}
.win-controls {
  display: flex;
  gap: 2px;
  -webkit-app-region: no-drag;
}
.win-btn {
  width: 38px;
  height: 30px;
  display: grid;
  place-items: center;
  border: none;
  background: transparent;
  color: var(--app-text-secondary);
  border-radius: 8px;
  cursor: pointer;
  font-size: 15px;
}
.win-btn:hover {
  background: var(--app-surface-2);
  color: var(--app-text-primary);
}
.win-btn.close:hover {
  background: var(--app-error);
  color: #fff;
}
</style>
