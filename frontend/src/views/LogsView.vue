<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  NButton, NIcon, NSelect, NInput, NTag, NPopconfirm, NEmpty, useMessage,
} from 'naive-ui'
import {
  RefreshOutline, PlayOutline, PauseOutline, DownloadOutline,
  TrashOutline, CopyOutline, ArrowDownOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'
import { useTasksStore } from '../stores/tasks'
import { useProjectStore } from '../stores/project'

const message = useMessage()
const tasks = useTasksStore()
const projects = useProjectStore()

const logs = ref([])              // {id, ts, level, name, msg}
const sinceId = ref(0)
const autoRefresh = ref(true)
const autoScroll = ref(true)
const levelFilter = ref('ALL')
const search = ref('')
const scroller = ref(null)
let timer = null

const LEVELS = [
  { label: '全部级别', value: 'ALL' },
  { label: 'INFO+', value: 'INFO' },
  { label: 'WARNING+', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
]
const RANK = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 }

const filtered = computed(() => {
  const min = levelFilter.value === 'ALL' ? -1 : (RANK[levelFilter.value] ?? -1)
  const kw = search.value.trim().toLowerCase()
  return logs.value.filter((e) => {
    if ((RANK[e.level] ?? 1) < min) return false
    if (kw && !(`${e.name} ${e.msg}`.toLowerCase().includes(kw))) return false
    return true
  })
})

// ── 运行状态 (work-progress summary) ──
const status = computed(() => {
  const items = []
  items.push({ k: '当前项目', v: projects.current?.name || '—' })
  const dec = tasks.decomposeStatus
  items.push({
    k: '剧本解析',
    v: dec === 'running' ? `进行中 ${tasks.decomposePercent}% (${tasks.decomposeDone}/${tasks.decomposeTotal})`
      : dec === 'error' ? `失败：${tasks.decomposeError || '未知'}` : dec === 'done' ? '完成' : '空闲',
    tone: dec === 'running' ? 'info' : dec === 'error' ? 'error' : dec === 'done' ? 'success' : 'default',
  })
  const an = tasks.analyzeStatus
  items.push({
    k: '全局分析',
    v: an === 'running' ? '进行中' : an === 'error' ? `失败：${tasks.analyzeError || '未知'}` : an === 'done' ? '完成' : '空闲',
    tone: an === 'running' ? 'info' : an === 'error' ? 'error' : an === 'done' ? 'success' : 'default',
  })
  let total = 0, done = 0, err = 0
  for (const b of tasks.batches) { total += b.total || 0; done += b.done || 0; err += b.error || 0 }
  items.push({
    k: '批量生成',
    v: tasks.batchActive ? `运行中 ${done}/${total}${err ? `，失败 ${err}` : ''}`
      : total ? `空闲（最近 ${done}/${total}${err ? `，失败 ${err}` : ''}）` : '空闲',
    tone: tasks.batchActive ? 'info' : err ? 'warning' : 'default',
  })
  return items
})

function fmtTs(ts) {
  const d = new Date(ts * 1000)
  const p = (n, w = 2) => String(n).padStart(w, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`
}

function tagType(level) {
  return level === 'ERROR' || level === 'CRITICAL' ? 'error'
    : level === 'WARNING' ? 'warning' : level === 'DEBUG' ? 'default' : 'info'
}

async function tick() {
  try {
    const fresh = await api.logs(sinceId.value)
    if (fresh.length) {
      logs.value.push(...fresh)
      sinceId.value = fresh[fresh.length - 1].id
      if (logs.value.length > 2000) logs.value.splice(0, logs.value.length - 2000)
      if (autoScroll.value) scrollToBottom()
    }
  } catch { /* transient — keep last-known logs */ }
}

function scrollToBottom() {
  nextTick(() => {
    const el = scroller.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function onScroll() {
  const el = scroller.value
  if (!el) return
  // Disable auto-scroll when the user scrolls up; re-enable near the bottom.
  autoScroll.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

function toggleAuto() {
  autoRefresh.value = !autoRefresh.value
  if (autoRefresh.value) { start(); message.info('已恢复自动刷新') }
  else { stop(); message.info('已暂停自动刷新') }
}

function start() { if (!timer) timer = setInterval(tick, 1000) }
function stop() { if (timer) { clearInterval(timer); timer = null } }

async function doExport() {
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
  const fname = `batch-studio-logs-${stamp}.log`
  const papi = window.pywebview && window.pywebview.api
  try {
    if (papi && papi.save_text_file) {
      // Desktop: WebView2 drops blob downloads, so write via the Python API.
      const text = await (await fetch('/api/logs/export')).text()
      const r = await papi.save_text_file(fname, text)
      if (r && r.ok) message.success('日志已导出：' + r.path)
      else message.error('导出失败：' + ((r && r.error) || '未知'))
    } else {
      // Browser dev: normal attachment download.
      await api.exportLogs()
      message.success('日志已导出（.log 文件）')
    }
  } catch (e) { message.error('导出失败：' + e.message) }
}

async function doClear() {
  try {
    await api.clearLogs()
    logs.value = []
    sinceId.value = 0
    message.success('日志已清空')
  } catch (e) { message.error('清空失败：' + e.message) }
}

async function copyAll() {
  const text = filtered.value
    .map((e) => `${fmtTs(e.ts)} [${e.level}] ${e.name}: ${e.msg}`).join('\n')
  try { await navigator.clipboard.writeText(text); message.success(`已复制 ${filtered.value.length} 条`) }
  catch { message.error('复制失败（剪贴板不可用）') }
}

onMounted(async () => {
  if (!projects.projects.length) { try { await projects.refreshList() } catch { /* ignore */ } }
  await tick()
  scrollToBottom()
  if (autoRefresh.value) start()
})
onUnmounted(stop)
</script>

<template>
  <div class="logs-view">
    <PageHeader title="日志调试" subtitle="查看工作进度与运行状态，导出日志以便联系开发者排查 BUG">
      <template #actions>
        <n-button size="small" @click="toggleAuto">
          <template #icon><n-icon :component="autoRefresh ? PauseOutline : PlayOutline" /></template>
          {{ autoRefresh ? '暂停' : '继续' }}
        </n-button>
        <n-button size="small" @click="tick">
          <template #icon><n-icon :component="RefreshOutline" /></template>
          刷新
        </n-button>
        <n-button size="small" @click="copyAll">
          <template #icon><n-icon :component="CopyOutline" /></template>
          复制
        </n-button>
        <n-button size="small" type="primary" @click="doExport">
          <template #icon><n-icon :component="DownloadOutline" /></template>
          导出日志
        </n-button>
        <n-popconfirm @positive-click="doClear">
          <template #trigger>
            <n-button size="small" tertiary type="error">
              <template #icon><n-icon :component="TrashOutline" /></template>
              清空
            </n-button>
          </template>
          确认清空所有日志缓冲？
        </n-popconfirm>
      </template>
    </PageHeader>

    <!-- 运行状态 -->
    <div class="status-grid">
      <div v-for="s in status" :key="s.k" class="status-card">
        <div class="status-k">{{ s.k }}</div>
        <div class="status-v" :class="'tone-' + (s.tone || 'default')">{{ s.v }}</div>
      </div>
    </div>

    <!-- 过滤工具条 -->
    <div class="toolbar">
      <n-select v-model:value="levelFilter" :options="LEVELS" size="small" style="width: 140px" />
      <n-input v-model:value="search" size="small" clearable placeholder="搜索日志内容 / 模块名…" style="max-width: 360px" />
      <span class="count">{{ filtered.length }} / {{ logs.length }} 条</span>
      <n-button v-if="!autoScroll" size="tiny" tertiary @click="autoScroll = true; scrollToBottom()">
        <template #icon><n-icon :component="ArrowDownOutline" /></template>
        跳到底部
      </n-button>
    </div>

    <!-- 日志列表 -->
    <div ref="scroller" class="log-box" @scroll="onScroll">
      <n-empty v-if="!filtered.length" description="暂无日志" style="margin-top: 60px" />
      <div v-for="e in filtered" :key="e.id" class="log-line" :class="'lv-' + e.level">
        <span class="ts">{{ fmtTs(e.ts) }}</span>
        <n-tag size="tiny" :type="tagType(e.level)" :bordered="false" class="lv-tag">{{ e.level }}</n-tag>
        <span class="name">{{ e.name }}</span>
        <span class="msg">{{ e.msg }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.logs-view { display: flex; flex-direction: column; height: 100%; }
.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.status-card {
  padding: 12px 14px;
  background: var(--app-surface);
  border: 1px solid var(--app-border);
  border-radius: 12px;
}
.status-k { font-size: 12px; color: var(--app-text-muted); margin-bottom: 6px; }
.status-v { font-size: 14px; font-weight: 600; color: var(--app-text-primary); word-break: break-all; }
.tone-info { color: var(--app-accent); }
.tone-success { color: #36ad6a; }
.tone-warning { color: #f0a020; }
.tone-error { color: #e88080; }

.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.count { font-size: 12px; color: var(--app-text-muted); margin-left: auto; }

.log-box {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  background: color-mix(in srgb, var(--app-bg-soft) 70%, transparent);
  border: 1px solid var(--app-border);
  border-radius: 12px;
  padding: 10px 12px;
  font-family: 'Cascadia Code', Consolas, Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.7;
}
.log-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 1px 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.log-line.lv-ERROR, .log-line.lv-CRITICAL { background: rgba(232, 128, 128, 0.08); border-radius: 4px; }
.log-line.lv-WARNING { background: rgba(240, 160, 32, 0.07); border-radius: 4px; }
.ts { color: var(--app-text-muted); flex-shrink: 0; }
.lv-tag { flex-shrink: 0; }
.name { color: var(--app-text-secondary); flex-shrink: 0; }
.msg { color: var(--app-text-primary); flex: 1; }
</style>
