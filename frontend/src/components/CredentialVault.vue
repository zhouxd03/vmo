<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  NCard, NButton, NTag, NIcon, NModal, NForm, NFormItem, NInput, NSwitch,
  NSelect, NEmpty, NSpace, NPopconfirm, useMessage,
} from 'naive-ui'
import {
  AddOutline, CreateOutline, TrashOutline, FlashOutline, StarOutline, Star,
} from '@vicons/ionicons5'
import { api } from '../api'

const message = useMessage()

const CATEGORIES = [
  { key: 'image', label: '生图 API', desc: '文生图 / 垫图（OpenAI 兼容 images 接口）', needsModel: true },
  { key: 'video', label: '生视频 API', desc: '文生 / 图生视频（地址+Key 手动填写，不写死）', needsModel: true },
  { key: 'llm', label: 'LLM 中转站', desc: '剧本分析 / 多模态复核（可拉取模型列表）', needsModel: true, fetchModels: true },
  { key: 'image_host', label: '图床（可选）', desc: '图生视频的公网图床；留空则用内置 catbox/0x0 兜底', needsModel: false },
]

const videoProviderOptions = [
  { label: 'Vidu (fixed web API, Cookie)', value: 'vidu' },
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
const needsBaseUrl = computed(() =>
  editing.value?.category !== 'image_host' && !isViduProvider.value)
const effectiveModelOptions = computed(() =>
  isViduProvider.value
    ? (editing.value?.category === 'image' ? VIDU_IMAGE_MODELS : VIDU_VIDEO_MODELS)
    : isSeedanceProvider.value ? SEEDANCE_MODELS : modelOptions.value)

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
      base_url: form.value.base_url,
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
      base_url: form.value.base_url,
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
      base_url: form.value.base_url,
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
    await api.upsertCredential(editing.value.category, form.value)
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
        <n-form-item v-else-if="editing?.category !== 'image_host'" label="API 地址 (base_url)">
          <n-input v-model:value="form.base_url" :placeholder="isSeedanceProvider ? 'http://119.45.252.34:8618 或 http://119.45.158.223:8618' : 'https://your-relay.example.com/v1'" />
        </n-form-item>
        <n-form-item label="API Key">
          <n-input v-model:value="form.api_key" type="password" show-password-on="click" placeholder="留空则保留原值（编辑时）" />
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
            <n-button size="small" :loading="modelLoading" @click="fetchModels">
              <template #icon><n-icon :component="FlashOutline" /></template>
              {{ isViduProvider ? 'Load Vidu models' : isSeedanceProvider ? '载入 Seedance 模型' : '获取模型列表' }}
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
            <n-button v-if="editing?.category !== 'image_host'" :loading="testing" @click="testConn">
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
</style>
