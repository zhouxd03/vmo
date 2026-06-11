<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import {
  NCard, NButton, NTag, NIcon, NModal, NForm, NFormItem, NInput, NSwitch,
  NSelect, NEmpty, NSpace, NPopconfirm, useMessage,
} from 'naive-ui'
import {
  AddOutline, CreateOutline, TrashOutline, FlashOutline, StarOutline, Star,
} from '@vicons/ionicons5'
import { api } from '../api'

const message = useMessage()
const HUAJING_BASE_URL = 'https://aibac.lizer.cc'
const DOUBAO_POOL_BASE_URL = 'local://doubao-pool'
const DOUBAO_POOL_INTL_BASE_URL = 'local://doubao-pool/intl'

const CATEGORIES = [
  { key: 'image', label: '生图 API', desc: '文生图 / 垫图（OpenAI 兼容 images 接口）', needsModel: true },
  { key: 'video', label: '生视频 API', desc: '文生 / 图生视频（地址+Key 手动填写，不写死）', needsModel: true },
  { key: 'llm', label: 'LLM 中转站', desc: '剧本分析 / 多模态复核（可拉取模型列表）', needsModel: true, fetchModels: true },
  { key: 'image_host', label: '图床（可选）', desc: '图生视频的公网图床；留空则用内置 catbox/0x0 兜底', needsModel: false },
]

const videoProviderOptions = [
  { label: 'VMO 国际版豆包号池', value: 'doubao_pool_intl' },
  { label: 'Vidu (fixed web API, Cookie)', value: 'vidu' },
  { label: 'Huajing AI (aibac.lizer.cc)', value: 'huajing' },
  { label: 'VMO 本地豆包号池', value: 'doubao_pool' },
  { label: 'OpenAI 兼容 (/v1/videos)', value: '' },
  { label: 'Seedance · doubao-seedance (multipart + token)', value: 'seedance' },
  { label: 'Seedance 网页端 (/user/Data + DataIndex)', value: 'seedance_web' },
]
const imageProviderOptions = [
  { label: 'OpenAI compatible (/v1/images)', value: '' },
  { label: 'Vidu (fixed web API, Cookie)', value: 'vidu' },
]

const SEEDANCE_MODELS = [
  'doubao-seedance-2-0-260128',
  'doubao-seedance-2-0-fast-260128',
  'doubao-seedance-2-0-260128-1',
  'doubao-seedance-2-0-260128-2',
  'doubao-seedance-2-0-260128-3',
].map((v) => ({ label: v, value: v }))
const VIDU_IMAGE_MODELS = [
  { label: '全能 Image', value: 'auto:3.2_image_2' },
  { label: '全能 Image - 文生图', value: 'text2image:3.2_image_2' },
  { label: '全能 Image - 参考生图', value: 'reference2image:3.2_image_2' },
]
const VIDU_VIDEO_MODELS = [
  { label: 'VIDU Q3', value: '3.2' },
  { label: 'Vidu 3.1', value: '3.1' },
]
const HUAJING_VIDEO_MODELS = [
  { label: '画镜SD2.0-企业满血', value: 'doubao-seedance-2-0-260128' },
  { label: '画镜SD2.0-fast-企业满血', value: 'doubao-seedance-2-0-fast-260128' },
  { label: '海外seedance 满血', value: 'video-pro' },
  { label: '海外seedance-fast 满血', value: 'video-fast' },
  { label: 'seedance 2.0 (官)', value: 'seedance_2' },
  { label: 'grok-imagine-video-1.5', value: 'grok-imagine-video-1.5' },
  { label: 'veo-3.1', value: 'veo-3.1' },
  { label: 'HappyHorse', value: 'HappyHorse' },
  { label: 'Vidu Q3 Pro', value: 'Vidu Q3 Pro' },
  { label: 'kling-3-omni', value: 'kling-3-omni' },
]

const DOUBAO_POOL_MODELS = [
  { label: '豆包网页视频 - 自动', value: 'doubao-web-video' },
]

