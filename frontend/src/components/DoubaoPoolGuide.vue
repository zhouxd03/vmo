<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert, NButton, NCard, NEmpty, NForm, NFormItem, NIcon, NInput,
  NInputNumber, NPopconfirm, NSpace, NSwitch, NTag, NRadioGroup, NRadioButton, useMessage,
} from 'naive-ui'
import {
  AddOutline, CheckmarkCircleOutline, DownloadOutline, OpenOutline, RefreshOutline, TrashOutline,
  CloudUploadOutline, FolderOpenOutline, DocumentTextOutline,
} from '@vicons/ionicons5'
import { api } from '../api'

const message = useMessage()
const DEFAULT_DOUBAO_VIDEO_QUOTA = 5
const site = ref('cn')
const siteOptions = [
  { label: '国内版', value: 'cn' },
  { label: '国际版', value: 'intl' },
]

const loading = ref(false)
const opening = ref('')
const readyBusy = ref(false)
const readyInfo = ref(null)
const state = ref({ accounts: [], accountCount: 0, remainingTotal: 0, activeTotal: 0 })
const form = ref({ name: '', quota: DEFAULT_DOUBAO_VIDEO_QUOTA })
const transferForm = ref({ source: '', quota: DEFAULT_DOUBAO_VIDEO_QUOTA })
const importingAccounts = ref(false)
const exportingAccounts = ref(false)
const importResult = ref(null)

let refreshTimer = 0
let autoPrepareStarted = false

const accounts = computed(() => state.value.accounts || [])
const siteLabel = computed(() => (site.value === 'intl' ? 'Dola 国际版' : '豆包国内版'))
const pluginsInstalled = computed(() => Boolean(state.value.pluginsInstalled))
const pluginsSourceCurrent = computed(() => Boolean(state.value.pluginsSourceCurrent))
const nativeAbilityReady = computed(() => Boolean(pluginsInstalled.value && pluginsSourceCurrent.value))
const pluginConnected = computed(() => Boolean(state.value.pluginConnected))
const durationOptions = computed(() => {
  const raw = state.value.durationOptions || state.value.pluginInstall?.durationOptions || [5, 10, 15]
  const values = Array.from(new Set((raw || [])
    .map((item) => Number(item))
    .filter((item) => Number.isFinite(item) && item > 0)))
    .sort((a, b) => a - b)
  return values.length ? values : [5, 10, 15]
})
const usingPackagedBrowser = computed(() => Boolean(state.value.usePackagedBrowser))
const automationMode = computed(() => state.value.settings?.automationMode || 'full')
const semiAutoMode = computed(() => automationMode.value === 'semi')
const abilitySummary = computed(() => (
  semiAutoMode.value
    ? `${usingPackagedBrowser.value ? '本地化豆包浏览器内核' : '系统浏览器'}、只自动上传参考图和提示词，时长/比例/生成/下载由用户手动完成`
    : `${usingPackagedBrowser.value ? '本地化豆包浏览器内核' : '系统浏览器'}、${durationOptions.value.join('/')} 秒自适应档位、去水印抓取、本地下载回传`
))

const poolStatus = computed(() => {
  if (readyBusy.value || opening.value) {
    return { type: 'info', label: '启动中', hint: '正在启动豆包本地化浏览器内核并接入生产链路' }
  }
  if (!state.value.chromePath) {
    return { type: 'warning', label: '缺少浏览器', hint: '未找到豆包本地化浏览器内核，请检查软件包资源' }
  }
  if (!nativeAbilityReady.value) {
    return { type: 'warning', label: '待初始化', hint: '点击启用后会自动校验豆包生产能力资源' }
  }
  if (!accounts.value.length) {
    return { type: 'warning', label: '待录入账号', hint: '先录入豆包账号，VMO 会为每个账号使用独立浏览器环境' }
  }
  if (!Number(state.value.availableTotal ?? state.value.remainingTotal ?? 0)) {
    return { type: 'warning', label: '次数为 0', hint: '重置本地次数或录入仍有额度的账号' }
  }
  if (pluginConnected.value) {
    return semiAutoMode.value
      ? { type: 'success', label: '半自动', hint: '豆包页面已与 VMO 联动，只负责传图和填词' }
      : { type: 'success', label: '已接入', hint: '豆包页面已与 VMO 联动，视频生产会自动接管' }
  }
  return { type: 'info', label: '可启用', hint: '点击启用后即可登录或确认账号状态' }
})

