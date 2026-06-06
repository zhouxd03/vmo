<script setup>
import { computed } from 'vue'
import { RemoveOutline, SquareOutline, CloseOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { useHealthStore } from '../stores/health'

const healthStore = useHealthStore()
const healthTitle = computed(() => `${healthStore.label}${healthStore.detail ? `：${healthStore.detail}` : ''}`)

function call(method) {
  const api = window.pywebview && window.pywebview.api
  if (api && api[method]) api[method]()
}

// On a frameless window the title bar must initiate dragging itself. We hand
// off to the OS via the Win32 native move loop (api.start_drag) on left-button
// mousedown over the drag region; this is far more reliable than the CSS
// .pywebview-drag-region / window.move approach. Window-control buttons stop
// propagation so clicking them never starts a drag.
function onDragStart(e) {
  if (e.button !== 0) return
  call('start_drag')
}
</script>

<template>
  <header class="titlebar" @mousedown="onDragStart" @dblclick="call('toggle_maximize')">
    <div class="brand">
      <span class="logo-dot" />
      <span class="brand-name">连续性批量创作</span>
      <span class="brand-sub">Batch Anime Studio</span>
    </div>
    <div class="spacer" />
    <button class="health-pill" :class="healthStore.status" :title="healthTitle" @mousedown.stop @click="healthStore.check()">
      <span />
      {{ healthStore.status === 'online' ? '已连接' : (healthStore.checking ? '检查中' : '连接异常') }}
    </button>
    <div class="win-controls" @mousedown.stop @dblclick.stop>
      <button class="win-btn" title="最小化" @click="call('minimize')">
        <n-icon :component="RemoveOutline" />
      </button>
      <button class="win-btn" title="最大化/还原" @click="call('toggle_maximize')">
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
.health-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 9px;
  margin-right: 8px;
  border: 1px solid var(--app-border);
  border-radius: 999px;
  background: color-mix(in srgb, var(--app-surface-2) 78%, transparent);
  color: var(--app-text-muted);
  cursor: pointer;
  font-size: 11px;
}
.health-pill span {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: #ffc24b;
  box-shadow: 0 0 8px color-mix(in srgb, #ffc24b 70%, transparent);
}
.health-pill.online span {
  background: var(--app-accent);
  box-shadow: 0 0 8px color-mix(in srgb, var(--app-accent) 70%, transparent);
}
.health-pill.offline {
  color: #ffd8d8;
  border-color: color-mix(in srgb, #ff5d6c 42%, var(--app-border));
}
.health-pill.offline span {
  background: #ff5d6c;
  box-shadow: 0 0 8px color-mix(in srgb, #ff5d6c 70%, transparent);
}
.win-controls {
  display: flex;
  gap: 2px;
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