const DOUBAO_POOL_INTL_MODELS = [
  { label: '国际版豆包网页视频 - 自动', value: 'dola-web-video' },
]

const data = ref({ image: [], video: [], llm: [], image_host: [] })
const loading = ref(false)

const showModal = ref(false)
const editing = ref(null) // { category, entry }
const form = ref({})
const saving = ref(false)
const testing = ref(false)
const userInfoLoading = ref(false)
const userInfo = ref(null)
const modelLoading = ref(false)
const modelOptions = ref([])

async function reload() {
  loading.value = true
  try {
    data.value = await api.listCredentials()
  } catch (e) {
    message.error('加载凭据失败: ' + e.message)
  } finally {
    loading.value = false
  }
}
onMounted(reload)

function openAdd(category) {
  editing.value = { category, isNew: true }
  form.value = { alias: '', base_url: '', api_key: '', model: '', provider: '', enabled: true, is_default: false, note: '' }
  modelOptions.value = []
  userInfo.value = null
  showModal.value = true
}

function openEdit(category, entry) {
  editing.value = { category, isNew: false }
  form.value = {
    id: entry.id,
    alias: entry.alias,
    base_url: entry.base_url,
    api_key: entry.api_key_masked, // masked; only overwritten if user types new
    model: entry.model,
    provider: entry.provider || '',
    enabled: entry.enabled,
    is_default: entry.is_default,
    note: entry.note,
  }
  modelOptions.value = entry.model ? [{ label: entry.model, value: entry.model }] : []
  userInfo.value = null
  showModal.value = true
}

const currentCat = computed(() => CATEGORIES.find((c) => c.key === editing.value?.category) || {})
const isSeedanceProvider = computed(() =>
  form.value.provider === 'seedance' || form.value.provider === 'seedance_web')
const isViduProvider = computed(() => form.value.provider === 'vidu')
const isHuajingProvider = computed(() => form.value.provider === 'huajing')
const isDoubaoPoolProvider = computed(() => form.value.provider === 'doubao_pool' || form.value.provider === 'doubao_pool_intl')
const needsBaseUrl = computed(() =>
  editing.value?.category !== 'image_host' && !isViduProvider.value && !isHuajingProvider.value && !isDoubaoPoolProvider.value)
const effectiveModelOptions = computed(() =>
  isViduProvider.value
    ? (editing.value?.category === 'image' ? VIDU_IMAGE_MODELS : VIDU_VIDEO_MODELS)
    : form.value.provider === 'doubao_pool_intl' ? DOUBAO_POOL_INTL_MODELS
    : isDoubaoPoolProvider.value ? DOUBAO_POOL_MODELS
    : isHuajingProvider.value ? HUAJING_VIDEO_MODELS
    : isSeedanceProvider.value ? SEEDANCE_MODELS : modelOptions.value)

watch(isHuajingProvider, (enabled) => {
  if (enabled) {
    form.value.base_url = HUAJING_BASE_URL
    if (!form.value.alias) form.value.alias = 'Huajing AI'
    if (!form.value.model) form.value.model = 'doubao-seedance-2-0-fast-260128'
  }
})

watch(isDoubaoPoolProvider, (enabled) => {
  if (enabled) {
    const intl = form.value.provider === 'doubao_pool_intl'
    form.value.base_url = intl ? DOUBAO_POOL_INTL_BASE_URL : DOUBAO_POOL_BASE_URL
    form.value.api_key = ''
    if (!form.value.alias) form.value.alias = 'VMO 本地豆包号池'
    if (intl) form.value.alias = form.value.alias || 'VMO 国际版豆包号池'
    if (intl && !form.value.model) form.value.model = 'dola-web-video'
    if (!form.value.model) form.value.model = 'doubao-web-video'
  }
})

