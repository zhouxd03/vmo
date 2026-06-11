<script setup>
import { computed, onErrorCaptured, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider, NLoadingBarProvider } from 'naive-ui'
import { zhCN, dateZhCN } from 'naive-ui'
import { darkTheme, lightTheme, buildThemeOverrides } from './theme'
import { useThemeStore } from './stores/theme'
import { useHealthStore } from './stores/health'
import { api } from './api'
import TitleBar from './layouts/TitleBar.vue'
import SideBar from './layouts/SideBar.vue'
import GenTabs from './layouts/GenTabs.vue'
import ResizeHandles from './layouts/ResizeHandles.vue'
import LoginGate from './components/LoginGate.vue'

const route = useRoute()
const fullBleed = computed(() => !!route.meta.fullBleed)

const themeStore = useThemeStore()
const healthStore = useHealthStore()
const naiveTheme = computed(() => (themeStore.mode === 'light' ? lightTheme : darkTheme))
const naiveOverrides = computed(() => buildThemeOverrides(themeStore.theme))
const viewError = ref('')
const authLoading = ref(true)
const currentUser = ref(null)
const urlParams = new URLSearchParams(window.location.search)
const LOCAL_DESKTOP = ['127.0.0.1', 'localhost'].includes(window.location.hostname)
const DEV_AUTH_BYPASS = import.meta.env.DEV || import.meta.env.VITE_DEV_AUTH_BYPASS === '1' || LOCAL_DESKTOP || urlParams.get('local_auth') === '1'
const devUser = { id: 'dev-local', email: 'dev@local', nickname: '\u672c\u5730\u5f00\u53d1' }

onErrorCaptured((err) => {
  viewError.value = err?.message || String(err || '页面加载失败')
  console.error(err)
  return false
})

async function bootstrapAuth() {
  authLoading.value = true
  if (DEV_AUTH_BYPASS) {
    currentUser.value = devUser
    healthStore.start()
    authLoading.value = false
    return
  }
  try {
    const res = await api.authMe()
    if (res?.ok) {
      currentUser.value = res.user
      healthStore.start()
    }
  } catch {
    currentUser.value = null
  } finally {
    authLoading.value = false
  }
}

function onLogin(user) {
  currentUser.value = user
  healthStore.start()
}

async function logout() {
  if (DEV_AUTH_BYPASS) {
    currentUser.value = devUser
    return
  }
  await api.authLogout()
  healthStore.stop()
  currentUser.value = null
}

onMounted(() => {
  themeStore.init()
  bootstrapAuth()
})
</script>

<template>
  <n-config-provider :theme="naiveTheme" :theme-overrides="naiveOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-loading-bar-provider>
      <n-message-provider>
        <n-dialog-provider>
          <div class="app-glow" />
          <ResizeHandles />
          <div class="app-root">
            <TitleBar :user="currentUser" :authenticated="!!currentUser" @logout="logout" />
            <div v-if="authLoading" class="auth-loading">
              <div class="loading-card">
                <strong>vmo studio</strong>
                <span>正在检查登录状态...</span>
              </div>
            </div>
            <LoginGate v-else-if="!currentUser" @login="onLogin" />
            <template v-else>
              <div v-if="healthStore.showBanner" class="health-banner" :class="healthStore.status">
                <div>
                  <strong>{{ healthStore.label }}</strong>
                  <span>{{ healthStore.detail }}</span>
                </div>
                <button :disabled="healthStore.checking" @click="healthStore.check()">
                  {{ healthStore.checking ? '检查中' : '重新连接' }}
                </button>
              </div>
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
            </template>
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
.auth-loading {
  flex: 1;
  display: grid;
  place-items: center;
  color: var(--app-text-primary);
}
.loading-card {
  display: grid;
  gap: 8px;
  min-width: 260px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-surface);
  padding: 20px;
  text-align: center;
}
.loading-card span {
  color: var(--app-text-muted);
  font-size: 13px;
}
.health-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid color-mix(in srgb, #ff5d6c 34%, var(--app-border));
  background: color-mix(in srgb, #ff5d6c 12%, var(--app-surface));
  color: var(--app-text-primary);
  font-size: 12px;
}
.health-banner div {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.health-banner span {
  color: var(--app-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.health-banner button {
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-surface-2);
  color: var(--app-text-primary);
  cursor: pointer;
  padding: 4px 10px;
}
.health-banner button:disabled {
  cursor: wait;
  opacity: .65;
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
