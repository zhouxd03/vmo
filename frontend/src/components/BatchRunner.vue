<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NSelect, NButton, NIcon, NCard, NEmpty, NInput, NInputNumber, NRadioGroup,
  NRadioButton, NProgress, NTag, NImage, NPopconfirm, NGrid, NGi, NTooltip,
  NScrollbar, NSwitch, useMessage,
} from 'naive-ui'
import {
  PlayOutline, PauseOutline, RefreshOutline, TrashOutline, AddOutline,
  DocumentTextOutline, ListOutline,
} from '@vicons/ionicons5'
import PageHeader from './PageHeader.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'

const props = defineProps({ kind: { type: String, default: 'image' } })
const isVideo = computed(() => props.kind === 'video')

const router = useRouter()
const message = useMessage()
const store = useProjectStore()

const batchList = ref([])
const current = ref(null)
const jobId = ref(null)
let poll = null

const viduImageSizeOptions = ['16:9', '9:16', '1:1', '4:3', '3:4']
  .flatMap((aspect) => ['1080p', '2K', '4K'].map((res) => ({
    label: `Vidu ${aspect} ${res}`,
    value: `${aspect}@${res}`,
  })))
const batchImageSizeOptions = [
  { label: '1024x1024', value: '1024x1024' },
  { label: '1024x1536', value: '1024x1536' },
  { label: '1536x1024', value: '1536x1024' },
  ...viduImageSizeOptions,
]
const aspectOptions = ['16:9', '9:16', '1:1', '4:3', '3:4'].map((v) => ({ label: v, value: v }))

// ── create form ──
const form = ref({
  name: '', source: 'shots', concurrency: 2, max_attempts: 3,
  prompts: '', size: '1024x1024', duration: 10, aspect_ratio: '16:9', resolution: '720p',
  continuity: false, continuity_mode: 'auto', use_llm: false, ai_review: true, llm_model: '',
})

onMounted(async () => {
  await store.refreshList()
  if (!store.current && store.projects.length) await store.select(store.projects[0].id)
  if (store.current) await loadBatches()
})
onUnmounted(() => { if (poll) clearInterval(poll) })

const projectOptions = computed(() =>
  store.projects.map((p) => ({ label: `${p.name}（${p.episode_count || 1}集·${p.shot_count || 0}镜）`, value: p.id })))

async function onSelectProject(pid) {
  await store.select(pid)
  current.value = null
  await loadBatches()
}

async function loadBatches() {
  if (!store.current) return
  batchList.value = await api.listBatches(store.current.id)
}

async function createBatch() {
  if (!store.current) return
  const body = {
    kind: props.kind, name: form.value.name, source: form.value.source,
    concurrency: form.value.concurrency, max_attempts: form.value.max_attempts,
    params: isVideo.value
      ? { duration: form.value.duration, aspect_ratio: form.value.aspect_ratio, resolution: form.value.resolution }
      : { size: form.value.size },
  }
  if (isVideo.value && form.value.continuity) {
    Object.assign(body.params, {
      continuity: true, mode: form.value.continuity_mode,
      decision_llm: form.value.use_llm, ai_review: form.value.ai_review,
      llm_model: form.value.llm_model || undefined,
    })
  }
  if (form.value.source === 'manual') {
    body.prompts = form.value.prompts.split('\n').map((s) => s.trim()).filter(Boolean)
    if (!body.prompts.length) { message.warning('请至少输入一行提示词'); return }
  }
  try {
    const b = await api.createBatch(store.current.id, body)
    message.success(`已创建批次（${b.tasks.length} 个任务）`)
    await loadBatches()
    await selectBatch(b.id)
  } catch (e) { message.error(e.message) }
}

async function selectBatch(bid) {
  current.value = await api.getBatch(store.current.id, bid)
}

const progress = computed(() => {
  if (!current.value) return 0
  const t = current.value.tasks.length || 1
  const d = current.value.tasks.filter((x) => x.status === 'done').length
  return Math.round((d / t) * 100)
})
const counts = computed(() => {
  const c = { pending: 0, running: 0, done: 0, error: 0 }
  current.value?.tasks.forEach((t) => { c[t.status] = (c[t.status] || 0) + 1 })
  return c
})