watch(() => form.value.provider, (provider) => {
  if (provider === 'doubao_pool_intl') {
    form.value.base_url = DOUBAO_POOL_INTL_BASE_URL
    form.value.api_key = ''
    form.value.model = 'dola-web-video'
    if (!form.value.alias || form.value.alias === 'VMO 本地豆包号池') form.value.alias = 'VMO 国际版豆包号池'
  } else if (provider === 'doubao_pool') {
    form.value.base_url = DOUBAO_POOL_BASE_URL
    form.value.api_key = ''
    if (!form.value.model || form.value.model === 'dola-web-video') form.value.model = 'doubao-web-video'
  }
})

function credentialPayload() {
  const payload = { ...form.value }
  if (payload.provider === 'huajing') {
    payload.base_url = HUAJING_BASE_URL
  }
  if (payload.provider === 'doubao_pool' || payload.provider === 'doubao_pool_intl') {
    const intl = payload.provider === 'doubao_pool_intl'
    payload.base_url = intl ? DOUBAO_POOL_INTL_BASE_URL : DOUBAO_POOL_BASE_URL
    payload.api_key = ''
    payload.model = payload.model || (intl ? 'dola-web-video' : 'doubao-web-video')
  }
  return payload
}

function normalizeModels(models) {
  return (models || []).map((m) => {
    if (typeof m === 'string') return { label: m, value: m }
    return { label: m.label || m.value || String(m), value: m.value || m.label || String(m) }
  })
}

async function fetchModels() {
  modelLoading.value = true
  try {
    const resp = await api.credentialModels(editing.value.category, {
      id: form.value.id,
      base_url: credentialPayload().base_url,
      api_key: form.value.api_key,
      provider: form.value.provider,
    })
    modelOptions.value = normalizeModels(resp.models)
    message.success(`${resp.builtin ? '已载入内置候选' : '拉取到'} ${modelOptions.value.length} 个模型`)
  } catch (e) {
    message.error('获取模型失败: ' + e.message)
  } finally {
    modelLoading.value = false
  }
}

async function testConn() {
  testing.value = true
  try {
    const resp = await api.testCredential(editing.value.category, {
      base_url: credentialPayload().base_url,
      api_key: form.value.api_key,
      provider: form.value.provider,
    })
    if (resp.ok) {
      let extra = ''
      if (resp.info) extra = `，余额 满血:${resp.info.Token ?? '-'} / 快速:${resp.info.FastToken ?? '-'} / 国际版:${resp.info.SdDuration ?? '-'}s`
      else if (resp.models?.length) extra = `，发现 ${resp.models.length} 个模型`
      message.success('连接成功' + extra)
      if (resp.models?.length) modelOptions.value = normalizeModels(resp.models)
    } else {
      message.error('连接失败: ' + (resp.error || '未知错误'))
    }
  } catch (e) {
    message.error('连接失败: ' + e.message)
  } finally {
    testing.value = false
  }
}

async function queryUserInfo() {
  userInfoLoading.value = true
  try {
    const resp = await api.credentialUserInfo(editing.value.category, {
      id: form.value.id,
      base_url: credentialPayload().base_url,
      api_key: form.value.api_key,
      provider: form.value.provider,
    })
    if (resp.ok) {
      userInfo.value = resp.info || {}
      message.success('用户首页信息已更新')
    } else {
      message.error('查询失败: ' + (resp.error || '未知错误'))
    }
  } catch (e) {
    message.error('查询失败: ' + e.message)
  } finally {
    userInfoLoading.value = false
  }
}

async function save() {
  if (!form.value.base_url && needsBaseUrl.value) {
    message.warning('请填写 API 地址')
    return
  }
  saving.value = true
  try {
    await api.upsertCredential(editing.value.category, credentialPayload())
    message.success('已保存')
    showModal.value = false
    await reload()
  } catch (e) {
    message.error('保存失败: ' + e.message)
  } finally {
    saving.value = false
  }
}

async function remove(category, id) {
  try {
    await api.deleteCredential(category, id)
    message.success('已删除')
    await reload()
  } catch (e) {
    message.error('删除失败: ' + e.message)
  }
}

async function makeDefault(category, id) {
  try {
    await api.setDefaultCredential(category, id)
    await reload()
  } catch (e) {
    message.error(e.message)
  }
}
</script>

