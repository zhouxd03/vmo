<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NSelect, NButton, NIcon, NCard, NEmpty, NModal, NForm, NFormItem, NInput,
  NImage, NTag, NSpin, NPopconfirm, NAlert, NTabs, NTabPane, useMessage,
} from 'naive-ui'
import {
  PersonOutline, LocationOutline, CubeOutline, AddOutline, ImageOutline,
  CreateOutline, TrashOutline, DownloadOutline, SparklesOutline, FlaskOutline,
  FlashOutline, CloudUploadOutline, RefreshOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'
import { useTasksStore } from '../stores/tasks'

const router = useRouter()
const message = useMessage()
const store = useProjectStore()
const tasks = useTasksStore()

const assets = ref([])
const loading = ref(false)
const nowTick = ref(Date.now())
const imageVersions = ref({})
const GEN_STORAGE_KEY = 'assets:generation-options'
const aspectOptions = [
  { label: '1:1 方图', value: '1:1' },
  { label: '16:9 横图', value: '16:9' },
  { label: '9:16 竖图', value: '9:16' },
  { label: '4:3 横图', value: '4:3' },
  { label: '3:4 竖图', value: '3:4' },
]
const resolutionOptions = [
  { label: '1080p', value: '1080p' },
  { label: '2K', value: '2K' },
  { label: '4K', value: '4K' },
]
const LEGACY_SIZE_MAP = {
  '1024x1024': '1:1',
  '1536x864': '16:9',
  '1280x720': '16:9',
  '1152x864': '4:3',
  '864x1152': '3:4',
  '864x1536': '9:16',
}
const genOpts = ref({ model: '', aspect: '1:1', resolution: '1080p' })
const genOptsLoaded = ref(false)

const COLS = [
  { type: 'character', label: '人物', trigger: '@', icon: PersonOutline, cls: 'c' },
  { type: 'scene', label: '场景', trigger: '#', icon: LocationOutline, cls: 's' },
  { type: 'prop', label: '道具', trigger: '$', icon: CubeOutline, cls: 'p' },
]

let clockTimer = null

onMounted(async () => {
  clockTimer = window.setInterval(() => { nowTick.value = Date.now() }, 1000)
  await store.refreshList()
  if (!store.current && store.projects.length) await store.select(store.projects[0].id)
  await loadGenerationOptions()
  if (store.current) await loadAssets()
})

onBeforeUnmount(() => {
  if (clockTimer) window.clearInterval(clockTimer)
})

async function loadGenerationOptions() {
  try {
    const saved = JSON.parse(localStorage.getItem(GEN_STORAGE_KEY) || '{}')
    const settings = await api.getSettings().catch(() => null)
    const normalized = normalizeGenerationOptions(saved, settings?.image_size)
    genOpts.value = {
      model: typeof saved.model === 'string' ? saved.model : '',
      aspect: normalized.aspect,
      resolution: normalized.resolution,
    }
  } catch {
    genOpts.value = { model: '', aspect: '1:1', resolution: '1080p' }
  } finally {
    genOptsLoaded.value = true
  }
}

function normalizeGenerationOptions(saved = {}, fallbackSize = '') {
  const rawAspect = typeof saved.aspect === 'string' ? saved.aspect : ''
  const rawSize = typeof saved.size === 'string' ? saved.size : (fallbackSize || '')
  const rawResolution = typeof saved.resolution === 'string' ? saved.resolution : ''
  let aspect = aspectOptions.some((o) => o.value === rawAspect) ? rawAspect : ''
  let resolution = normalizeResolution(rawResolution)

  const packed = rawSize.match(/^(\d+:\d+)@(.+)$/)
  if (packed) {
    if (!aspect) aspect = packed[1]
    if (!resolution) resolution = normalizeResolution(packed[2])
  } else if (/^\d+:\d+$/.test(rawSize)) {
    if (!aspect) aspect = rawSize
  } else if (LEGACY_SIZE_MAP[rawSize]) {
    if (!aspect) aspect = LEGACY_SIZE_MAP[rawSize]
  }

  if (!aspectOptions.some((o) => o.value === aspect)) aspect = '1:1'
  if (!resolution) resolution = '1080p'
  return { aspect, resolution }
}

function normalizeResolution(value) {
  const v = String(value || '').trim().toLowerCase()
  if (v === '4k') return '4K'
  if (v === '2k') return '2K'
  if (v === '1080p') return '1080p'
  return ''
}

function saveGenerationOptions() {
  try { localStorage.setItem(GEN_STORAGE_KEY, JSON.stringify(genOpts.value)) }
  catch { /* ignore */ }
}

function generationPayload() {
  saveGenerationOptions()
  const aspect = genOpts.value.aspect || '1:1'
  const resolution = genOpts.value.resolution || '1080p'
  return {
    model: (genOpts.value.model || '').trim() || undefined,
    size: `${aspect}@${resolution}`,
  }
}

watch(genOpts, () => {
  if (genOptsLoaded.value) saveGenerationOptions()
}, { deep: true })

watch(() => tasks.assetBatchGen.running, async (running, wasRunning) => {
  if (wasRunning && !running && tasks.assetProjectId === store.currentId) {
    await loadAssets()
    if (tasks.assetBatchGen.failed?.length) {
      message.warning(`生成完成：成功 ${tasks.assetBatchGen.done}/${tasks.assetBatchGen.total}，失败 ${tasks.assetBatchGen.failed.length}`)
    } else if (tasks.assetBatchGen.jobId) {
      message.success(`已补全 ${tasks.assetBatchGen.done} 张缺失参考图`)
    }
  }
})

watch(() => tasks.assetJobs, async (jobs, oldJobs) => {
  if (tasks.assetProjectId !== store.currentId) return
  for (const [assetId, job] of Object.entries(jobs || {})) {
    const old = oldJobs?.[assetId]
    if (old?.running && !job.running) {
      if (job.status === 'done') {
        markImageFresh(assetId)
        await loadAssets()
        message.success(`${job.assetName || '资产'} 参考图已更新`)
      } else if (job.status === 'error') {
        message.error(`生成失败：${job.error || job.message || '未知错误'}`)
      }
    }
  }
}, { deep: true })

const projectOptions = computed(() =>
  store.projects.map((p) => ({ label: `${p.name}（${p.stage}）`, value: p.id })))

async function onSelect(pid) {
  await store.select(pid)
  await loadAssets()
}

async function loadAssets() {
  if (!store.current) return
  loading.value = true
  try { assets.value = await api.listAssets(store.current.id) }
  finally { loading.value = false }
}

const byType = (t) => assets.value.filter((a) => a.type === t)

// Stable per-type asset code (C01 / S01 / P01 …) shown on each card so users can
// reference assets by number and keep numbering consistent across episodes.
const CODE_PREFIX = { character: 'C', scene: 'S', prop: 'P' }
function assetCode(col, idx) {
  return `${CODE_PREFIX[col.type] || 'A'}${String(idx + 1).padStart(2, '0')}`
}

// ── add / edit modal ──
const modal = ref(false)
const editing = ref(null)
const form = ref({ type: 'character', name: '', desc: '', appearance: '', voice: '' })

function openAdd(type) {
  editing.value = null
  form.value = { type, name: '', desc: '', appearance: '', voice: '' }
  modal.value = true
}
function openEdit(a) {
  editing.value = a
  form.value = { type: a.type, name: a.name, desc: a.desc, appearance: a.appearance, voice: a.voice || '' }
  modal.value = true
}
async function submit() {
  if (!form.value.name.trim()) { message.warning('请填写名称'); return }
  try {
    if (editing.value) {
      await api.updateAsset(store.current.id, editing.value.id, {
        name: form.value.name, desc: form.value.desc, appearance: form.value.appearance, voice: form.value.voice,
      })
      message.success('已保存')
    } else {
      await api.addAsset(store.current.id, form.value)
      message.success('已添加资产')
    }
    modal.value = false
    await loadAssets()
  } catch (e) { message.error(e.message) }
}
async function removeAsset(a) {
  await api.deleteAsset(store.current.id, a.id)
  await loadAssets()
}

async function seedFromBible() {
  try {
    await api.seedAssets(store.current.id)
    message.success('已从故事圣经导入资产')
    await loadAssets()
  } catch (e) { message.error(e.message) }
}

// ── one-click generate missing reference images ──
const missingCount = computed(() => assets.value.filter((a) => !a.ref_image).length)
const batchGen = computed(() => (
  tasks.assetProjectId === store.currentId
    ? tasks.assetBatchGen
    : { running: false, done: 0, total: 0, failed: [], message: '', startedAt: null, jobId: '' }
))

function elapsedText(startedAt) {
  if (!startedAt) return '0秒'
  const seconds = Math.max(0, Math.floor((nowTick.value - startedAt) / 1000))
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}分${String(rest).padStart(2, '0')}秒`
}

function assetJob(a) {
  if (tasks.assetProjectId !== store.currentId) return null
  return tasks.assetJob(a.id)
}

function markImageFresh(assetId) {
  imageVersions.value = { ...imageVersions.value, [assetId]: Date.now() }
}

async function genMissing() {
  if (!store.current) return
  if (!missingCount.value) { message.info('所有资产都已有参考图'); return }
  const payload = generationPayload()
  await tasks.startMissingAssetRefs(store.current.id, payload, missingCount.value)
  if (!tasks.assetBatchGen.running && tasks.assetBatchGen.message && !tasks.assetBatchGen.jobId) {
    message.error('一键生成失败: ' + tasks.assetBatchGen.message)
  }
}

async function retryFailed() {
  // failed assets still have no ref_image, so generate-missing only retries them.
  await genMissing()
}

// ── import an external image as the reference ──
const importingId = ref(null)
const fileInput = ref(null)
const importModal = ref(false)
const importTab = ref('library')
const libraryLoading = ref(false)
const libraryItems = ref([])
const libraryQuery = ref('')
let importTarget = null
function openImport(a) {
  importTarget = a
  importModal.value = true
  importTab.value = 'library'
  loadAssetLibrary()
}
function pickImport(a) {
  if (a) importTarget = a
  fileInput.value?.click()
}
const filteredLibraryItems = computed(() => {
  const targetType = importTarget?.type
  const q = libraryQuery.value.trim().toLowerCase()
  return libraryItems.value.filter((item) => {
    if (targetType && item.type !== targetType) return false
    if (!q) return true
    return [item.name, item.project_name, item.desc, item.appearance, item.voice]
      .filter(Boolean).join(' ').toLowerCase().includes(q)
  })
})
async function loadAssetLibrary() {
  if (!store.current) return
  libraryLoading.value = true
  try {
    libraryItems.value = await api.listAssetLibrary(store.current.id)
  } catch (e) {
    message.error('素材库加载失败: ' + e.message)
  } finally {
    libraryLoading.value = false
  }
}
async function importFromLibrary(item) {
  if (!store.current || !importTarget || !item) return
  importingId.value = importTarget.id
  try {
    await api.importAssetFromLibrary(store.current.id, importTarget.id, {
      source_project_id: item.project_id,
      source_asset_id: item.asset_id,
    })
    markImageFresh(importTarget.id)
    message.success(`已复用素材库图片：${item.trigger}${item.name}`)
    importModal.value = false
    await loadAssets()
  } catch (e) {
    message.error('素材库导入失败: ' + e.message)
  } finally {
    importingId.value = null
  }
}
async function onFilePicked(e) {
  const file = e.target.files?.[0]
  e.target.value = ''  // allow re-picking the same file
  if (!file || !importTarget) return
  importingId.value = importTarget.id
  try {
    await api.importAssetImage(store.current.id, importTarget.id, file)
    message.success(`已导入 ${importTarget.trigger}${importTarget.name} 参考图`)
    await loadAssets()
  } catch (err) {
    message.error('导入失败: ' + err.message)
  } finally {
    importingId.value = null
    importTarget = null
    importModal.value = false
  }
}

const genningId = ref(null)
async function genRef(a) {
  const current = assetJob(a)
  if (current?.running) return
  const payload = generationPayload()
  genningId.value = a.id
  try {
    await tasks.startAssetRef(store.current.id, a, payload)
  } catch (e) {
    message.error('生成失败: ' + e.message)
  } finally { genningId.value = null }
}

function imgUrl(a) {
  if (!a.ref_image) return null
  const v = imageVersions.value[a.id]
  const url = api.assetImageUrl(store.current.id, a.ref_image)
  return v ? `${url}?v=${v}` : url
}

// ── @ resolve tester ──
const testText = ref('')
const resolved = ref(null)
async function runResolve() {
  if (!store.current) return
  resolved.value = await api.resolveMentions(store.current.id, testText.value)
}
</script>

<template>
  <div>
    <PageHeader title="资产库" subtitle="人物 @ / 场景 # / 道具 $ 提取 → 参考图 → 生成时自动 @ 引用">
      <template #actions>
        <n-select
          :value="store.currentId" :options="projectOptions"
          placeholder="选择项目" style="width: 280px" @update:value="onSelect"
        />
      </template>
    </PageHeader>

    <n-empty v-if="!store.current" description="请先在「项目导入」导入一个项目">
      <template #extra><n-button size="small" @click="router.push('/import')">去导入</n-button></template>
    </n-empty>

    <template v-else>
      <div class="toolbar">
        <n-button size="small" secondary @click="seedFromBible">
          <template #icon><n-icon :component="SparklesOutline" /></template>
          从故事圣经导入
        </n-button>
        <div class="gen-controls">
          <n-input
            v-model:value="genOpts.model"
            size="small"
            clearable
            placeholder="生成模型：留空使用默认"
            class="gen-model"
            @blur="saveGenerationOptions"
          />
          <n-select
            v-model:value="genOpts.aspect"
            size="small"
            :options="aspectOptions"
            class="gen-aspect"
            @update:value="saveGenerationOptions"
          />
          <n-select
            v-model:value="genOpts.resolution"
            size="small"
            :options="resolutionOptions"
            class="gen-resolution"
            @update:value="saveGenerationOptions"
          />
        </div>
        <n-button
          size="small" type="primary"
          :loading="batchGen.running"
          :disabled="!missingCount && !batchGen.running"
          @click="genMissing"
        >
          <template #icon><n-icon :component="FlashOutline" /></template>
          一键生成缺失参考图<span v-if="missingCount">（{{ missingCount }}）</span>
        </n-button>
        <span class="hint">提示：模型留空走默认凭据；比例和清晰度会同时影响单张重生与一键补全。</span>
      </div>

      <n-alert
        v-if="batchGen.running || batchGen.total"
        :type="batchGen.failed.length ? 'warning' : (batchGen.running ? 'info' : 'success')"
        :bordered="false" style="margin-bottom: 14px"
      >
        <template v-if="batchGen.running">
          正在生成缺失参考图… {{ batchGen.done }} / {{ batchGen.total }}
          <span class="batch-sub">已用 {{ elapsedText(batchGen.startedAt) }} · {{ batchGen.message }}</span>
          <div class="batch-progress">
            <span :style="{ width: `${Math.min(100, Math.round(((batchGen.done || 0) / (batchGen.total || 1)) * 100))}%` }"></span>
          </div>
        </template>
        <template v-else>
          本次补全：成功 {{ batchGen.done }} / {{ batchGen.total }}
          <template v-if="batchGen.failed.length">
            ，失败 {{ batchGen.failed.length }}：
            <n-tag
              v-for="f in batchGen.failed" :key="f.id" size="small"
              type="error" :bordered="false" style="margin: 0 4px 4px 0"
            >{{ f.trigger }}{{ f.name }}</n-tag>
            <n-button size="tiny" tertiary style="margin-left: 6px" @click="retryFailed">
              <template #icon><n-icon :component="RefreshOutline" /></template>重试失败项
            </n-button>
          </template>
        </template>
      </n-alert>
      <input ref="fileInput" type="file" accept="image/*" style="display:none" @change="onFilePicked" />

      <n-spin :show="loading">
        <div class="cols">
          <n-card v-for="col in COLS" :key="col.type" :bordered="false" class="col">
            <div class="col-head">
              <div class="col-title">
                <n-icon :component="col.icon" />
                {{ col.label }} <span class="tg" :class="col.cls">{{ col.trigger }}</span>
                <span class="cnt">{{ byType(col.type).length }}</span>
              </div>
              <n-button size="tiny" tertiary @click="openAdd(col.type)">
                <template #icon><n-icon :component="AddOutline" /></template>添加
              </n-button>
            </div>

            <n-empty v-if="!byType(col.type).length" description="暂无" size="small" style="margin: 24px 0" />
            <div v-else class="cards">
              <div v-for="(a, ai) in byType(col.type)" :key="a.id" class="asset" :class="{ 'character-card': col.type === 'character' }">
                <div class="thumb">
                  <n-image v-if="imgUrl(a)" :src="imgUrl(a)" object-fit="cover" width="100%" />
                  <div v-else class="thumb-empty"><n-icon :component="ImageOutline" size="22" /></div>
                  <span class="code-badge">{{ assetCode(col, ai) }}</span>
                  <div v-if="assetJob(a)?.running" class="thumb-busy">
                    <n-spin size="small" />
                  </div>
                </div>
                <div class="asset-body">
                  <div class="asset-name"><span class="tg" :class="col.cls">{{ col.trigger }}</span>{{ a.name }}</div>
                  <template v-if="col.type === 'character'">
                    <div class="prop-line voice-line">
                      <span class="prop-k">音色</span>
                      <span class="prop-v">{{ a.voice || '未设置' }}</span>
                    </div>
                    <div class="prop-line">
                      <span class="prop-k">外形</span>
                      <span class="prop-v">{{ a.appearance || '未设置' }}</span>
                    </div>
                    <div class="prop-line">
                      <span class="prop-k">描述</span>
                      <span class="prop-v">{{ a.desc || '—' }}</span>
                    </div>
                  </template>
                  <div v-else class="asset-desc">{{ a.appearance || a.desc || '—' }}</div>
                  <div v-if="assetJob(a)" class="gen-status" :class="{ error: assetJob(a)?.status === 'error', done: assetJob(a)?.status === 'done' }">
                    <div class="gen-status-main">
                      <span>{{ assetJob(a)?.message || '准备中' }}</span>
                      <span v-if="assetJob(a)?.running">已用 {{ elapsedText(assetJob(a)?.startedAt) }}</span>
                    </div>
                    <div v-if="assetJob(a)?.running" class="gen-status-bar">
                      <span :style="{ width: `${Math.min(100, Math.round(((assetJob(a)?.progress || 0) / (assetJob(a)?.total || 1)) * 100))}%` }"></span>
                    </div>
                    <div v-if="assetJob(a)?.error" class="gen-status-error">{{ assetJob(a)?.error }}</div>
                  </div>
                  <div class="asset-actions">
                    <n-button size="tiny" :loading="assetJob(a)?.running || genningId === a.id" :disabled="assetJob(a)?.running" @click="genRef(a)">
                      <template #icon><n-icon :component="ImageOutline" /></template>
                      {{ assetJob(a)?.running ? '生成中' : (a.ref_image ? '重生' : '参考图') }}
                    </n-button>
                    <n-button size="tiny" quaternary :loading="importingId === a.id" title="导入/复用图片" @click="openImport(a)">
                      <template #icon><n-icon :component="CloudUploadOutline" /></template>
                    </n-button>
                    <n-button size="tiny" quaternary @click="openEdit(a)">
                      <template #icon><n-icon :component="CreateOutline" /></template>
                    </n-button>
                    <n-popconfirm @positive-click="removeAsset(a)">
                      <template #trigger>
                        <n-button size="tiny" quaternary type="error">
                          <template #icon><n-icon :component="TrashOutline" /></template>
                        </n-button>
                      </template>
                      删除资产「{{ a.name }}」？
                    </n-popconfirm>
                  </div>
                </div>
              </div>
            </div>
          </n-card>
        </div>
      </n-spin>

      <!-- @ auto-reference tester -->
      <n-card title="@ 自动引用测试" :bordered="false" class="resolver">
        <div class="resolver-desc">
          输入一段分镜文本，系统会自动识别其中出现的资产名 → 注入参考图占位符
          <code>@image1 / @image2…</code>（缺参考图会告警跳过）。这正是批量生成时喂给模型的逻辑。
        </div>
        <n-input
          v-model:value="testText" type="textarea"
          :autosize="{ minRows: 3, maxRows: 6 }"
          placeholder="例如：林夏走进废弃停车场，手里攥着青铜酒爵……"
        />
        <n-button type="primary" size="small" style="margin-top: 10px" @click="runResolve">
          <template #icon><n-icon :component="FlaskOutline" /></template>解析引用
        </n-button>

        <div v-if="resolved" class="resolved">
          <div class="resolved-row">
            <span class="rk">替换后文本</span>
            <div class="rv mono">{{ resolved.text || '（空）' }}</div>
          </div>
          <div class="resolved-row">
            <span class="rk">引用资产</span>
            <div class="rv">
              <n-tag v-for="m in resolved.materials" :key="m.asset_id" size="small" round
                :type="m.ref_image ? 'success' : 'warning'" :bordered="false" style="margin: 0 6px 6px 0">
                {{ m.placeholder }} = {{ m.trigger }}{{ m.name }}{{ m.ref_image ? '' : '（无参考图）' }}
              </n-tag>
              <span v-if="!resolved.materials.length" class="muted">未识别到任何资产</span>
            </div>
          </div>
          <n-alert v-if="resolved.warnings?.length" type="warning" :bordered="false" style="margin-top: 8px">
            <div v-for="(w, i) in resolved.warnings" :key="i">{{ w }}</div>
          </n-alert>
        </div>
      </n-card>
    </template>

    <n-modal v-model:show="importModal" preset="card" title="导入参考图" class="import-modal">
      <div class="import-target" v-if="importTarget">
        当前资产：<b>{{ importTarget.trigger }}{{ importTarget.name }}</b>
      </div>
      <n-tabs v-model:value="importTab" type="segment">
        <n-tab-pane name="library" tab="素材库复用">
          <div class="library-head">
            <n-input v-model:value="libraryQuery" clearable size="small" placeholder="搜索素材名、项目名、描述" />
            <n-button size="small" tertiary :loading="libraryLoading" @click="loadAssetLibrary">
              <template #icon><n-icon :component="RefreshOutline" /></template>
              刷新
            </n-button>
          </div>
          <n-spin :show="libraryLoading">
            <n-empty v-if="!filteredLibraryItems.length" description="暂无可复用素材" style="margin: 26px 0" />
            <div v-else class="library-grid">
              <button
                v-for="item in filteredLibraryItems"
                :key="`${item.project_id}:${item.asset_id}`"
                class="library-card"
                @click="importFromLibrary(item)"
              >
                <img :src="api.assetImageUrl(item.project_id, item.ref_image)" alt="" />
                <div class="library-info">
                  <b>{{ item.trigger }}{{ item.name }}</b>
                  <span>{{ item.project_name }}</span>
                  <small>{{ item.voice || item.appearance || item.desc || '无描述' }}</small>
                </div>
              </button>
            </div>
          </n-spin>
        </n-tab-pane>
        <n-tab-pane name="local" tab="本地文件">
          <div class="local-import">
            <n-icon :component="CloudUploadOutline" size="28" />
            <div>
              <b>从资源管理器选择图片</b>
              <span>保留原来的本地导入方式，图片会复制到当前项目资产目录。</span>
            </div>
            <n-button type="primary" :loading="!!importingId" @click="pickImport()">
              打开资源管理器
            </n-button>
          </div>
        </n-tab-pane>
      </n-tabs>
    </n-modal>

    <!-- add/edit modal -->
    <n-modal v-model:show="modal" preset="card" :title="editing ? '编辑资产' : '添加资产'" style="width: 520px">
      <n-form label-placement="top">
        <n-form-item label="名称">
          <n-input v-model:value="form.name" placeholder="资产名（生成时用作 @/#/$ 引用名）" />
        </n-form-item>
        <n-form-item v-if="form.type === 'character'" label="外形锚定（appearance）">
          <n-input v-model:value="form.appearance" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }"
            placeholder="外形/服装/发型/特征，用于生成三视图参考图" />
        </n-form-item>
        <n-form-item v-if="form.type === 'character'" label="角色音色（voice）">
          <n-input v-model:value="form.voice" placeholder="如：少女音，清亮偏软，急促时尾音发颤" />
        </n-form-item>
        <n-form-item :label="form.type === 'prop' ? '形制/年代/材质（防穿帮）' : '描述'">
          <n-input v-model:value="form.desc" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }"
            :placeholder="form.type === 'prop' ? '如：古代青铜酒爵，非现代玻璃高脚杯' : '地点/时代/光影/布局 等'" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 10px">
          <n-button @click="modal = false">取消</n-button>
          <n-button type="primary" @click="submit">保存</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap;
}
.gen-controls {
  display: flex; align-items: center; gap: 8px;
  padding: 4px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-bg-soft) 82%, transparent);
}
.gen-model { width: 220px; }
.gen-aspect { width: 132px; }
.gen-resolution { width: 108px; }
.hint { color: var(--app-text-muted); font-size: 12px; }
.import-modal { width: min(880px, 92vw); }
.import-target {
  color: var(--app-text-muted);
  margin-bottom: 12px;
}
.import-target b { color: var(--app-text-primary); }
.library-head {
  display: flex;
  gap: 10px;
  margin: 12px 0;
}
.library-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  max-height: 460px;
  overflow: auto;
  padding-right: 4px;
}
.library-card {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: 10px;
  padding: 8px;
  border: 1px solid var(--app-border);
  border-radius: 10px;
  background: var(--app-bg-soft);
  color: var(--app-text-primary);
  cursor: pointer;
  text-align: left;
}
.library-card:hover {
  border-color: color-mix(in srgb, var(--app-accent) 55%, var(--app-border));
  background: color-mix(in srgb, var(--app-accent) 8%, var(--app-bg-soft));
}
.library-card img {
  width: 76px;
  height: 76px;
  object-fit: cover;
  border-radius: 8px;
  background: var(--app-bg);
}
.library-info {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.library-info b,
.library-info span,
.library-info small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.library-info span {
  color: var(--app-text-muted);
  font-size: 12px;
}
.library-info small {
  color: var(--app-text-secondary);
  font-size: 11px;
}
.local-import {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  margin-top: 14px;
  padding: 18px;
  border: 1px solid var(--app-border);
  border-radius: 12px;
  background: var(--app-bg-soft);
}
.local-import div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.local-import span {
  color: var(--app-text-muted);
  font-size: 12px;
}
@media (max-width: 760px) {
  .gen-controls { width: 100%; flex-wrap: wrap; }
  .gen-model, .gen-aspect, .gen-resolution { width: 100%; }
  .library-grid { grid-template-columns: 1fr; }
  .local-import { grid-template-columns: 1fr; }
}
.cols {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;
}
@media (max-width: 1100px) { .cols { grid-template-columns: 1fr; } }
.col {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border); border-radius: var(--r-card);
}
.col-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--app-border);
}
.col-title { display: flex; align-items: center; gap: 7px; font-weight: 700; }
.cnt {
  background: var(--app-bg-soft); border-radius: 10px; padding: 0 8px;
  font-size: 12px; color: var(--app-text-muted);
}
.tg { font-weight: 800; }
.tg.c { color: var(--app-accent); }
.tg.s { color: var(--app-accent-alt); }
.tg.p { color: #ffb454; }
.cards { display: flex; flex-direction: column; gap: 10px; }
.asset {
  display: flex; gap: 10px; padding: 10px;
  background: var(--app-bg-soft); border: 1px solid var(--app-border); border-radius: 12px;
}
.asset.character-card {
  align-items: flex-start;
  padding: 12px;
  border-radius: 14px;
  background: color-mix(in srgb, var(--app-surface) 76%, var(--app-bg-soft));
  box-shadow: 0 10px 24px rgba(0,0,0,.12);
}
.asset.character-card .thumb {
  width: 92px; height: 112px; flex-basis: 92px;
  border-radius: 10px;
}
.thumb {
  position: relative;
  width: 72px; height: 72px; flex: 0 0 72px; border-radius: 8px; overflow: hidden;
  background: var(--app-bg); display: flex; align-items: center; justify-content: center;
}
.thumb-empty { color: var(--app-text-muted); }
.thumb-busy {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, .42);
  backdrop-filter: blur(2px);
}
.code-badge {
  position: absolute; left: 3px; top: 3px;
  font-family: var(--font-mono, monospace); font-size: 10px; font-weight: 700;
  line-height: 1; padding: 2px 4px; border-radius: 4px;
  background: rgba(0, 0, 0, 0.6); color: #fff;
}
.asset-body { flex: 1; min-width: 0; }
.asset-name {
  display: flex; align-items: center; gap: 4px;
  font-weight: 700; margin-bottom: 6px;
}
.asset-desc {
  color: var(--app-text-muted); font-size: 12px; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.prop-line {
  display: grid; grid-template-columns: 38px minmax(0, 1fr); gap: 8px;
  margin-top: 5px; font-size: 12px; line-height: 1.45;
}
.prop-k {
  color: var(--app-text-muted);
}
.prop-v {
  color: var(--app-text-secondary);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.voice-line .prop-v {
  color: var(--app-accent);
  font-weight: 700;
}
.gen-status {
  margin-top: 8px;
  padding: 7px 8px;
  border-radius: 8px;
  border: 1px solid color-mix(in srgb, var(--app-accent) 28%, var(--app-border));
  background: color-mix(in srgb, var(--app-accent) 8%, transparent);
  font-size: 12px;
  color: var(--app-text-secondary);
}
.gen-status.done {
  border-color: color-mix(in srgb, #45d483 36%, var(--app-border));
  background: color-mix(in srgb, #45d483 9%, transparent);
}
.gen-status.error {
  border-color: color-mix(in srgb, #ff6b6b 42%, var(--app-border));
  background: color-mix(in srgb, #ff6b6b 10%, transparent);
}
.gen-status-main {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
}
.gen-status-bar,
.batch-progress {
  height: 4px;
  margin-top: 7px;
  border-radius: 999px;
  overflow: hidden;
  background: color-mix(in srgb, var(--app-border) 55%, transparent);
}
.gen-status-bar span,
.batch-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--app-accent), var(--app-accent-alt));
  transition: width .25s ease;
}
.gen-status-error {
  margin-top: 5px;
  color: #ff8b8b;
  word-break: break-word;
}
.batch-sub {
  margin-left: 10px;
  color: var(--app-text-muted);
  font-size: 12px;
}
.asset-actions { display: flex; gap: 6px; margin-top: 8px; }
.resolver {
  margin-top: 18px;
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border); border-radius: var(--r-card);
}
.resolver-desc { color: var(--app-text-secondary); font-size: 13px; margin-bottom: 12px; }
.resolver-desc code, .mono { font-family: var(--font-mono, monospace); }
.resolved { margin-top: 14px; }
.resolved-row { display: flex; gap: 14px; margin-bottom: 10px; }
.rk { flex: 0 0 84px; color: var(--app-text-muted); font-size: 13px; }
.rv { flex: 1; }
.rv.mono {
  background: var(--app-bg-soft); padding: 8px 12px; border-radius: 8px;
  border: 1px solid var(--app-border); line-height: 1.6; word-break: break-all;
}
.muted { color: var(--app-text-muted); }
</style>
