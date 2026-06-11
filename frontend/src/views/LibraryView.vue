<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NSelect, NButton, NIcon, NEmpty, NTag, NModal, NCard, NProgress,
  NPopconfirm, NScrollbar, useMessage,
} from 'naive-ui'
import {
  RefreshOutline, PlayOutline, PauseOutline, TrashOutline, ReloadOutline,
  ImageOutline, VideocamOutline, AlbumsOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'

const router = useRouter()
const message = useMessage()
const store = useProjectStore()

const batches = ref([])          // summaries
const current = ref(null)        // full batch detail
const loading = ref(false)
const preview = ref(null)        // { url, video }
let poll = null

onMounted(async () => {
  await store.refreshList()
  if (!store.current && store.projects.length) await store.select(store.projects[0].id)
  await loadBatches()
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
  if (!store.current) { batches.value = []; return }
  loading.value = true
  try { batches.value = await api.listBatches(store.current.id) }
  catch (e) { message.error(e.message) }
  finally { loading.value = false }
}

async function selectBatch(bid) {
  current.value = await api.getBatch(store.current.id, bid)
  if (['running', 'pending'].includes(current.value.status)) startPolling()
}

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

const progress = computed(() => {
  if (!current.value?.tasks?.length) return 0
  const d = current.value.tasks.filter((t) => ['done', 'error', 'skipped'].includes(t.status)).length
  return Math.round((d / current.value.tasks.length) * 100)
})
const counts = computed(() => {
  const c = { pending: 0, running: 0, done: 0, error: 0, skipped: 0, dismissed: 0 }
  current.value?.tasks?.forEach((t) => {
    const s = viewStatus(t)
    c[s] = (c[s] || 0) + 1
  })
  return c
})
const currentBatchStatus = computed(() => {
  if (current.value?.status === 'error' && !counts.value.error && counts.value.dismissed) return 'dismissed'
  return current.value?.status || 'pending'
})

async function start() {
  await api.startBatch(store.current.id, current.value.id)
  current.value.status = 'running'; startPolling()
}
async function pause() {
  await api.pauseBatch(store.current.id, current.value.id)
  message.info('已请求暂停，正在结束进行中的任务…')
}
async function retry() {
  await api.retryBatch(store.current.id, current.value.id)
  current.value.status = 'running'; startPolling()
}
async function removeBatch(b) {
  await api.deleteBatch(store.current.id, b.id)
  if (current.value?.id === b.id) current.value = null
  await loadBatches()
  message.success('批次已删除')
}

function isVideoFile(f) { return /\.(mp4|webm|mov|mkv)$/i.test(f || '') }
function taskUrl(t) {
  return t.result?.filename ? api.outputUrl(store.current.id, current.value.id, t.result.filename) : null
}
function openPreview(t) {
  const url = taskUrl(t)
  if (url) preview.value = { url, video: isVideoFile(t.result.filename) }
}
function statusType(s) {
  return { done: 'success', error: 'error', running: 'warning', pending: 'default', paused: 'warning', skipped: 'default', dismissed: 'default' }[s] || 'default'
}
function statusLabel(s) {
  if (s === 'dismissed') return '已清除'
  if (s === 'skipped') return '已跳过'
  return { done: '完成', error: '失败', running: '生成中', pending: '等待', paused: '已暂停' }[s] || s
}
function isDismissedError(t) {
  return t?.status === 'error' && !!t?.error_dismissed_at
}
function viewStatus(t) {
  return isDismissedError(t) ? 'dismissed' : (t?.status || 'pending')
}
function batchListStatus(b) {
  if (b?.status === 'error' && !Number(b?.error || 0)) return 'dismissed'
  return b?.status || 'pending'
}
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
</script>

<template>
  <div>
    <PageHeader title="批次库" subtitle="历史批次 · 产物归档 · 全链路编号追溯（剧本/尾帧/站位图/视频同前缀对齐）">
      <template #actions>
        <n-select :value="store.currentId" :options="projectOptions" placeholder="选择项目"
          style="width: 240px" @update:value="onSelectProject" />
        <n-button size="small" quaternary :disabled="!store.current" @click="loadBatches">
          <template #icon><n-icon :component="RefreshOutline" /></template>刷新
        </n-button>
      </template>
    </PageHeader>

    <n-empty v-if="!store.current" description="请先在「项目导入」导入一个项目" style="margin-top: 80px">
      <template #extra><n-button size="small" @click="router.push('/import')">去导入</n-button></template>
    </n-empty>

    <div v-else class="layout">
      <!-- left: batch list -->
      <div class="side">
        <div class="side-head">批次记录（{{ batches.length }}）</div>
        <n-empty v-if="!batches.length" description="暂无批次" class="side-empty">
          <template #extra><n-button size="tiny" @click="router.push('/worktable')">去工作台生成</n-button></template>
        </n-empty>
        <n-scrollbar v-else style="max-height: calc(100vh - 220px)">
          <button v-for="b in batches" :key="b.id" class="bcard"
            :class="{ active: current?.id === b.id }" @click="selectBatch(b.id)">
            <div class="bc-top">
              <n-icon :component="b.kind === 'video' ? VideocamOutline : ImageOutline" :size="15" />
              <span class="bc-name">{{ b.name }}</span>
              <n-tag size="tiny" :type="statusType(batchListStatus(b))" :bordered="false">{{ statusLabel(batchListStatus(b)) }}</n-tag>
            </div>
            <div class="bc-meta">
              <span>{{ b.done }}/{{ b.total }} 完成</span>
              <span v-if="b.error" class="bc-err">· {{ b.error }} 失败</span>
              <span v-if="b.skipped">· {{ b.skipped }} 跳过</span>
              <span class="bc-time">{{ fmtTime(b.created_at) }}</span>
            </div>
            <div class="bc-bar"><span :style="{ width: (b.total ? Math.round(((b.done || 0) + (b.error || 0) + (b.skipped || 0)) / b.total * 100) : 0) + '%' }" /></div>
          </button>
        </n-scrollbar>
      </div>

      <!-- right: detail -->
      <div class="detail">
        <n-empty v-if="!current" description="选择左侧一个批次查看产物与任务" class="detail-empty">
          <template #icon><n-icon :component="AlbumsOutline" :size="40" /></template>
        </n-empty>
        <template v-else>
          <div class="d-head glass">
            <div class="d-title">
              <n-icon :component="current.kind === 'video' ? VideocamOutline : ImageOutline" :size="18" />
              <span>{{ current.name }}</span>
              <n-tag size="small" :type="statusType(currentBatchStatus)" :bordered="false">{{ statusLabel(currentBatchStatus) }}</n-tag>
            </div>
            <div class="d-actions">
              <n-button v-if="['pending','paused','error'].includes(current.status)" size="small" type="primary" @click="start">
                <template #icon><n-icon :component="PlayOutline" /></template>开始
              </n-button>
              <n-button v-if="current.status === 'running'" size="small" @click="pause">
                <template #icon><n-icon :component="PauseOutline" /></template>暂停
              </n-button>
              <n-button v-if="counts.error" size="small" @click="retry">
                <template #icon><n-icon :component="ReloadOutline" /></template>重跑失败（{{ counts.error }}）
              </n-button>
              <n-popconfirm @positive-click="removeBatch(current)">
                <template #trigger>
                  <n-button size="small" quaternary type="error">
                    <template #icon><n-icon :component="TrashOutline" /></template>删除
                  </n-button>
                </template>
                删除该批次记录？产物文件一并移除。
              </n-popconfirm>
            </div>
            <div class="d-progress">
              <n-progress type="line" :percentage="progress" :height="8" :border-radius="6"
                :status="counts.error ? 'error' : 'success'" />
              <span class="d-counts">完成 {{ counts.done }} · 生成中 {{ counts.running }} · 等待 {{ counts.pending }} · 跳过 {{ counts.skipped }}<template v-if="counts.dismissed"> · 已清除 {{ counts.dismissed }}</template> · 失败 {{ counts.error }}</span>
            </div>
          </div>

          <div class="grid">
            <div v-for="t in current.tasks" :key="t.id" class="cell glass">
              <div class="cell-media" :class="{ clickable: taskUrl(t) }" @click="openPreview(t)">
                <video v-if="taskUrl(t) && isVideoFile(t.result.filename)" :src="taskUrl(t)" muted class="media" />
                <img v-else-if="taskUrl(t)" :src="taskUrl(t)" class="media" />
                <div v-else class="media ph" :class="viewStatus(t)">
                  <n-icon :component="current.kind === 'video' ? VideocamOutline : ImageOutline" :size="22" />
                  <span>{{ statusLabel(viewStatus(t)) }}</span>
                </div>
              </div>
              <div class="cell-foot">
                <span class="cell-no">{{ t.shot_no || `#${t.index + 1}` }}</span>
                <n-tag size="tiny" :type="statusType(viewStatus(t))" :bordered="false">{{ statusLabel(viewStatus(t)) }}</n-tag>
              </div>
              <div class="cell-prompt" :title="t.prompt">{{ t.prompt || '（无提示词）' }}</div>
              <div v-if="t.error && !isDismissedError(t)" class="cell-error" :title="t.error">⚠ {{ t.error }}</div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <n-modal :show="!!preview" @update:show="(v) => { if (!v) preview = null }">
      <n-card style="width: auto; max-width: 90vw" :bordered="false" role="dialog">
        <video v-if="preview?.video" :src="preview.url" controls autoplay style="max-width: 86vw; max-height: 80vh; display:block" />
        <img v-else :src="preview?.url" style="max-width: 86vw; max-height: 80vh; display:block" />
      </n-card>
    </n-modal>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 20px; align-items: start; }
.side-head { font-size: 13px; color: var(--app-text-secondary); margin-bottom: 10px; padding: 0 4px; }
.side-empty, .detail-empty { margin-top: 40px; }
.bcard {
  width: 100%; text-align: left; padding: 12px 14px; margin-bottom: 10px;
  border: 1px solid var(--app-border); border-radius: var(--r-card);
  background: var(--app-surface); cursor: pointer; transition: border-color 0.15s, transform 0.12s;
}
.bcard:hover { border-color: var(--app-border-strong); transform: translateY(-1px); }
.bcard.active { border-color: var(--app-accent); box-shadow: 0 0 0 2px var(--app-accent-soft); }
.bc-top { display: flex; align-items: center; gap: 8px; color: var(--app-text-primary); }
.bc-name { flex: 1; font-weight: 600; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bc-meta { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--app-text-muted); margin-top: 7px; }
.bc-err { color: var(--app-error); }
.bc-time { margin-left: auto; }
.bc-bar { height: 4px; border-radius: 4px; background: var(--app-border); margin-top: 9px; overflow: hidden; }
.bc-bar span { display: block; height: 100%; background: var(--app-accent); border-radius: 4px; }

.d-head { padding: 16px 18px; margin-bottom: 18px; }
.d-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; }
.d-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.d-progress { margin-top: 14px; }
.d-counts { display: block; margin-top: 7px; font-size: 12px; color: var(--app-text-muted); }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; }
.cell { padding: 10px; }
.cell-media {
  height: 150px; border-radius: 10px; overflow: hidden; display: grid; place-items: center;
  background: var(--app-bg-soft); border: 1px solid var(--app-border);
}
.cell-media.clickable { cursor: zoom-in; }
.media { width: 100%; height: 100%; object-fit: cover; }
.media.ph { display: flex; flex-direction: column; gap: 6px; color: var(--app-text-muted); font-size: 12px; }
.media.ph.error { color: var(--app-error); }
.media.ph.running { color: var(--app-warning); }
.cell-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; }
.cell-no { font-weight: 600; font-size: 13px; color: var(--app-accent); }
.cell-prompt {
  margin-top: 6px; font-size: 12px; color: var(--app-text-secondary); line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.cell-error {
  margin-top: 6px; font-size: 11px; color: var(--app-error);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
</style>
