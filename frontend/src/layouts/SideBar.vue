<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon } from 'naive-ui'
import {
  GridOutline, CloudUploadOutline, DocumentTextOutline, CubeOutline,
  ImageOutline, VideocamOutline, GitNetworkOutline, AlbumsOutline,
  ColorWandOutline, SettingsOutline, GridSharp, BugOutline,
} from '@vicons/ionicons5'

const route = useRoute()
const router = useRouter()

const iconMap = {
  home: GridOutline,
  import: CloudUploadOutline,
  script: DocumentTextOutline,
  assets: CubeOutline,
  worktable: GridSharp,
  image: ImageOutline,
  video: VideocamOutline,
  pipeline: GitNetworkOutline,
  library: AlbumsOutline,
  templates: ColorWandOutline,
  settings: SettingsOutline,
  logs: BugOutline,
}

const items = computed(() =>
  router.options.routes.map((r) => ({
    name: r.name,
    path: r.path,
    title: r.meta.title,
    icon: iconMap[r.meta.icon] || GridOutline,
  }))
)

const groups = computed(() => [
  { label: '工作流', names: ['home', 'import', 'script', 'assets'] },
  { label: '批量生成', names: ['worktable'] },
  { label: '资源 / 配置', names: ['library', 'templates', 'settings', 'logs'] },
])

function itemsOf(names) {
  return items.value.filter((i) => names.includes(i.name))
}
</script>

<template>
  <aside class="sidebar">
    <nav>
      <div v-for="g in groups" :key="g.label" class="group">
        <div class="group-label">{{ g.label }}</div>
        <button
          v-for="it in itemsOf(g.names)"
          :key="it.name"
          class="nav-item"
          :class="{ active: route.name === it.name }"
          @click="router.push(it.path)"
        >
          <n-icon :component="it.icon" size="18" />
          <span>{{ it.title }}</span>
        </button>
      </div>
    </nav>
    <div class="sidebar-foot">
      <span class="ver">v0.1.0 · 单机版</span>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
  background: color-mix(in srgb, var(--app-bg-soft) 70%, transparent);
  border-right: 1px solid var(--app-border);
  overflow-y: auto;
}
nav {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.group-label {
  font-size: 11px;
  letter-spacing: 1.5px;
  color: var(--app-text-muted);
  text-transform: uppercase;
  padding: 0 10px 6px;
}
.nav-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  margin-bottom: 2px;
  border: none;
  background: transparent;
  color: var(--app-text-secondary);
  border-radius: 12px;
  cursor: pointer;
  font-size: 14px;
  text-align: left;
  transition: background 0.15s, color 0.15s;
}
.nav-item:hover {
  background: var(--app-surface);
  color: var(--app-text-primary);
}
.nav-item.active {
  background: var(--app-accent-soft);
  color: var(--app-accent);
  font-weight: 600;
}
.sidebar-foot {
  padding: 12px 10px 4px;
  border-top: 1px solid var(--app-border);
  margin-top: 12px;
}
.ver {
  color: var(--app-text-muted);
  font-size: 11px;
}
</style>