<template>
  <div class="vault">
    <n-card v-for="cat in CATEGORIES" :key="cat.key" class="cat-card" :bordered="false">
      <div class="cat-head">
        <div>
          <div class="cat-title">{{ cat.label }}</div>
          <div class="cat-desc">{{ cat.desc }}</div>
        </div>
        <n-button type="primary" size="small" @click="openAdd(cat.key)">
          <template #icon><n-icon :component="AddOutline" /></template>
          添加
        </n-button>
      </div>

      <n-empty v-if="!data[cat.key]?.length" description="尚未配置" class="cat-empty" />

      <div v-else class="entries">
        <div v-for="e in data[cat.key]" :key="e.id" class="entry" :class="{ disabled: !e.enabled }">
          <div class="entry-main">
            <div class="entry-line1">
              <span class="entry-alias">{{ e.alias || '(未命名)' }}</span>
              <n-tag v-if="e.is_default" type="success" size="small" round :bordered="false">默认</n-tag>
              <n-tag v-if="!e.enabled" size="small" round :bordered="false">已禁用</n-tag>
            </div>
            <div class="entry-line2">
              <span class="entry-url">{{ e.base_url || '—' }}</span>
              <span v-if="e.model" class="entry-model">· {{ e.model }}</span>
              <span class="entry-key">· {{ e.api_key_masked || '无 Key' }}</span>
            </div>
          </div>
          <n-space :size="6">
            <n-button v-if="!e.is_default" quaternary circle size="small" title="设为默认" @click="makeDefault(cat.key, e.id)">
              <template #icon><n-icon :component="StarOutline" /></template>
            </n-button>
            <n-button v-else quaternary circle size="small" disabled title="当前默认">
              <template #icon><n-icon :component="Star" color="var(--app-accent)" /></template>
            </n-button>
            <n-button quaternary circle size="small" title="编辑" @click="openEdit(cat.key, e)">
              <template #icon><n-icon :component="CreateOutline" /></template>
            </n-button>
            <n-popconfirm @positive-click="remove(cat.key, e.id)">
              <template #trigger>
                <n-button quaternary circle size="small" title="删除">
                  <template #icon><n-icon :component="TrashOutline" /></template>
                </n-button>
              </template>
              确认删除该凭据？
            </n-popconfirm>
          </n-space>
        </div>
      </div>
    </n-card>

    <n-modal
      v-model:show="showModal"
      preset="card"
      :title="(editing?.isNew ? '添加' : '编辑') + ' · ' + currentCat.label"
      style="width: 560px"
    >
      <n-form label-placement="top">
        <n-form-item label="别名">
          <n-input v-model:value="form.alias" placeholder="便于识别，如：主力生图 / 备用Key" />
        </n-form-item>
        <n-form-item v-if="editing?.category === 'video'" label="接口类型 (provider)">
          <n-select v-model:value="form.provider" :options="videoProviderOptions" />
        </n-form-item>
        <n-form-item v-if="editing?.category === 'image'" label="Provider">
          <n-select v-model:value="form.provider" :options="imageProviderOptions" />
        </n-form-item>
        <n-form-item v-if="isViduProvider" label="Vidu fixed base_url">
          <div class="fixed-provider-url">https://service.vidu.cn/vidu/v1</div>
        </n-form-item>
        <n-form-item v-else-if="isHuajingProvider" label="API 地址 (base_url)">
          <div class="fixed-provider-url">{{ HUAJING_BASE_URL }}</div>
        </n-form-item>
        <n-form-item v-else-if="editing?.category !== 'image_host' && !isDoubaoPoolProvider" label="API 地址 (base_url)">
          <n-input v-model:value="form.base_url" :placeholder="isSeedanceProvider ? 'http://119.45.252.34:8618 或 http://119.45.158.223:8618' : 'https://your-relay.example.com/v1'" />
        </n-form-item>
        <n-form-item v-if="!isDoubaoPoolProvider" :label="isHuajingProvider ? '画镜数据目录 / 安装目录' : 'API Key'">
          <n-input
            v-model:value="form.api_key"
            :type="isHuajingProvider ? 'text' : 'password'"
            :show-password-on="isHuajingProvider ? undefined : 'click'"
            :placeholder="isHuajingProvider ? '如 C:\\Users\\你\\AppData\\Roaming\\comic-generator-electron，或 F:\\画镜\\comic-generator-electron' : '留空则保留原值（编辑时）'"
          />
          <div v-if="isHuajingProvider" class="field-hint">
            保存或连通性测试时会自动扫描该目录并提取 token；留空则扫描当前用户默认 AppData。
          </div>
        </n-form-item>
        <n-form-item v-if="isDoubaoPoolProvider" label="本地凭证">
          <div class="local-provider-box">
            <b>由左侧“豆包号池”自动托管</b>
            <span>无需 API 地址或 Key。录入账号并打开登录后，VMO 会使用本地 Chrome/Edge 号池生成视频。</span>
          </div>
        </n-form-item>
        <n-form-item v-if="currentCat.needsModel" label="模型">
          <n-space vertical style="width: 100%">
            <n-select
              v-model:value="form.model"
              filterable
              tag
              :options="effectiveModelOptions"
              placeholder="选择或手动输入模型名"
            />
            <n-button v-if="!isDoubaoPoolProvider" size="small" :loading="modelLoading" @click="fetchModels">
              <template #icon><n-icon :component="FlashOutline" /></template>
              {{ isViduProvider ? 'Load Vidu models' : isHuajingProvider ? '载入画镜模型' : isSeedanceProvider ? '载入 Seedance 模型' : '获取模型列表' }}
            </n-button>
          </n-space>
        </n-form-item>
        <n-form-item v-if="editing?.category === 'video' && isSeedanceProvider" label="用户首页信息">
          <div class="seedance-info">
            <div class="seedance-info-head">
              <span>飞书 Seedance 用户首页接口（UserIndex）</span>
              <n-button size="tiny" :loading="userInfoLoading" @click="queryUserInfo">
                <template #icon><n-icon :component="FlashOutline" /></template>
                查询
              </n-button>
            </div>
            <div v-if="userInfo" class="seedance-info-grid">
              <div><span>用户 ID</span><b>{{ userInfo.Id ?? '-' }}</b></div>
              <div><span>普通 Token</span><b>{{ userInfo.Token ?? '-' }}</b></div>
              <div><span>快速 Token</span><b>{{ userInfo.FastToken ?? '-' }}</b></div>
              <div><span>国际版时长</span><b>{{ userInfo.SdDuration ?? '-' }}s</b></div>
              <div><span>创建时间</span><b>{{ userInfo.CreatedAt || '-' }}</b></div>
              <div><span>更新时间</span><b>{{ userInfo.UpdatedAt || '-' }}</b></div>
            </div>
            <div v-else class="seedance-info-empty">查询后会在这里显示账号额度与首页信息。</div>
          </div>
        </n-form-item>
        <n-form-item v-if="editing?.category === 'video' && isHuajingProvider" label="画镜积分">
          <div class="seedance-info huajing-quota">
            <div class="seedance-info-head">
              <span>当前画镜登录账号积分</span>
              <n-button size="tiny" :loading="userInfoLoading" @click="queryUserInfo">
                <template #icon><n-icon :component="FlashOutline" /></template>
                查询积分
              </n-button>
            </div>
            <div v-if="userInfo" class="seedance-info-grid huajing-quota-grid">
              <div class="quota-primary"><span>剩余铃铛</span><b>{{ userInfo.remaining_quota ?? '-' }}</b></div>
              <div><span>已用铃铛</span><b>{{ userInfo.used_quota ?? '-' }}</b></div>
              <div><span>免费额度</span><b>{{ userInfo.free_quota ?? '-' }}</b></div>
              <div><span>会员等级</span><b>{{ userInfo.membership_level || '-' }}</b></div>
              <div><span>昵称</span><b>{{ userInfo.nickname || userInfo.username || '-' }}</b></div>
              <div><span>用户 ID</span><b>{{ userInfo.id ?? '-' }}</b></div>
            </div>
            <div v-else class="seedance-info-empty">查询后会显示当前画镜账号的剩余铃铛、会员等级和账号信息。</div>
          </div>
        </n-form-item>
        <n-space>
          <n-form-item label="启用">
            <n-switch v-model:value="form.enabled" />
          </n-form-item>
          <n-form-item label="设为默认">
            <n-switch v-model:value="form.is_default" />
          </n-form-item>
        </n-space>
        <n-form-item label="备注">
          <n-input v-model:value="form.note" type="textarea" :autosize="{ minRows: 1, maxRows: 3 }" placeholder="用量/限制说明（可选）" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="space-between">
          <n-space>
            <n-button v-if="editing?.category !== 'image_host' && !isDoubaoPoolProvider" :loading="testing" @click="testConn">
              <template #icon><n-icon :component="FlashOutline" /></template>
              连通性测试
            </n-button>
            <n-button
              v-if="editing?.category === 'video' && isSeedanceProvider"
              :loading="userInfoLoading"
              @click="queryUserInfo"
            >
              用户首页查询
            </n-button>
            <n-button
              v-if="editing?.category === 'video' && isHuajingProvider"
              :loading="userInfoLoading"
              @click="queryUserInfo"
            >
              查询画镜积分
            </n-button>
          </n-space>
          <n-space>
            <n-button @click="showModal = false">取消</n-button>
            <n-button type="primary" :loading="saving" @click="save">保存</n-button>
          </n-space>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.vault {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.cat-card {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border);
  border-radius: var(--r-card);
}
.cat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.cat-title {
  font-weight: 700;
  font-size: 15px;
}
.cat-desc {
  color: var(--app-text-muted);
  font-size: 12px;
  margin-top: 3px;
}
.cat-empty {
  padding: 16px 0;
}
.entries {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.entry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  background: var(--app-bg-soft);
  border: 1px solid var(--app-border);
  border-radius: 12px;
}
.entry.disabled {
  opacity: 0.55;
}
.entry-line1 {
  display: flex;
  align-items: center;
  gap: 8px;
}
.entry-alias {
  font-weight: 600;
}
.entry-line2 {
  margin-top: 4px;
  color: var(--app-text-muted);
  font-size: 12px;
}
.entry-model {
  color: var(--app-accent-alt);
}
.fixed-provider-url {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  color: var(--app-text-muted);
  background: var(--app-bg-soft);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}