function startPolling() {
  if (poll) clearInterval(poll)
  poll = setInterval(async () => {
    if (!current.value) return
    current.value = await api.getBatch(store.current.id, current.value.id)
    if (!['running', 'pending'].includes(current.value.status)) {
      clearInterval(poll); poll = null
      await loadBatches()
    }
  }, 1500)
}

async function start() {
  const r = await api.startBatch(store.current.id, current.value.id)
  jobId.value = r.job_id
  current.value.status = 'running'
  startPolling()
}
async function pause() {
  await api.pauseBatch(store.current.id, current.value.id)
  message.info('已请求暂停，正在结束进行中的任务…')
}
async function retry() {
  await api.retryBatch(store.current.id, current.value.id)
  current.value.status = 'running'
  startPolling()
}
async function removeBatch(b) {
  await api.deleteBatch(store.current.id, b.id)
  if (current.value?.id === b.id) current.value = null
  await loadBatches()
}

function tagType(s) {
  return { done: 'success', error: 'error', running: 'warning', pending: 'default' }[s] || 'default'
}
function tagLabel(s) {
  return { done: '完成', error: '失败', running: '生成中', pending: '等待' }[s] || s
}
function thumbUrl(t) {
  return t.result?.filename ? api.outputUrl(store.current.id, current.value.id, t.result.filename) : null
}
</script>

