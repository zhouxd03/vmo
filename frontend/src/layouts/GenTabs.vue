<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon } from 'naive-ui'
import {
  MenuOutline, GridSharp, ImageOutline, VideocamOutline, GitNetworkOutline,
} from '@vicons/ionicons5'

const route = useRoute()
const router = useRouter()

const iconMap = {
  worktable: GridSharp,
  image: ImageOutline,
  video: VideocamOutline,
  pipeline: GitNetworkOutline,
}

const tabs = computed(() =>
  router.options.routes
    .filter((r) => r.meta?.gen)
    .map((r) => ({ name: r.name, path: r.path, title: r.meta.title, icon: iconMap[r.meta.icon] || GridSharp }))
)
</script>

<template>
  <div class="gentabs">
    <button class="back" title="返回主导航" @click="router.push('/')">
      <n-icon :component="MenuOutline" size="18" />
    </button>
    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.name"
        class="tab"
        :class="{ active: route.name === t.name }"
        @click="router.push(t.path)"
      >
        <n-icon :component="t.icon" size="15" />
        <span>{{ t.title }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.gentabs {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: color-mix(in srgb, var(--app-bg-soft) 86%, transparent);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--app-border);
}
.back {
  display: flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; flex-shrink: 0;
  border: 1px solid var(--app-border); border-radius: 9px;
  background: transparent; color: var(--app-text-secondary); cursor: pointer;
  transition: background .15s, color .15s;
}
.back:hover { background: var(--app-surface); color: var(--app-accent); }
.tabs { display: flex; align-items: center; gap: 4px; }
.tab {
  display: flex; align-items: center; gap: 6px;
  padding: 7px 14px; border-radius: 9px;
  border: 1px solid transparent; background: transparent;
  color: var(--app-text-secondary); cursor: pointer; font-size: 13px;
  transition: background .15s, color .15s;
}
.tab:hover { background: var(--app-surface); color: var(--app-text-primary); }
.tab.active {
  background: var(--app-accent-soft); color: var(--app-accent); font-weight: 600;
  border-color: color-mix(in srgb, var(--app-accent) 35%, transparent);
}
</style>
