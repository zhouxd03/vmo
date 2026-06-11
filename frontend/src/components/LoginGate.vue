<script setup>
import { computed, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { api } from '../api'

const emit = defineEmits(['login'])

const mode = ref('login')
const busy = ref(false)
const form = reactive({
  email: '',
  password: '',
  invite_code: '',
  nickname: '',
  remember_days: 7,
})
const message = useMessage()

const rememberOptions = [
  { label: '1 天刷新一次', value: 1 },
  { label: '7 天刷新一次', value: 7 },
  { label: '15 天刷新一次', value: 15 },
  { label: '30 天刷新一次', value: 30 },
]

const isRegister = computed(() => mode.value === 'register')
const submitText = computed(() => (isRegister.value ? '申请并登录' : '登录'))

function switchMode(next) {
  mode.value = next
}

async function submit() {
  if (busy.value) return
  busy.value = true
  try {
    const body = {
      email: form.email.trim(),
      password: form.password,
      invite_code: form.invite_code.trim(),
      nickname: form.nickname.trim(),
      remember_days: form.remember_days,
    }
    const res = isRegister.value ? await api.authRegister(body) : await api.authLogin(body)
    if (!res?.ok) throw new Error(res?.error || '认证失败')
    message.success(isRegister.value ? '账号已创建，欢迎使用 vmo studio' : '登录成功')
    emit('login', res.user)
  } catch (e) {
    message.error(e?.message || String(e))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="login-screen">
    <div class="login-panel">
      <div class="brand-block">
        <div class="brand-mark">v</div>
        <div>
          <h1>vmo studio</h1>
          <p>登录后进入本地创作工作台</p>
        </div>
      </div>

      <div class="mode-tabs">
        <button :class="{ active: mode === 'login' }" @click="switchMode('login')">登录</button>
        <button :class="{ active: mode === 'register' }" @click="switchMode('register')">账号申请</button>
      </div>

      <n-form class="auth-form" @submit.prevent="submit">
        <n-form-item label="邮箱">
          <n-input v-model:value="form.email" placeholder="name@example.com" autocomplete="email" />
        </n-form-item>
        <n-form-item label="密码">
          <n-input
            v-model:value="form.password"
            type="password"
            show-password-on="click"
            placeholder="至少 8 位"
            autocomplete="current-password"
          />
        </n-form-item>
        <n-form-item v-if="isRegister" label="邀请码">
          <n-input v-model:value="form.invite_code" placeholder="请输入管理员提供的邀请码" />
        </n-form-item>
        <n-form-item v-if="isRegister" label="昵称">
          <n-input v-model:value="form.nickname" placeholder="可选" />
        </n-form-item>
        <n-form-item label="记住登录">
          <n-select v-model:value="form.remember_days" :options="rememberOptions" />
        </n-form-item>
        <n-button type="primary" size="large" block :loading="busy" @click="submit">
          {{ submitText }}
        </n-button>
      </n-form>
    </div>
  </section>
</template>

<style scoped>
.login-screen {
  flex: 1;
  min-height: 0;
  display: grid;
  place-items: center;
  padding: 32px;
  background:
    radial-gradient(circle at 22% 18%, color-mix(in srgb, var(--app-accent) 16%, transparent), transparent 34%),
    linear-gradient(135deg, color-mix(in srgb, var(--app-bg-soft) 94%, #111), var(--app-bg));
}
.login-panel {
  width: min(420px, 100%);
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-surface) 92%, transparent);
  box-shadow: 0 24px 70px rgba(0, 0, 0, .24);
  padding: 28px;
}
.brand-block {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 22px;
}
.brand-mark {
  width: 46px;
  height: 46px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: var(--app-accent);
  color: #07120d;
  font-size: 25px;
  font-weight: 900;
  text-transform: uppercase;
}
h1 {
  margin: 0;
  font-size: 25px;
  line-height: 1.1;
  letter-spacing: 0;
}
p {
  margin: 6px 0 0;
  color: var(--app-text-muted);
  font-size: 13px;
}
.mode-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  padding: 4px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-surface-2);
  margin-bottom: 20px;
}
.mode-tabs button {
  height: 34px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--app-text-muted);
  cursor: pointer;
  font-weight: 700;
}
.mode-tabs button.active {
  background: var(--app-surface);
  color: var(--app-text-primary);
  box-shadow: 0 1px 0 rgba(255, 255, 255, .06) inset;
}
.auth-form {
  display: block;
}
</style>