.local-provider-box {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border: 1px solid color-mix(in srgb, var(--app-accent) 32%, var(--app-border));
  border-radius: 8px;
  background: var(--app-accent-soft);
}
.local-provider-box b {
  color: var(--app-accent);
}
.local-provider-box span {
  color: var(--app-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}
.field-hint {
  width: 100%;
  margin-top: 6px;
  color: var(--app-text-muted);
  font-size: 12px;
  line-height: 1.5;
}
.seedance-info {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-bg-soft);
}
.seedance-info-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: var(--app-text-muted);
  margin-bottom: 10px;
}
.seedance-info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.seedance-info-grid div {
  min-width: 0;
  padding: 8px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--app-surface) 78%, transparent);
}
.seedance-info-grid span {
  display: block;
  color: var(--app-text-muted);
  font-size: 11px;
}
.seedance-info-grid b {
  display: block;
  margin-top: 3px;
  font-size: 12px;
  font-weight: 650;
  word-break: break-all;
}
.seedance-info-empty {
  color: var(--app-text-muted);
  font-size: 12px;
}
.huajing-quota-grid .quota-primary {
  background: color-mix(in srgb, #16a34a 16%, var(--app-surface));
  border: 1px solid color-mix(in srgb, #16a34a 38%, transparent);
}
.huajing-quota-grid .quota-primary b {
  color: #22c55e;
  font-size: 18px;
}
</style>