function accountHealthType(account) {
  const status = account?.accountHealthStatus || 'ok'
  if (status === 'login-required') return 'error'
  if (status === 'exhausted') return 'error'
  if (status === 'cooldown' || status === 'suspect') return 'warning'
  if (account?.pluginConnected) return 'success'
  if (account?.lastKeepaliveStatus === 'recovering') return 'info'
  if (account?.lastKeepaliveStatus === 'stale') return 'warning'
  return 'default'
}

function accountHealthLabel(account) {
  const status = account?.accountHealthStatus || 'ok'
  if (status === 'login-required') return '需登录'
  if (status === 'exhausted') return '额度耗尽'
  if (status === 'cooldown') return '冷却中'
  if (status === 'suspect') return '待复核'
  if (account?.pluginConnected) return '在线'
  if (account?.lastKeepaliveStatus === 'recovering') return '保活中'
  if (account?.lastKeepaliveReason === 'doubao-tab-missing') return '页面待恢复'
  if (account?.lastKeepaliveReason === 'browser-debug-disconnected') return '浏览器待启动'
  return '未连接'
}

function applyState(nextState, credentialReady = state.value.credentialReady) {
  state.value = { ...(nextState || {}), credentialReady: Boolean(credentialReady) }
}

watch(site, () => {
  readyInfo.value = null
  importResult.value = null
  opening.value = ''
  reload({ silent: true })
})

async function reload({ silent = false } = {}) {
  if (!silent) loading.value = true
  try {
    const resp = await api.doubaoPool({ site: site.value })
    applyState(resp.state, resp.credentialReady)
    if (resp.ready) readyInfo.value = resp.ready
  } catch (e) {
    if (!silent) message.error('加载豆包视频凭证失败: ' + e.message)
  } finally {
    if (!silent) loading.value = false
  }
}

async function prepareNativeAbility({ silent = false, force = false } = {}) {
  try {
    const resp = await api.prepareDoubaoExtension({ force, site: site.value })
    if (resp.state) applyState(resp.state, true)
    const latest = await api.doubaoPool({ site: site.value })
    applyState(latest.state, latest.credentialReady)
    if (!silent && !resp.pluginsInstalled) {
      message.warning('豆包生产能力资源缺失，请检查软件包资源')
    }
  } catch (e) {
    if (!silent) message.error('准备豆包生产能力失败: ' + e.message)
  }
}

async function startPool({ accountId = '', silent = false, forceRestart = false } = {}) {
  if (readyBusy.value) return readyInfo.value
  opening.value = accountId || '__pool__'
  readyBusy.value = true
  try {
    const resp = await api.readyDoubaoPool({
      accountId,
      site: site.value,
      openBrowser: true,
      requireCapacity: false,
      forceRestart,
    })
    readyInfo.value = resp
    if (resp.state) applyState(resp.state, true)
    if (!silent) {
      if (resp.ready || resp.opened || resp.openDebounced) {
        message.success(resp.openDebounced ? '豆包账号环境正在启动，请稍等' : '豆包视频凭证已启用')
      } else {
        message.warning((resp.issues || [])[0] || '豆包视频凭证尚未就绪')
      }
    }
    return resp
  } catch (e) {
    if (!silent) message.error('启用豆包视频凭证失败: ' + e.message)
    return null
  } finally {
    opening.value = ''
    readyBusy.value = false
  }
}

async function addAccount() {
  try {
    const resp = await api.addDoubaoAccount({
      name: form.value.name || undefined,
      quota: form.value.quota || DEFAULT_DOUBAO_VIDEO_QUOTA,
      site: site.value,
    })
    applyState(resp.state, true)
    form.value = { name: '', quota: DEFAULT_DOUBAO_VIDEO_QUOTA }
    message.success('已新增账号，正在打开登录窗口')
    await startPool({ silent: true })
  } catch (e) {
    message.error('新增账号失败: ' + e.message)
  }
}

async function importAccounts() {
  const source = (transferForm.value.source || '').trim()
  if (!source) {
    message.warning('请填写要导入的目录或文件路径')
    return
  }
  importingAccounts.value = true
  importResult.value = null
  try {
    const resp = await api.importDoubaoAccounts({
      source,
      quota: transferForm.value.quota || DEFAULT_DOUBAO_VIDEO_QUOTA,
      includeProfiles: true,
      site: site.value,
    })
    if (resp.state) applyState(resp.state, true)
    importResult.value = resp
    if (resp.imported) {
      message.success(`已导入 ${resp.imported} 个账号环境`)
    } else {
      message.warning((resp.skipped || [])[0] || '未识别到可导入的账号环境')
    }
  } catch (e) {
    message.error('导入账号环境失败: ' + e.message)
  } finally {
    importingAccounts.value = false
  }
}