<template>
  <div>
    <PageHeader
      :title="isVideo ? '批量生视频' : '批量生图'"
      :subtitle="isVideo ? '分镜 → 视频队列（并发 + 重试 + 断点续跑），文件名与分镜编号一致' : '分镜/手动 → 图像队列（并发 + 重试 + 断点续跑）'">
      <template #actions>
        <n-select :value="store.currentId" :options="projectOptions" placeholder="选择项目"
          style="width: 260px" @update:value="onSelectProject" />
      </template>
    </PageHeader>

    <n-empty v-if="!store.current" description="请先在「项目导入」导入一个项目">
      <template #extra><n-button size="small" @click="router.push('/import')">去导入</n-button></template>
    </n-empty>

    <div v-else class="layout">
      <!-- left: create + list -->
      <div class="side">
        <n-card title="新建批次" :bordered="false" class="panel">
          <div class="field">
            <label>来源</label>
            <n-radio-group v-model:value="form.source" size="small">
              <n-radio-button value="shots">从分镜</n-radio-button>
              <n-radio-button value="manual">手动多行</n-radio-button>
            </n-radio-group>
          </div>
          <div v-if="form.source === 'manual'" class="field">
            <label>提示词（每行一个任务，支持 @人物/#场景/$道具）</label>
            <n-input v-model:value="form.prompts" type="textarea"
              :autosize="{ minRows: 3, maxRows: 7 }" placeholder="每行一个生成任务…" />
          </div>
          <div v-else class="hint">从已拆解的分镜自动建任务（含 @/#/$ 参考图注入）。</div>

          <div class="field"><label>批次名称</label>
            <n-input v-model:value="form.name" placeholder="可选" />
          </div>
          <div class="row">
            <div class="field"><label>并发数</label>
              <n-input-number v-model:value="form.concurrency" :min="1" :max="8" size="small" style="width: 100%" />
            </div>
            <div class="field"><label>失败重试</label>
              <n-input-number v-model:value="form.max_attempts" :min="1" :max="6" size="small" style="width: 100%" />
            </div>
          </div>
          <template v-if="isVideo">
            <div class="row">
              <div class="field"><label>时长(秒)</label>
                <n-input-number v-model:value="form.duration" :min="1" :max="60" size="small" style="width: 100%" />
              </div>
              <div class="field"><label>画幅</label>
                <n-select v-model:value="form.aspect_ratio" size="small"
                  :options="aspectOptions" />
              </div>
            </div>
            <div class="field continuity-box">
              <div class="opt"><label>启用连续性引擎</label>
                <n-switch v-model:value="form.continuity" size="small" /></div>
              <div class="hint">开启后批次串行执行，逐镜中继尾帧/站位图保证连贯（见「分镜流水线」可视化）。</div>
              <template v-if="form.continuity">
                <div class="field"><label>衔接模式</label>
                  <n-radio-group v-model:value="form.continuity_mode" size="small">
                    <n-radio-button value="manual">手动确认</n-radio-button>
                    <n-radio-button value="auto">全自动</n-radio-button>
                  </n-radio-group>
                </div>
                <div class="opt"><label>LLM 升级决策</label>
                  <n-switch v-model:value="form.use_llm" size="small" /></div>
                <div v-if="form.continuity_mode === 'auto'" class="opt"><label>AI 复核闸门</label>
                  <n-switch v-model:value="form.ai_review" size="small" /></div>
                <div class="field"><label>LLM 模型名（可选）</label>
                  <n-input v-model:value="form.llm_model" size="small" placeholder="留空用默认 LLM 凭据" />
                </div>
              </template>
            </div>
          </template>
          <div v-else class="field"><label>尺寸</label>
            <n-select v-model:value="form.size" size="small"
              :options="batchImageSizeOptions" />
          </div>

          <n-button type="primary" block @click="createBatch">
            <template #icon><n-icon :component="AddOutline" /></template>创建批次
          </n-button>
        </n-card>

        <n-card title="批次列表" :bordered="false" class="panel">
          <n-empty v-if="!batchList.length" description="暂无批次" size="small" />
          <div v-else class="blist">
            <div v-for="b in batchList" :key="b.id" class="bitem"
              :class="{ active: current?.id === b.id }" @click="selectBatch(b.id)">
              <div class="bitem-main">
                <div class="bname">{{ b.name }}</div>
                <div class="bmeta">
                  <n-tag size="tiny" :type="tagType(b.status)" :bordered="false">{{ b.status }}</n-tag>
                  <span>{{ b.done }}/{{ b.total }}</span>
                  <span v-if="b.error" class="err">✗{{ b.error }}</span>
                </div>
              </div>
              <n-popconfirm @positive-click="removeBatch(b)">
                <template #trigger>
                  <n-button size="tiny" quaternary type="error" @click.stop>
                    <template #icon><n-icon :component="TrashOutline" /></template>
                  </n-button>
                </template>
                删除批次「{{ b.name }}」？
              </n-popconfirm>
            </div>
          </div>
        </n-card>
      </div>

      <!-- right: detail -->
      <div class="detail">
        <n-empty v-if="!current" description="选择或新建一个批次" style="margin-top: 80px">
          <template #icon><n-icon :component="ListOutline" /></template>
        </n-empty>
        <template v-else>
          <n-card :bordered="false" class="panel">
            <div class="dhead">
              <div>
                <div class="dtitle">{{ current.name }}
                  <n-tag size="small" :type="tagType(current.status)" :bordered="false">{{ current.status }}</n-tag>
                </div>
                <div class="dsub">{{ isVideo ? '视频' : '图像' }}批次 · 并发 {{ current.concurrency }} · {{ current.tasks.length }} 任务</div>
              </div>
              <div class="dctrl">
                <n-button v-if="!['running'].includes(current.status)" type="primary" size="small" @click="start">
                  <template #icon><n-icon :component="PlayOutline" /></template>
                  {{ counts.done ? '继续' : '开始' }}
                </n-button>
                <n-button v-else size="small" @click="pause">
                  <template #icon><n-icon :component="PauseOutline" /></template>暂停
                </n-button>
                <n-button v-if="counts.error" size="small" type="warning" ghost @click="retry">
                  <template #icon><n-icon :component="RefreshOutline" /></template>重试失败({{ counts.error }})
                </n-button>
              </div>
            </div>
            <n-progress type="line" :percentage="progress" :indicator-placement="'inside'"
              :status="current.status === 'error' ? 'error' : 'success'" style="margin-top: 12px" />
            <div class="legend">
              <span>完成 {{ counts.done }}</span><span>生成中 {{ counts.running }}</span>
              <span>等待 {{ counts.pending }}</span><span class="err">失败 {{ counts.error }}</span>
            </div>
          </n-card>

          <n-card title="任务" :bordered="false" class="panel">
            <n-grid :cols="isVideo ? 3 : 4" :x-gap="12" :y-gap="12">
              <n-gi v-for="t in current.tasks" :key="t.id">
                <div class="task" :class="t.status">
                  <div class="task-thumb">
                    <n-image v-if="thumbUrl(t) && !isVideo" :src="thumbUrl(t)" object-fit="cover" width="100%" />
                    <video v-else-if="thumbUrl(t) && isVideo" :src="thumbUrl(t)" controls width="100%" />
                    <div v-else class="task-ph">{{ tagLabel(t.status) }}</div>
                  </div>
                  <div class="task-info">
                    <span class="sn">{{ t.shot_no }}</span>
                    <n-tag size="tiny" :type="tagType(t.status)" :bordered="false">{{ tagLabel(t.status) }}</n-tag>
                  </div>
                  <n-tooltip v-if="t.prompt" trigger="hover">
                    <template #trigger><div class="task-prompt">{{ t.prompt }}</div></template>
                    {{ t.prompt }}
                  </n-tooltip>
                  <div v-if="t.error" class="task-err">{{ t.error }}</div>
                </div>
              </n-gi>
            </n-grid>
          </n-card>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 340px 1fr; gap: 16px; }
