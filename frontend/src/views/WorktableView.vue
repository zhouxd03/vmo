<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NSelect, NButton, NIcon, NEmpty, NInput, NTag, NImage, NSwitch, NTooltip,
  NModal, NCard, NSpace, NInputNumber, NScrollbar, NBadge, NPopover, NDropdown,
  NDrawer, NDrawerContent, NProgress, NSpin,
  useMessage,
} from 'naive-ui'
import {
  ImageOutline, VideocamOutline, LockClosedOutline, LockOpenOutline,
  SparklesOutline, AlbumsOutline, CubeOutline, EyeOutline, PlayOutline,
  DownloadOutline, RefreshOutline, SettingsOutline, ColorWandOutline,
  FilmOutline, AddOutline, FlashOutline, ChevronDownOutline, GitMergeOutline,
  ExpandOutline, CheckmarkCircleOutline, AlertCircleOutline, TimeOutline,
  SyncOutline, HourglassOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import EpisodeBar from '../components/EpisodeBar.vue'
import PipelineView from './PipelineView.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'

const router = useRouter()
const message = useMessage()
const store = useProjectStore()

const batches = ref([])          // all batches of the project
const rowState = reactive({})    // shot_no -> { promptDraft, chosen, running }
const locks = ref(new Set())     // locked shot_no (skipped in batch)
let poll = null

// live generation status (per shot) + overall progress of active batches
const statusByShot = reactive({}) // shot_no -> { status, attempts, error, kind, decision, updated }
const genProgress = reactive({ active: false, done: 0, total: 0, running: '', kind: '', name: '' })
const genStart = ref(0)
const nowTick = ref(Date.now())

// toolbar batch params
const tb = reactive({
  imageSize: '1024x1024',
  duration: 10,
  resolution: '720p',
  aspect: '16:9',
  model: '',
  continuity: false,
  continuityMode: 'auto',
  aiReview: true,
})

const sizeOptions = ['1024x1024', '1280x720', '720x1280'].map((v) => ({ label: v, value: v }))
const resOptions = ['720p', '1080p'].map((v) => ({ label: v, value: v }))
const aspectOptions = ['16:9', '9:16', '1:1'].map((v) => ({ label: v, value: v }))
const imageModelOptions = [
  { label: '默认模型', value: '' }, { label: 'gpt-image-2', value: 'gpt-image-2' }, { label: 'jimeng-4.6', value: 'jimeng-4.6' },
]
const videoModelOptions = [
  { label: '默认模型', value: '' }, { label: 'seed-2.0fast', value: 'seed-2.0fast' },
]

onMounted(async () => {
  await store.refreshList()
  if (!store.current && store.projects.length) await store.select(store.projects[0].id)
  await reload()
})
onUnmounted(() => stopPolling())

const projectOptions = computed(() =>
  store.projects.map((p) => ({ label: `${p.name}（${p.episode_count || 1}集·${p.shot_count || 0}镜）`, value: p.id })))

const episode = computed(() => store.currentEpisode)
const shots = computed(() => episode.value?.shots || [])

// ensure a row-state entry exists for every shot BEFORE render (avoid undefined access)
function ensureRows(list) {
  ;(list || []).forEach((s) => {
    if (!rowState[s.shot_no]) rowState[s.shot_no] = { promptDraft: '', chosen: null, running: false, mode: 'image' }
    else if (!rowState[s.shot_no].mode) rowState[s.shot_no].mode = 'image'
  })
}

const modeTabs = [
  { key: 'image', label: '图片' },
  { key: 'video', label: '视频' },
  { key: 'sync', label: '同步后续' },
]
function autoPrompt(s) {
  // local auto-generated prompt from shot fields (no LLM needed)
  return [s.camera, resolvedText(s)].filter(Boolean).join('，')
}
function inferRow(no) {
  const s = shots.value.find((x) => x.shot_no === no)
  if (s) { rowState[no].promptDraft = autoPrompt(s); message.success(`已推理填充 ${no} 描述词`) }
}
function inferAll() {
  let n = 0
  shots.value.forEach((s) => { if (!locks.value.has(s.shot_no)) { rowState[s.shot_no].promptDraft = autoPrompt(s); n++ } })
  message.success(`已批量推理填充 ${n} 镜描述词`)
}
watch(shots, (list) => ensureRows(list), { immediate: true })

watch(() => store.currentEpisodeId, () => { loadLocks(); reload() })

async function onSelectProject(pid) {
  await store.select(pid)
  await reload()
}

function lockKey() { return `lock:${store.currentId}:${store.currentEpisodeId}` }
function loadLocks() {
  try { locks.value = new Set(JSON.parse(localStorage.getItem(lockKey()) || '[]')) }
  catch { locks.value = new Set() }
}
function toggleLock(no) {
  const s = new Set(locks.value)
  s.has(no) ? s.delete(no) : s.add(no)
  locks.value = s
  localStorage.setItem(lockKey(), JSON.stringify([...s]))
}

async function reload() {
  if (!store.current) return
  loadLocks()
  ensureRows(shots.value)
  batches.value = await api.listBatches(store.current.id)
  await rebuildMaterials()
  // if a batch is already running (e.g. started elsewhere), surface its progress live
  if (batches.value.some((b) => ['running', 'pending'].includes(b.status))) startPolling()
}

// materials of the CURRENT episode only (batches tagged with episode_id, or shot_no prefix match)
function isEpisodeBatch(b) {
  if (b.params?.episode_id) return b.params.episode_id === store.currentEpisodeId
  // fallback: match by shot_no prefix (S{idx}-)
  const idx = episode.value?.idx
  const pfx = idx ? `S${String(idx).padStart(2, '0')}-` : null
  return pfx ? (b.tasks || []).some((t) => (t.shot_no || '').startsWith(pfx)) : false
}

const materialsByShot = reactive({})  // shot_no -> [{bid, filename, kind, status}]

function taskError(t) {
  const e = t.error
  if (!e) return ''
  if (typeof e === 'string') return e
  // structured error from error_policy: friendly message + actionable hint
  const msg = e.message || e.msg || ''
  const hint = e.hint && e.hint !== msg ? `\n${e.hint}` : ''
  return (msg + hint) || JSON.stringify(e)
}

async function rebuildMaterials() {
  // Build into temp maps first, then commit ONLY changed shots — this avoids
  // tearing down <video> elements (and interrupting playback/clicks) on every poll.
  const mat = {}, st = {}
  let pgDone = 0, pgTotal = 0, pgRunning = '', pgKind = '', pgName = '', pgActive = false
  for (const b of batches.value) {
    // list endpoint omits params/tasks → always fetch detail, then decide episode membership
    const full = (b.params && b.tasks && b.tasks.length) ? b : await api.getBatch(store.current.id, b.id)
    if (!isEpisodeBatch(full)) continue
    const batchActive = ['running', 'pending'].includes(full.status)
    ;(full.tasks || []).forEach((t) => {
      const no = t.shot_no
      if (!no) return
      if (!mat[no]) mat[no] = []
      if (t.status === 'done' && t.result?.filename) {
        mat[no].push({ bid: full.id, filename: t.result.filename, kind: full.kind, status: 'done' })
      }
      // track latest task status per shot (newest wins) for the live status badge
      const cand = {
        status: t.status, attempts: t.attempts || 0, error: taskError(t),
        kind: full.kind, decision: t.decision || null, updated: t.updated_at || 0,
      }
      if (!st[no] || cand.updated >= (st[no].updated || 0)) st[no] = cand
      // overall progress: only count tasks belonging to currently-active batches
      if (batchActive) {
        pgActive = true; pgTotal++; pgKind = full.kind; pgName = full.name || ''
        if (t.status === 'done' || t.status === 'error') pgDone++
        if (t.status === 'running') pgRunning = no
      }
    })
  }
  // commit materials incrementally (replace a shot's list only when it actually changed)
  for (const no of Object.keys(materialsByShot)) { if (!(no in mat)) delete materialsByShot[no] }
  for (const no of Object.keys(mat)) {
    const cur = materialsByShot[no]
    const changed = !cur || cur.length !== mat[no].length
      || cur.some((m, i) => m.filename !== mat[no][i]?.filename || m.bid !== mat[no][i]?.bid)
    if (changed) materialsByShot[no] = mat[no]
  }
  // commit status badges incrementally
  for (const no of Object.keys(statusByShot)) { if (!(no in st)) delete statusByShot[no] }
  for (const no of Object.keys(st)) {
    const cur = statusByShot[no]
    if (!cur || cur.status !== st[no].status || cur.error !== st[no].error
        || (cur.decision?.use_tail_frame) !== (st[no].decision?.use_tail_frame)) {
      statusByShot[no] = st[no]
    }
  }
  genProgress.active = pgActive
  genProgress.done = pgDone
  genProgress.total = pgTotal
  genProgress.running = pgRunning
  genProgress.kind = pgKind
  genProgress.name = pgName
}

// ── status badge helpers ──
const STATUS_META = {
  pending: { label: '排队中', type: 'default', icon: HourglassOutline },
  running: { label: '生成中', type: 'info', icon: SyncOutline },
  done: { label: '已完成', type: 'success', icon: CheckmarkCircleOutline },
  error: { label: '失败', type: 'error', icon: AlertCircleOutline },
}
function statusMeta(no) { return STATUS_META[statusByShot[no]?.status] || null }
function decisionLabel(no) {
  const d = statusByShot[no]?.decision
  if (!d) return ''
  return d.use_tail_frame ? '承接上一镜尾帧' : '首镜直生'
}
const genPercent = computed(() => genProgress.total ? Math.round((genProgress.done / genProgress.total) * 100) : 0)
const elapsedText = computed(() => {
  if (!genStart.value) return '0秒'
  const s = Math.max(0, Math.floor((nowTick.value - genStart.value) / 1000))
  const m = Math.floor(s / 60)
  return m ? `${m}分${s % 60}秒` : `${s}秒`
})
watch(() => genProgress.active, (a) => {
  if (a && !genStart.value) genStart.value = Date.now()
  if (!a) genStart.value = 0
})

function isVideoFile(fn) { return /\.(mp4|webm|mov)$/i.test(fn || '') }
function matUrl(m) { return api.outputUrl(store.current.id, m.bid, m.filename) }
function currentMaterial(no) {
  const list = materialsByShot[no] || []
  const chosen = rowState[no]?.chosen
  if (chosen && list.some((m) => m.bid === chosen.bid && m.filename === chosen.filename)) return chosen
  // default: prefer the latest video (this is a video-first preview), else latest image
  const vids = list.filter((m) => isVideoFile(m.filename))
  return vids.at(-1) || list.at(-1) || null
}
function setCurrent(no, m) { rowState[no].chosen = m }

// per-shot media viewer (enlarged video / image)
const showViewer = ref(false)
const viewerMat = ref(null)
const viewerShot = ref('')
function openViewer(no) {
  const m = currentMaterial(no)
  if (!m) return
  viewerMat.value = m
  viewerShot.value = no
  showViewer.value = true
}

// col4: existing materials padded with empty placeholder slots (min 6, scrollable multi-select)
function optSlots(no) {
  const list = materialsByShot[no] || []
  const min = 6
  const pad = Math.max(0, min - list.length)
  return [...list, ...Array(pad).fill(null)]
}

function resolvedText(s) {
  // human-readable subtitle/source for the shot
  return s.action || s.dialogue || s.scene || ''
}

// ── generation ──
function buildParams(kind) {
  const p = kind === 'video'
    ? { duration: tb.duration, resolution: tb.resolution, aspect_ratio: tb.aspect }
    : { size: tb.imageSize }
  if (tb.model) p.model = tb.model
  if (kind === 'video' && tb.continuity) {
    Object.assign(p, { continuity: true, mode: tb.continuityMode, ai_review: tb.aiReview })
  }
  return p
}

function overridesFor(shotNos) {
  const o = {}
  shotNos.forEach((no) => {
    const d = rowState[no]?.promptDraft
    if (d && d.trim()) o[no] = d.trim()
  })
  return o
}

async function genRow(no, kind) {
  rowState[no].running = true
  try {
    const b = await api.createBatch(store.current.id, {
      kind, source: 'shots', name: `${no}-${kind}`,
      episode_id: store.currentEpisodeId, shot_nos: [no],
      prompt_overrides: overridesFor([no]),
      params: buildParams(kind), concurrency: 1,
    })
    await api.startBatch(store.current.id, b.id)
    message.success(`已提交 ${no} ${kind === 'video' ? '生视频' : '生图'}`)
    await refreshNow()  // surface the progress bar immediately; heavy polling starts after 100s
    startPolling()
  } catch (e) {
    message.error(e.message)
    rowState[no].running = false
  }
}

async function genBatch(kind) {
  const targets = shots.value.map((s) => s.shot_no).filter((no) => !locks.value.has(no))
  if (!targets.length) { message.warning('没有可生成的分镜（可能全部已锁定）'); return }
  try {
    const b = await api.createBatch(store.current.id, {
      kind, source: 'shots', name: `${episode.value.name}-批量${kind === 'video' ? '生视频' : '生图'}`,
      episode_id: store.currentEpisodeId, shot_nos: targets,
      prompt_overrides: overridesFor(targets),
      params: buildParams(kind), concurrency: kind === 'video' && tb.continuity ? 1 : 2,
    })
    await api.startBatch(store.current.id, b.id)
    message.success(`已提交批量${kind === 'video' ? '生视频' : '生图'}（${targets.length} 镜${locks.value.size ? `，跳过 ${locks.value.size} 锁定` : ''}）`)
    await refreshNow()  // surface the progress bar immediately; heavy polling starts after 100s
    startPolling()
  } catch (e) { message.error(e.message) }
}

// Polling cadence: video generation is slow, so we DON'T hammer the server.
// First status query fires 100s after generation starts, then every 20s.
// A separate lightweight 1s clock only advances the elapsed timer (no network,
// no material rebuild) so the running video can still be clicked/played.
const POLL_INITIAL_DELAY = 100_000
const POLL_INTERVAL = 20_000
let clock = null

async function pollOnce() {
  nowTick.value = Date.now()
  batches.value = await api.listBatches(store.current.id)
  await rebuildMaterials()
  const active = batches.value.some((b) => ['running', 'pending'].includes(b.status))
  // clear per-row running flags for shots that now have done materials
  shots.value.forEach((s) => {
    if (rowState[s.shot_no]?.running && (materialsByShot[s.shot_no] || []).length) rowState[s.shot_no].running = false
  })
  if (!active) { stopPolling(); shots.value.forEach((s) => { if (rowState[s.shot_no]) rowState[s.shot_no].running = false }) }
  else { poll = setTimeout(pollOnce, POLL_INTERVAL) }
}

function startPolling() {
  if (poll || clock) return  // already polling — don't reset the timer/clock
  if (!genStart.value) genStart.value = Date.now()
  // smooth elapsed-time clock (cheap; never rebuilds materials → no playback disruption)
  clock = setInterval(() => { if (genProgress.active) nowTick.value = Date.now() }, 1000)
  // first heavy status query only after the initial delay
  poll = setTimeout(pollOnce, POLL_INITIAL_DELAY)
}

function stopPolling() {
  if (poll) { clearTimeout(poll); poll = null }
  if (clock) { clearInterval(clock); clock = null }
}

// one-shot fetch + rebuild (no scheduling) — for initial paint / right after a manual start
async function refreshNow() {
  if (!store.current) return
  nowTick.value = Date.now()
  batches.value = await api.listBatches(store.current.id)
  await rebuildMaterials()
}

// preview (storyboard) modal
const showPreview = ref(false)
// continuity (pipeline) drawer
const showPipeline = ref(false)

// bottom-bar 批量生成 dropdown
const batchMenuOptions = [
  { label: '批量生成图片', key: 'image' },
  { label: '批量生成视频', key: 'video' },
]
function onBatchMenu(key) { genBatch(key) }
function notImpl(name) { message.info(`「${name}」即将支持`) }

// ── 导入剪映: export current episode's storyboard as a 剪映 draft (zip) ──
const exportingJy = ref(false)
async function exportJianying() {
  if (!store.current || !store.currentEpisodeId) {
    message.warning('请先选择项目与分集')
    return
  }
  const items = shots.value
    .map((s) => {
      const m = currentMaterial(s.shot_no)
      if (!m) return null
      return { shot_no: s.shot_no, bid: m.bid, filename: m.filename, subtitle: resolvedText(s) }
    })
    .filter(Boolean)
  if (!items.length) {
    message.warning('本集还没有可导出的素材，请先生成分镜图片或视频')
    return
  }
  exportingJy.value = true
  try {
    const draftName = `${store.current.name || '项目'}_${episode.value?.name || ''}`
    const r = await api.exportJianying(store.current.id, store.currentEpisodeId, { items, draft_name: draftName })
    message.success(`已导出剪映草稿：${r.name}（解压后放入剪映草稿目录即可打开）`)
  } catch (e) {
    message.error(`剪映导出失败：${e.message}`)
  } finally {
    exportingJy.value = false
  }
}
</script>

<template>
  <div class="wt">
    <PageHeader title="批量工作台" subtitle="逐镜总览 · 一行一镜（序号/字幕 · 描述词 · 当前素材 · 可选素材）+ 底部统筹工具条">
      <template #actions>
        <n-select :value="store.currentId" :options="projectOptions" placeholder="选择小说项目"
          style="width: 300px" @update:value="onSelectProject" />
      </template>
    </PageHeader>

    <EpisodeBar v-if="store.current" />

    <!-- live generation progress bar (visible while a batch is running) -->
    <div v-if="genProgress.active" class="gen-progress">
      <div class="gp-top">
        <span class="gp-title">
          <n-icon :component="FlashOutline" /> {{ genProgress.kind === 'video' ? '批量生视频' : '批量生图' }}进行中
        </span>
        <span class="gp-stat">{{ genProgress.done }} / {{ genProgress.total }}</span>
        <span v-if="genProgress.running" class="gp-run">
          <n-spin :size="12" /> 正在生成 <b>{{ genProgress.running }}</b>
          <em v-if="decisionLabel(genProgress.running)">· {{ decisionLabel(genProgress.running) }}</em>
        </span>
        <span class="gp-elapsed"><n-icon :component="TimeOutline" /> 已用 {{ elapsedText }}</span>
      </div>
      <n-progress type="line" :percentage="genPercent" :height="8" :border-radius="4" :show-indicator="false" processing />
    </div>

    <n-empty v-if="!store.current" description="请先在「项目导入」导入一个小说项目" style="margin-top: 40px">
      <template #extra><n-button size="small" @click="router.push('/import')">去导入</n-button></template>
    </n-empty>
    <n-empty v-else-if="!shots.length" :description="`「${episode?.name || '本集'}」尚未拆解出分镜`" style="margin-top: 40px">
      <template #extra><n-button size="small" @click="router.push('/script')">去剧本解析</n-button></template>
    </n-empty>

    <template v-else>
      <!-- column header -->
      <div class="row head">
        <div class="c-seq">序号 / 字幕</div>
        <div class="c-prompt">描述词（@人物 #场景 $道具）</div>
        <div class="c-cur">当前素材</div>
        <div class="c-opt">可选素材</div>
      </div>

      <n-scrollbar style="max-height: calc(100vh - 360px)">
        <div v-for="s in shots" :key="s.shot_no" class="row" :class="{ locked: locks.has(s.shot_no) }">
          <!-- col1 seq / subtitle -->
          <div class="c-seq">
            <div class="shot-no">{{ s.shot_no }}</div>
            <div class="sub">{{ resolvedText(s) }}</div>
            <div class="meta">
              <n-tag v-if="s.scene" size="tiny" :bordered="false">#{{ s.scene }}</n-tag>
              <n-tag v-for="c in (s.characters || [])" :key="c" size="tiny" :bordered="false" type="success">@{{ c }}</n-tag>
            </div>
          </div>

          <!-- col2 prompt + controls (xingyao-style borderless) -->
          <div class="c-prompt">
            <div class="mode-tabs">
              <button
                v-for="t in modeTabs" :key="t.key"
                class="mtab" :class="{ on: rowState[s.shot_no].mode === t.key }"
                @click="rowState[s.shot_no].mode = t.key"
              >{{ t.label }}</button>
            </div>
            <textarea
              class="prompt-area"
              v-model="rowState[s.shot_no].promptDraft"
              :placeholder="`${rowState[s.shot_no].mode === 'video' ? '视频' : '图片'}提示词，输入 @ 引用角色，# 引用场景，$ 引用物品…`"
            ></textarea>
            <div class="ctl">
              <template v-if="rowState[s.shot_no].mode === 'image'">
                <n-select size="tiny" v-model:value="tb.model" :options="imageModelOptions" style="width: 120px" />
                <n-select size="tiny" v-model:value="tb.imageSize" :options="sizeOptions" style="width: 120px" />
              </template>
              <template v-else>
                <n-select size="tiny" v-model:value="tb.model" :options="videoModelOptions" style="width: 124px" />
                <n-input-number size="tiny" v-model:value="tb.duration" :min="3" :max="15" style="width: 84px" />
                <n-select size="tiny" v-model:value="tb.aspect" :options="aspectOptions" style="width: 78px" />
                <n-button size="tiny" quaternary @click="notImpl('首帧')"><template #icon><n-icon :component="AddOutline" /></template>首帧</n-button>
                <n-button size="tiny" quaternary @click="notImpl('尾帧')"><template #icon><n-icon :component="AddOutline" /></template>尾帧</n-button>
              </template>
              <div class="ctl-right">
                <n-button size="tiny" @click="inferRow(s.shot_no)">
                  <template #icon><n-icon :component="SparklesOutline" /></template>AI推理
                </n-button>
                <n-button v-if="rowState[s.shot_no].mode === 'image'" size="tiny" type="primary" :loading="rowState[s.shot_no].running" @click="genRow(s.shot_no, 'image')">
                  <template #icon><n-icon :component="ImageOutline" /></template>生成图片
                </n-button>
                <n-button v-else size="tiny" type="primary" :loading="rowState[s.shot_no].running" @click="genRow(s.shot_no, 'video')">
                  <template #icon><n-icon :component="VideocamOutline" /></template>生成视频
                </n-button>
              </div>
            </div>
          </div>

          <!-- col3 current material (status + lock + preview placeholder) -->
          <div class="c-cur">
            <div class="cur-top">
              <template v-if="statusMeta(s.shot_no)">
                <n-tooltip v-if="statusByShot[s.shot_no].status === 'error'" trigger="hover">
                  <template #trigger>
                    <span class="st-badge st-error">
                      <n-icon :component="AlertCircleOutline" /> 失败
                    </span>
                  </template>
                  {{ statusByShot[s.shot_no].error || '生成失败' }}
                </n-tooltip>
                <span v-else class="st-badge" :class="`st-${statusByShot[s.shot_no].status}`">
                  <n-spin v-if="statusByShot[s.shot_no].status === 'running'" :size="11" />
                  <n-icon v-else :component="statusMeta(s.shot_no).icon" />
                  {{ statusMeta(s.shot_no).label }}
                </span>
                <span v-if="statusByShot[s.shot_no].kind === 'video' && decisionLabel(s.shot_no)" class="st-decision">
                  {{ decisionLabel(s.shot_no) }}
                </span>
              </template>
              <button class="lock-row" :class="{ on: locks.has(s.shot_no) }" @click="toggleLock(s.shot_no)">
                <n-icon :component="locks.has(s.shot_no) ? LockClosedOutline : LockOpenOutline" /> 锁定
              </button>
            </div>
            <div class="preview-box" :class="{ empty: !currentMaterial(s.shot_no) }">
              <template v-if="currentMaterial(s.shot_no)">
                <video v-if="isVideoFile(currentMaterial(s.shot_no).filename)" :src="matUrl(currentMaterial(s.shot_no))" muted controls class="cur-media" />
                <n-image v-else :src="matUrl(currentMaterial(s.shot_no))" class="cur-media" object-fit="cover" />
                <button class="view-btn" :title="isVideoFile(currentMaterial(s.shot_no).filename) ? '查看视频' : '查看大图'" @click.stop="openViewer(s.shot_no)">
                  <n-icon :component="isVideoFile(currentMaterial(s.shot_no).filename) ? PlayOutline : ExpandOutline" />
                </button>
                <span v-if="isVideoFile(currentMaterial(s.shot_no).filename)" class="vid-badge">视频</span>
              </template>
            </div>
          </div>

          <!-- col4 optional materials (scrollable multi-select grid placeholder) -->
          <div class="c-opt">
            <div class="opt-scroll">
              <div class="opt-grid">
                <div
                  v-for="(m, i) in optSlots(s.shot_no)" :key="i"
                  class="opt-cell"
                  :class="{ active: m && currentMaterial(s.shot_no) === m, empty: !m }"
                  @click="m && setCurrent(s.shot_no, m)"
                >
                  <template v-if="m">
                    <video v-if="isVideoFile(m.filename)" :src="matUrl(m)" muted class="opt-media" />
                    <img v-else :src="matUrl(m)" class="opt-media" />
                  </template>
                </div>
              </div>
            </div>
          </div>
        </div>
      </n-scrollbar>

      <!-- bottom toolbar: compact centered pill -->
      <div class="toolbar">
        <div class="pill">
          <button class="pbtn" @click="router.push('/assets')"><n-icon :component="CubeOutline" /><span>资产库</span></button>
          <button class="pbtn" @click="showPreview = true"><n-icon :component="EyeOutline" /><span>预览</span></button>
          <button class="pbtn" @click="router.push('/templates')"><n-icon :component="ColorWandOutline" /><span>自定义指令</span></button>
          <button class="pbtn" @click="showPipeline = true"><n-icon :component="GitMergeOutline" /><span>连续性</span></button>
          <n-popover trigger="click" placement="top">
            <template #trigger>
              <button class="pbtn"><n-icon :component="SettingsOutline" /><span>设置</span></button>
            </template>
            <div class="params-pop">
              <div class="pp-row"><span class="pp-l">图片尺寸</span><n-select size="small" v-model:value="tb.imageSize" :options="sizeOptions" style="width: 150px" /></div>
              <div class="pp-row"><span class="pp-l">视频时长</span><n-input-number size="small" v-model:value="tb.duration" :min="3" :max="15" style="width: 150px" /></div>
              <div class="pp-row"><span class="pp-l">分辨率</span><n-select size="small" v-model:value="tb.resolution" :options="resOptions" style="width: 150px" /></div>
              <div class="pp-row"><span class="pp-l">画幅</span><n-select size="small" v-model:value="tb.aspect" :options="aspectOptions" style="width: 150px" /></div>
              <div class="pp-row"><span class="pp-l">模型</span><n-input size="small" v-model:value="tb.model" placeholder="留空用默认" style="width: 150px" /></div>
              <div class="pp-row"><span class="pp-l">连续性</span><n-switch size="small" v-model:value="tb.continuity" /></div>
              <div class="pp-tip">开启连续性：视频批次串行执行，承接上一镜尾帧（跨集亦承接上一集末镜）。</div>
            </div>
          </n-popover>
          <button class="pbtn" @click="inferAll"><n-icon :component="SparklesOutline" /><span>批量推理</span></button>
          <n-dropdown trigger="click" placement="top" :options="batchMenuOptions" @select="onBatchMenu">
            <button class="pbtn accent"><n-icon :component="FlashOutline" /><span>批量生成</span><n-icon :component="ChevronDownOutline" size="12" /></button>
          </n-dropdown>
          <button class="pbtn" :disabled="exportingJy" @click="exportJianying"><n-icon :component="FilmOutline" /><span>{{ exportingJy ? '导出中…' : '导入剪映' }}</span></button>
          <span v-if="locks.size" class="lock-pill">🔒 {{ locks.size }}</span>
        </div>
      </div>
    </template>

    <!-- preview storyboard modal -->
    <n-modal v-model:show="showPreview">
      <n-card style="width: 92vw; max-width: 1200px" :title="`分镜预览 · ${episode?.name || ''}（${shots.length} 镜）`" :bordered="false" role="dialog">
        <n-scrollbar style="max-height: 70vh">
          <div class="pv-grid">
            <div v-for="s in shots" :key="s.shot_no" class="pv-cell" :class="{ clickable: currentMaterial(s.shot_no) }" @click="openViewer(s.shot_no)">
              <div class="pv-no">{{ s.shot_no }}<span v-if="currentMaterial(s.shot_no) && isVideoFile(currentMaterial(s.shot_no).filename)" class="pv-tag">视频</span></div>
              <template v-if="currentMaterial(s.shot_no)">
                <video v-if="isVideoFile(currentMaterial(s.shot_no).filename)" :src="matUrl(currentMaterial(s.shot_no))" muted class="pv-media" />
                <img v-else :src="matUrl(currentMaterial(s.shot_no))" class="pv-media" />
              </template>
              <div v-else class="pv-ph">未生成</div>
              <div class="pv-sub">{{ resolvedText(s) }}</div>
            </div>
          </div>
        </n-scrollbar>
      </n-card>
    </n-modal>

    <!-- single-shot media viewer (enlarged video / image) -->
    <n-modal v-model:show="showViewer">
      <n-card style="width: auto; max-width: 90vw" :title="`分镜查看 · ${viewerShot}`" :bordered="false" role="dialog" closable @close="showViewer = false">
        <div class="viewer-wrap">
          <template v-if="viewerMat">
            <video v-if="isVideoFile(viewerMat.filename)" :src="matUrl(viewerMat)" controls autoplay class="viewer-media" />
            <img v-else :src="matUrl(viewerMat)" class="viewer-media" />
          </template>
        </div>
      </n-card>
    </n-modal>

    <!-- continuity pipeline drawer -->
    <n-drawer v-model:show="showPipeline" :width="760" placement="right">
      <n-drawer-content title="分镜流水线 · 连续性引擎" closable :native-scrollbar="false">
        <PipelineView />
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<style scoped>
.wt { display: flex; flex-direction: column; --wt-media-h: 200px; }
.row {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr) 230px 250px;
  gap: 14px;
  padding: 14px;
  align-items: stretch;
  border: 1px solid var(--app-border);
  border-radius: var(--r-card);
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  margin-bottom: 10px;
}
.row.head {
  background: transparent;
  border: none;
  padding: 4px 14px;
  margin-bottom: 4px;
  align-items: center;
  font-size: 12px;
  color: var(--app-text-muted);
  font-weight: 700;
}
.row.locked { box-shadow: 0 0 0 1px color-mix(in srgb, #ffb454 50%, transparent) inset; }
.c-seq { display: flex; flex-direction: column; gap: 6px; }
.shot-no { font-family: var(--font-mono, monospace); font-weight: 800; color: var(--app-accent); font-size: 13px; }
.sub { font-size: 12.5px; line-height: 1.5; color: var(--app-text); flex: 1; min-height: 60px; max-height: 150px; overflow: auto; }
.meta { display: flex; flex-wrap: wrap; gap: 4px; }

/* ── col2 描述词: borderless, fills the cell ── */
.c-prompt { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.mode-tabs { display: flex; gap: 14px; align-items: center; }
.mtab {
  border: none; background: transparent; cursor: pointer; padding: 2px 0;
  font-size: 13px; color: var(--app-text-muted); position: relative;
}
.mtab.on { color: var(--app-accent); font-weight: 700; }
.mtab.on::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -5px; height: 2px;
  background: var(--app-accent); border-radius: 2px;
}
.prompt-area {
  flex: 1; min-height: 96px; width: 100%; resize: none;
  background: transparent; border: none; outline: none;
  color: var(--app-text); font-size: 13px; line-height: 1.6;
  font-family: inherit; padding: 0;
}
.prompt-area::placeholder { color: var(--app-text-muted); }
.ctl { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ctl-right { margin-left: auto; display: flex; align-items: center; gap: 6px; }

/* ── col3 当前素材: lock + preview placeholder ── */
.c-cur { display: flex; flex-direction: column; gap: 6px; }
.cur-top { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.st-badge {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 11px; padding: 2px 7px; border-radius: 7px; font-weight: 600;
  border: 1px solid transparent;
}
.st-badge :deep(.n-icon) { font-size: 12px; }
.st-pending { color: var(--app-text-secondary); background: color-mix(in srgb, var(--app-text-secondary) 12%, transparent); }
.st-running { color: #4098fc; background: color-mix(in srgb, #4098fc 14%, transparent); }
.st-done { color: #46c98b; background: color-mix(in srgb, #46c98b 16%, transparent); }
.st-error { color: #f56c6c; background: color-mix(in srgb, #f56c6c 15%, transparent); cursor: help; }
.st-decision {
  font-size: 10px; color: var(--app-accent, #46c98b);
  padding: 1px 6px; border-radius: 6px;
  border: 1px dashed color-mix(in srgb, var(--app-accent, #46c98b) 45%, transparent);
}
.lock-row {
  margin-left: auto; display: inline-flex; align-items: center; gap: 4px;
  font-size: 12px; padding: 2px 8px; border-radius: 7px; cursor: pointer;
  border: 1px solid var(--app-border); background: transparent; color: var(--app-text-secondary);
}
.lock-row.on { color: #ffb454; border-color: color-mix(in srgb, #ffb454 50%, transparent); }

/* live generation progress bar */
.gen-progress {
  margin: 10px 0 4px; padding: 10px 14px; border-radius: 10px;
  background: var(--app-surface-2, rgba(255,255,255,0.03));
  border: 1px solid var(--app-border);
}
.gp-top { display: flex; align-items: center; gap: 14px; margin-bottom: 8px; flex-wrap: wrap; font-size: 13px; }
.gp-title { display: inline-flex; align-items: center; gap: 5px; font-weight: 600; color: var(--app-accent, #46c98b); }
.gp-stat { font-variant-numeric: tabular-nums; font-weight: 700; }
.gp-run { display: inline-flex; align-items: center; gap: 5px; color: var(--app-text-secondary); }
.gp-run b { color: var(--app-text); }
.gp-run em { font-style: normal; color: var(--app-accent, #46c98b); }
.gp-elapsed { display: inline-flex; align-items: center; gap: 4px; margin-left: auto; color: var(--app-text-secondary); font-variant-numeric: tabular-nums; }
.preview-box {
  position: relative;
  height: var(--wt-media-h); border-radius: 10px; overflow: hidden;
  border: 1px solid var(--app-border); background: var(--app-bg-soft);
}
.view-btn {
  position: absolute; top: 6px; right: 6px; z-index: 2;
  width: 26px; height: 26px; border-radius: 6px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 14px;
  background: rgba(0, 0, 0, 0.5); border: 1px solid rgba(255, 255, 255, 0.25);
  transition: background 0.15s;
}
.view-btn:hover { background: var(--app-accent); color: #06210f; }
.vid-badge {
  position: absolute; left: 6px; bottom: 6px; z-index: 2;
  font-size: 10px; line-height: 1; padding: 3px 6px; border-radius: 4px;
  background: rgba(33, 254, 132, 0.85); color: #06210f; font-weight: 700;
}
.viewer-wrap { display: flex; align-items: center; justify-content: center; }
.viewer-media { max-width: 86vw; max-height: 74vh; border-radius: 8px; display: block; }
.pv-tag {
  margin-left: 6px; font-size: 10px; padding: 1px 5px; border-radius: 4px;
  background: rgba(33, 254, 132, 0.2); color: var(--app-accent); font-weight: 700;
}
.pv-cell.clickable { cursor: pointer; transition: border-color 0.15s; }
.pv-cell.clickable:hover { border-color: var(--app-accent); }
.preview-box.empty {
  background-color: #14161a;
  background-image:
    linear-gradient(45deg, #23262b 25%, transparent 25%),
    linear-gradient(-45deg, #23262b 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #23262b 75%),
    linear-gradient(-45deg, transparent 75%, #23262b 75%);
  background-size: 18px 18px;
  background-position: 0 0, 0 9px, 9px -9px, -9px 0;
}
.cur-media { width: 100%; height: var(--wt-media-h); object-fit: cover; display: block; }
.c-cur :deep(.n-image), .c-cur :deep(.n-image img) { width: 100%; height: var(--wt-media-h); display: block; }
.c-cur :deep(.n-image img) { object-fit: cover; }

/* ── col4 可选素材: scrollable multi-select grid placeholder ── */
.c-opt { display: flex; }
.opt-scroll {
  width: 100%; height: calc(var(--wt-media-h) + 28px);
  overflow-y: auto; padding-right: 4px;
}
.opt-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.opt-cell {
  height: 96px; border-radius: 8px; overflow: hidden;
  border: 2px solid transparent; cursor: pointer;
}
.opt-cell.empty {
  border: 1px dashed color-mix(in srgb, var(--app-border) 90%, transparent);
  background: color-mix(in srgb, var(--app-bg-soft) 60%, transparent);
  cursor: default;
}
.opt-cell.active { border-color: var(--app-accent); }
.opt-media { width: 100%; height: 100%; object-fit: cover; display: block; }

/* ── bottom toolbar: compact centered pill ── */
.toolbar {
  position: sticky; bottom: 12px;
  margin-top: 14px;
  display: flex; justify-content: center;
}
.pill {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 6px 8px;
  background: color-mix(in srgb, var(--app-surface) 96%, transparent);
  backdrop-filter: blur(10px);
  border: 1px solid var(--app-border);
  border-radius: 999px;
  box-shadow: 0 8px 24px rgba(0,0,0,.28);
}
.pbtn {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 7px 12px; border-radius: 999px;
  border: none; background: transparent; color: var(--app-text-secondary);
  cursor: pointer; font-size: 12.5px; white-space: nowrap;
  transition: background .15s, color .15s;
}
.pbtn:hover { background: var(--app-surface); color: var(--app-text-primary); }
.pbtn.accent { background: var(--app-accent); color: #04130b; font-weight: 700; }
.pbtn.accent:hover { filter: brightness(1.06); }
.lock-pill { font-size: 12px; color: #ffb454; padding: 0 6px; }
.params-pop { display: flex; flex-direction: column; gap: 10px; min-width: 230px; }
.pp-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.pp-l { font-size: 12.5px; color: var(--app-text-secondary); }
.pp-tip { font-size: 11.5px; color: var(--app-text-muted); line-height: 1.5; }

.pv-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
.pv-cell { border: 1px solid var(--app-border); border-radius: 8px; padding: 8px; }
.pv-no { font-family: var(--font-mono, monospace); font-weight: 800; color: var(--app-accent); font-size: 12px; margin-bottom: 6px; }
.pv-media, .pv-ph {
  width: 100%; height: 110px; border-radius: 6px; object-fit: cover;
  background: var(--app-bg-soft); border: 1px solid var(--app-border);
}
.pv-ph { display: flex; align-items: center; justify-content: center; color: var(--app-text-muted); font-size: 12px; }
.pv-sub { font-size: 11.5px; color: var(--app-text-muted); margin-top: 6px; line-height: 1.45; max-height: 50px; overflow: hidden; }
</style>