async function chooseImportPath(mode = 'file') {
  const nativeApi = window.pywebview && window.pywebview.api
  if (!nativeApi?.choose_path) {
    message.warning('当前窗口不支持打开文件管理器，请在桌面版中使用选择按钮')
    return
  }
  try {
    const resp = await nativeApi.choose_path(mode)
    if (!resp?.ok) {
      message.error('打开文件管理器失败: ' + (resp?.error || '未知错误'))
      return
    }
    if (resp.path) {
      transferForm.value.source = resp.path
    }
  } catch (e) {
    message.error('打开文件管理器失败: ' + e.message)
  }
}

async function exportAccounts() {
  exportingAccounts.value = true
  try {
    await api.exportDoubaoAccounts({ site: site.value })
    message.success('已导出账号配置')
  } catch (e) {
    message.error('导出账号配置失败: ' + e.message)
  } finally {
    exportingAccounts.value = false
  }
}

async function updateAccount(id, patch) {
  try {
    const resp = await api.updateDoubaoAccount(id, { ...patch, site: site.value })
    applyState(resp.state, state.value.credentialReady)
  } catch (e) {
    message.error('更新账号失败: ' + e.message)
  }
}

async function resetAccountUsage(id = '') {
  try {
    const resp = id
      ? await api.resetDoubaoAccountUsage(id, { used: 0, clearRemote: true, site: site.value })
      : await api.resetAllDoubaoAccountUsage({ used: 0, clearRemote: true, site: site.value })
    applyState(resp.state, state.value.credentialReady)
    message.success(id ? '已重置此账号本地次数' : '已重置全部账号本地次数')
  } catch (e) {
    message.error('重置次数失败: ' + e.message)
  }
}

async function removeAccount(id) {
  try {
    const resp = await api.deleteDoubaoAccount(id, { site: site.value })
    applyState(resp.state, state.value.credentialReady)
    message.success('已移除账号')
  } catch (e) {
    message.error('移除失败: ' + e.message)
  }
}

async function updateAutomationMode(value) {
  const nextMode = value ? 'semi' : 'full'
  const prevState = state.value
  state.value = {
    ...state.value,
    settings: { ...(state.value.settings || {}), automationMode: nextMode },
  }
  try {
    const resp = await api.updateDoubaoPoolSettings({ automationMode: nextMode, site: site.value })
    if (resp.state) applyState(resp.state, state.value.credentialReady)
    message.success(nextMode === 'semi' ? '已切换为半自动模式' : '已切换为全自动模式')
  } catch (e) {
    state.value = prevState
    message.error('切换豆包自动化模式失败: ' + e.message)
  }
}

onMounted(() => {
  reload().then(() => {
    if (!autoPrepareStarted && !nativeAbilityReady.value) {
      autoPrepareStarted = true
      prepareNativeAbility({ silent: true })
    }
  })
  refreshTimer = window.setInterval(() => {
    reload({ silent: true })
  }, 5000)
})