@media (max-width: 1000px) { .layout { grid-template-columns: 1fr; } }
.panel {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border); border-radius: var(--r-card); margin-bottom: 16px;
}
.side { min-width: 0; }
.field { margin-bottom: 12px; }
.field > label { display: block; font-size: 12px; color: var(--app-text-muted); margin-bottom: 5px; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.hint { font-size: 12px; color: var(--app-text-muted); margin-bottom: 12px; }
.continuity-box { padding: 10px; border: 1px dashed var(--app-border); border-radius: 10px; }
.opt {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 12px;
  min-height: 32px; padding: 6px 8px; margin-bottom: 8px;
  border: 1px solid var(--app-border); border-radius: 8px; background: var(--app-bg-soft);
}
.opt > label {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 12px; color: var(--app-text-muted);
}
.blist { display: flex; flex-direction: column; gap: 8px; }
.bitem {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px; border-radius: 10px; background: var(--app-bg-soft);
  border: 1px solid var(--app-border); cursor: pointer; transition: border-color .15s;
}
.bitem:hover { border-color: var(--app-accent); }
.bitem.active { border-color: var(--app-accent); box-shadow: 0 0 0 1px var(--app-accent) inset; }
.bname { font-weight: 600; font-size: 13px; }
.bmeta { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--app-text-muted); margin-top: 3px; }
.err { color: #ff6b6b; }
.dhead { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.dtitle { font-weight: 700; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.dsub { color: var(--app-text-muted); font-size: 12px; margin-top: 4px; }
.dctrl { display: flex; gap: 8px; }
.legend { display: flex; gap: 16px; font-size: 12px; color: var(--app-text-muted); margin-top: 10px; }
.task {
  border: 1px solid var(--app-border); border-radius: 10px; overflow: hidden;
  background: var(--app-bg-soft);
}
.task.error { border-color: color-mix(in srgb, #ff6b6b 50%, var(--app-border)); }
.task.done { border-color: color-mix(in srgb, var(--app-accent) 40%, var(--app-border)); }
.task-thumb {
  aspect-ratio: 1 / 1; background: var(--app-bg); display: flex; align-items: center; justify-content: center;
}
.task-ph { color: var(--app-text-muted); font-size: 13px; }
.task-info { display: flex; align-items: center; justify-content: space-between; padding: 7px 9px 4px; }
.sn { font-family: var(--font-mono, monospace); font-size: 12px; }
.task-prompt {
  padding: 0 9px 8px; font-size: 11px; color: var(--app-text-muted); line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.task-err { padding: 0 9px 8px; font-size: 11px; color: #ff6b6b; }
</style>
