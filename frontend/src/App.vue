<script setup>
import { computed, onErrorCaptured, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider, NLoadingBarProvider } from 'naive-ui'
import { zhCN, dateZhCN } from 'naive-ui'
import { darkTheme, lightTheme, buildThemeOverrides } from './theme'
import { useThemeStore } from './stores/theme'
import TitleBar from './layouts/TitleBar.vue'
import SideBar from './layouts/SideBar.vue'
import GenTabs from './layouts/GenTabs.vue'
import ResizeHandles from './layouts/ResizeHandles.vue'

const route = useRoute()
const fullBleed = computed(() => !!route.meta.fullBleed)

const themeStore = useThemeStore()
const naiveTheme = computed(() => (themeStore.mode === 'light' ? lightTheme : darkTheme))
const naiveOverrides = computed(() => buildThemeOverrides(themeStore.theme))
const viewError = ref('')
onErrorCaptured((err) => {
  viewError.value = err?.message || String(err || '页面加载失败')
  console.error(err)
  return false
})
onMounted(() => themeStore.init())
</script>

<template>
  <n-config-provider :theme="naiveTheme" :theme-overrides="naiveOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-loading-bar-provider>
      <n-message-provider>
        <n-dialog-provider>
          <div class="app-glow" />
          <ResizeHandles />
          <div class="app-root">
            <TitleBar />
            <div class="app-body">
              <SideBar v-if="!fullBleed" />
              <main class="app-main" :class="{ bleed: fullBleed }">
                <GenTabs v-if="fullBleed" />
                <div v-if="viewError" class="view-error">
                  <strong>页面加载失败</strong>
                  <span>{{ viewError }}</span>
                </div>
                <router-view v-slot="{ Component }">
                  <component :is="Component" v-if="!viewError" />
                </router-view>
              </main>
            </div>
          </div>
        </n-dialog-provider>
      </n-message-provider>
    </n-loading-bar-provider>
  </n-config-provider>
</template>

<style scoped>
.app-root {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.app-body {
  flex: 1;
  display: flex;
  min-height: 0;
}
.app-main {
  flex: 1;
  min-width: 0;
  overflow: auto;
  padding: 24px 28px;
}
.app-main.bleed {
  padding: 0;
}
.app-main.bleed > :not(.gentabs) {
  padding: 18px 24px 0;
}
.view-error {
  margin: 18px 24px;
  border: 1px solid rgba(255, 92, 92, .35);
  border-radius: 8px;
  background: rgba(80, 20, 24, .35);
  color: #ffd8d8;
  display: grid;
  gap: 8px;
  padding: 16px;
}
.view-error span {
  color: rgba(255, 216, 216, .82);
  word-break: break-word;
}
</style>