onBeforeUnmount(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<template>
  <div class="doubao-guide">
    <n-card class="panel" :bordered="false">
      <div class="top">
        <div>
          <div class="title">豆包视频凭证</div>
          <div class="sub">使用 VMO 本地化浏览器内核作为专用运行环境，生产时自动接管网页流程。</div>
        </div>
        <n-space>
          <n-radio-group v-model:value="site" size="small">
            <n-radio-button
              v-for="item in siteOptions"
              :key="item.value"
              :value="item.value"
              :label="item.label"
            />
          </n-radio-group>
          <n-tag :type="poolStatus.type" :bordered="false">
            <template #icon><n-icon :component="CheckmarkCircleOutline" /></template>
            {{ poolStatus.label }}
          </n-tag>
          <n-button type="primary" :loading="opening === '__pool__' || readyBusy" @click="startPool()">
            <template #icon><n-icon :component="OpenOutline" /></template>
            启用凭证
          </n-button>
          <n-button :loading="loading" @click="reload()">
            <template #icon><n-icon :component="RefreshOutline" /></template>
            刷新
          </n-button>
        </n-space>
      </div>

      <div class="stats">
        <div><span>账号</span><b>{{ state.accountCount || 0 }}</b></div>
        <div><span>可分配</span><b>{{ state.availableTotal ?? state.remainingTotal ?? 0 }}</b></div>
        <div><span>进行中</span><b>{{ state.activeTotal || 0 }}</b></div>
      </div>

      <div class="mode-row">
        <div>
          <div class="mode-title">生产模式</div>
          <div class="sub">
            {{ semiAutoMode ? '只传图和填词；时长、比例、生成、下载全部手动处理。' : '自动设置参数、提交生成、轮询并下载回传。' }}
          </div>
        </div>
        <div class="mode-switch">
          <span :class="{ on: !semiAutoMode }">全自动</span>
          <n-switch :value="semiAutoMode" @update:value="updateAutomationMode" />
          <span :class="{ on: semiAutoMode }">半自动</span>
        </div>
      </div>

      <n-alert :type="poolStatus.type" :bordered="false" class="guide">
        {{ poolStatus.hint }}。{{ abilitySummary }}。
      </n-alert>

      <n-alert v-if="readyInfo && (readyInfo.issues || []).length" type="warning" :bordered="false" class="diagnose">
        <div v-for="issue in readyInfo.issues || []" :key="issue">{{ issue }}</div>
      </n-alert>
    </n-card>

    <n-card class="panel" :bordered="false">
      <div class="section-head">
        <div>
          <div class="section-title">录入账号</div>
          <div class="sub">默认 5 个视频位；用完后自动切换到下一个有额度账号。</div>
        </div>
        <n-button @click="resetAccountUsage()">
          <template #icon><n-icon :component="RefreshOutline" /></template>
          重置全部次数
        </n-button>
      </div>

      <n-form class="add-row" :show-feedback="false">
        <n-form-item label="账号名">
          <n-input v-model:value="form.name" placeholder="例如：豆包主号" clearable />
        </n-form-item>
        <n-form-item label="视频数">
          <n-input-number v-model:value="form.quota" :min="1" :max="9999" />
        </n-form-item>
        <n-button type="primary" @click="addAccount">
          <template #icon><n-icon :component="AddOutline" /></template>
          新增账号
        </n-button>
      </n-form>

      <div class="transfer-row">
        <div class="transfer-main">
          <div class="section-title">导入 / 导出</div>
          <div class="sub">填写目录或配置包路径后自动检索账号环境；导出仅生成本软件可识别的配置包。</div>
          <div class="transfer-form">
            <n-input
              v-model:value="transferForm.source"
              placeholder="粘贴目录、配置包或可执行文件所在路径"
              clearable
            />
            <div class="transfer-pickers">
              <n-button size="small" @click="chooseImportPath('folder')">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                选目录
              </n-button>
              <n-button size="small" @click="chooseImportPath('file')">
                <template #icon><n-icon :component="DocumentTextOutline" /></template>
                选文件
              </n-button>
            </div>
            <n-input-number
              v-model:value="transferForm.quota"
              :min="1"
              :max="9999"
              size="small"
            />
          </div>
          <n-alert v-if="importResult" class="transfer-result" :type="importResult.imported ? 'success' : 'warning'" :bordered="false">
            已导入 {{ importResult.imported || 0 }} 个账号环境，复制 {{ importResult.copiedProfiles || 0 }} 个登录环境。
            <span v-if="(importResult.skipped || []).length"> {{ (importResult.skipped || []).join('；') }}</span>
          </n-alert>
        </div>
        <div class="transfer-actions">
          <n-button :loading="importingAccounts" @click="importAccounts">
            <template #icon><n-icon :component="CloudUploadOutline" /></template>
            导入
          </n-button>
          <n-button :loading="exportingAccounts" @click="exportAccounts">
            <template #icon><n-icon :component="DownloadOutline" /></template>
            导出
          </n-button>
        </div>
      </div>

      <n-empty v-if="!accounts.length" description="还没有豆包账号" class="empty" />

      <div v-else class="accounts">
        <div v-for="account in accounts" :key="account.id" class="account" :class="{ off: !account.enabled }">
          <div class="account-main">
            <div class="account-line">
              <n-input
                class="name"
                :value="account.name"
                placeholder="账号名"
                @update:value="(value) => updateAccount(account.id, { name: value })"
              />
              <n-tag :type="account.enabled ? 'success' : 'default'" :bordered="false">
                {{ account.enabled ? '启用' : '停用' }}
              </n-tag>
              <n-tag :type="accountHealthType(account)" :bordered="false">
                {{ accountHealthLabel(account) }}
              </n-tag>
            </div>
            <div class="account-meta">
              <span>剩余 {{ account.remaining }} / {{ account.quota }}</span>
              <span>可分配 {{ account.available ?? account.remaining }}</span>
              <span v-if="account.active">进行中 {{ account.active }}</span>
              <span v-if="account.remoteCreditRemaining !== undefined">远端剩余 {{ account.remoteCreditRemaining }} 积分</span>
              <span v-if="account.accountIssueReason">状态：{{ account.accountIssueReason }}</span>
              <span>下载 {{ account.videoCount || 0 }} 个</span>
            </div>
          </div>
          <div class="account-actions">
            <span class="quota-label">视频数</span>
            <n-input-number
              :value="account.quota"
              :min="1"
              :max="9999"
              size="small"
              @update:value="(value) => updateAccount(account.id, { quota: Number(value || 1) })"
            />
            <n-switch
              :value="account.enabled"
              @update:value="(value) => updateAccount(account.id, { enabled: value })"
            />
            <n-button size="small" type="primary" :loading="opening === account.id" @click="startPool({ accountId: account.id })">
              <template #icon><n-icon :component="OpenOutline" /></template>
              打开登录
            </n-button>
            <n-button size="small" @click="resetAccountUsage(account.id)">
              <template #icon><n-icon :component="RefreshOutline" /></template>
              重置
            </n-button>
            <n-popconfirm @positive-click="removeAccount(account.id)">
              <template #trigger>
                <n-button size="small" type="error" ghost>
                  <template #icon><n-icon :component="TrashOutline" /></template>
                </n-button>
              </template>
              确认移除此账号？
            </n-popconfirm>
          </div>
        </div>
      </div>
    </n-card>
  </div>
</template>

<style scoped>
.doubao-guide {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.panel {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border);
  border-radius: var(--r-card);
}
.top,
.section-head,
.account,
.account-line,
.account-actions,
.add-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.top,
.section-head,
.account {
  justify-content: space-between;
}
.title {
  font-size: 18px;
  font-weight: 700;
}
.top :deep(.n-radio-group) {
  flex: 0 0 auto;
}
.section-title {
  font-size: 15px;
  font-weight: 700;
}
.sub,
.account-meta {
  color: var(--app-text-muted);
  font-size: 12px;
  margin-top: 4px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0;
}
.stats > div {
  padding: 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-bg-soft);
}
.stats span {
  display: block;
  color: var(--app-text-muted);
  font-size: 12px;
}
.stats b {
  display: block;
  margin-top: 4px;
}
.mode-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 14px;
  padding: 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-bg-soft);
}
.mode-title {
  font-size: 13px;
  font-weight: 700;
}
.mode-switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--app-text-muted);
  font-size: 12px;
  white-space: nowrap;
}
.mode-switch .on {
  color: var(--app-accent);
  font-weight: 700;
}
.guide {
  line-height: 1.6;
}
.diagnose {
  margin-top: 12px;
}
.add-row {
  margin: 14px 0;
}
.add-row :deep(.n-form-item) {
  margin-bottom: 0;
}
.transfer-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 14px;
  padding: 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-bg-soft);
}
.transfer-main {
  min-width: 0;
  flex: 1;
}
.transfer-form {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto 110px;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.transfer-pickers {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.transfer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.transfer-result {
  margin-top: 10px;
}
.empty {
  padding: 24px 0;
}
.accounts {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.account {
  padding: 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-bg-soft);
}
.account.off {
  opacity: 0.6;
}
.account-main {
  min-width: 0;
  flex: 1;
}
.account-line {
  justify-content: flex-start;
}
.name {
  max-width: 220px;
}
.account-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
}
.account-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}
.quota-label {
  color: var(--app-text-muted);
  font-size: 12px;
}

@media (max-width: 860px) {
  .top,
  .section-head,
  .account {
    align-items: flex-start;
    flex-direction: column;
  }
  .stats {
    grid-template-columns: 1fr;
  }
  .mode-row {
    align-items: flex-start;
    flex-direction: column;
  }
  .transfer-row,
  .add-row,
  .account-actions {
    align-items: stretch;
    flex-direction: column;
  }
  .transfer-form {
    grid-template-columns: 1fr;
  }
  .transfer-pickers {
    flex-wrap: wrap;
  }
  .transfer-actions {
    justify-content: flex-start;
  }
  .name {
    max-width: 100%;
  }
}
</style>
