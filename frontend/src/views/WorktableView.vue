<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NSelect, NButton, NIcon, NEmpty, NInput, NTag, NImage, NSwitch, NTooltip,
  NModal, NCard, NSpace, NInputNumber, NScrollbar, NBadge, NPopover, NDropdown,
  NDrawer, NDrawerContent, NProgress, NSpin, NCheckbox, NCheckboxGroup,
  useMessage,
} from 'naive-ui'
import {
  ImageOutline, VideocamOutline, LockClosedOutline, LockOpenOutline,
  SparklesOutline, AlbumsOutline, CubeOutline, EyeOutline, PlayOutline,
  DownloadOutline, RefreshOutline, SettingsOutline, ColorWandOutline,
  FilmOutline, AddOutline, FlashOutline, ChevronDownOutline, ChevronForwardOutline, GitMergeOutline,
  ExpandOutline, CheckmarkCircleOutline, AlertCircleOutline, TimeOutline,
  SyncOutline, HourglassOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import EpisodeBar from '../components/EpisodeBar.vue'
import PipelineView from './PipelineView.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'
import { useTasksStore } from '../stores/tasks'

const router = useRouter()
const message = useMessage()
const store = useProjectStore()
const tasks = useTasksStore()

// batch list lives in the global tasks store so polling/progress survive tab
// switches (§8.2). This view just reads it and derives per-episode materials.
const batches = computed(() => tasks.batches)
const rowState = reactive({})    // shot_no -> { imagePrompt, videoPrompt, chosen, running, mode }
const locks = ref(new Set())     // locked shot_no (skipped in batch)
const inferAllRunning = ref(false) // 批量 AI 推理进行中

// live generation status (per shot) + overall progress of active batches
const statusByShot = reactive({}) // shot_no -> { status, attempts, error, kind, decision, updated }
const genProgress = reactive({ active: false, done: 0, total: 0, running: '', kind: '', name: '' })

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
  decisionLlm: true,  // 默认直接开启 LLM 升级决策（无凭据时后端自动回退规则）
  batchPollMaxMinutes: 30,
})
const TB_STORAGE_PREFIX = 'wt:tb:'
function tbStorageKey(pid = store.currentId) { return `${TB_STORAGE_PREFIX}${pid || 'none'}` }
function saveTb() {
  try {
    if (!store.currentId) return
    localStorage.setItem(tbStorageKey(), JSON.stringify({
      imageSize: tb.imageSize,
      duration: tb.duration,
      resolution: tb.resolution,
      aspect: tb.aspect,
      model: tb.model,
      continuity: tb.continuity,
      continuityMode: tb.continuityMode,
      aiReview: tb.aiReview,
      decisionLlm: tb.decisionLlm,
      batchPollMaxMinutes: tb.batchPollMaxMinutes,
    }))
  } catch { /* ignore */ }
}
function loadTb() {
  try {
    const raw = localStorage.getItem(tbStorageKey())
    if (!raw) return
    const data = JSON.parse(raw)
    if (!data || typeof data !== 'object') return
    Object.assign(tb, {
      imageSize: data.imageSize || tb.imageSize,
      duration: Number.isFinite(Number(data.duration)) ? Number(data.duration) : tb.duration,
      resolution: data.resolution || tb.resolution,
      aspect: data.aspect || tb.aspect,
      model: typeof data.model === 'string' ? data.model : tb.model,
      continuity: data.continuity !== undefined ? !!data.continuity : tb.continuity,
      continuityMode: data.continuityMode || tb.continuityMode,
      aiReview: data.aiReview !== undefined ? !!data.aiReview : tb.aiReview,
      decisionLlm: data.decisionLlm !== undefined ? !!data.decisionLlm : tb.decisionLlm,
      batchPollMaxMinutes: Number.isFinite(Number(data.batchPollMaxMinutes)) ? Number(data.batchPollMaxMinutes) : tb.batchPollMaxMinutes,
    })
  } catch { /* ignore */ }
}
function resetTb() {
  Object.assign(tb, {
    imageSize: '1024x1024',
    duration: 10,
    resolution: '720p',
    aspect: '16:9',
    model: '',
    continuity: false,
    continuityMode: 'auto',
    aiReview: true,
    decisionLlm: true,
    batchPollMaxMinutes: 30,
  })
}

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
  loadTb()
  await reload()
})

watch(() => store.currentId, () => { loadTb() })
watch(tb, () => saveTb(), { deep: true })
// NOTE: we intentionally do NOT stop polling on unmount — the tasks store keeps
// polling across tab switches so an in-flight batch keeps updating (§8.2).

const projectOptions = computed(() =>
  store.projects.map((p) => ({ label: `${p.name}（${p.episode_count || 1}集·${p.shot_count || 0}镜）`, value: p.id })))

const episode = computed(() => store.currentEpisode)
const shots = computed(() => episode.value?.shots || [])

// ensure a row-state entry exists for every shot BEFORE render (avoid undefined access)
function ensureRows(list) {
  ;(list || []).forEach((s) => {
    if (!rowState[s.shot_no]) rowState[s.shot_no] = { imagePrompt: '', videoPrompt: '', chosen: null, running: false, inferring: false, mode: 'image' }
    else {
      if (!rowState[s.shot_no].mode) rowState[s.shot_no].mode = 'image'
      if (rowState[s.shot_no].imagePrompt === undefined) rowState[s.shot_no].imagePrompt = ''
      if (rowState[s.shot_no].videoPrompt === undefined) rowState[s.shot_no].videoPrompt = ''
    }
  })
}

const modeTabs = [
  { key: 'image', label: '图片' },
  { key: 'video', label: '视频' },
  { key: 'sync', label: '同步后续' },
]
// current-mode prompt accessors (image vs video are stored separately so each
// mode keeps its own editable text)
function promptField(no) { return rowState[no]?.mode === 'video' ? 'videoPrompt' : 'imagePrompt' }
function currentPrompt(no) { return rowState[no]?.[promptField(no)] || '' }
function setPrompt(no, val) { if (rowState[no]) rowState[no][promptField(no)] = val }

// ── @ 引用缩略图：把当前提示词里的 @资产 解析为其参考图缩略图，支持单击放大
// （n-image 内置灯箱）+ 长按拖动重排（重排会改写提示词里 @token 的先后顺序）──
const assetList = ref([])
async function loadAssets() {
  if (!store.current) { assetList.value = []; return }
  try { assetList.value = await api.listAssets(store.current.id) }
  catch { assetList.value = [] }
}
function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') }
// ordered, de-duplicated refs present in the prompt. 严格映射：只渲染「资产库里确实
// 存在、且已生成/导入参考图」的资产（a.ref_image 在 = 资产能被实际调用），否则回落
// 纯文字、绝不臆造。匹配统一 @名，也兼容库分类符 #场景/$道具 与裸名，覆盖各类引用。
function promptRefs(no) {
  const text = currentPrompt(no)
  if (!text || !assetList.value.length) return []
  const out = []
  // longest name first so 「@林夏」不会被「@林」抢先命中
  const ordered = [...assetList.value].sort((a, b) => (b.name || '').length - (a.name || '').length)
  for (const a of ordered) {
    if (!a.ref_image || !a.name) continue
    const name = escRe(a.name)
    // @名（统一前缀）｜库分类符 #/$ + 名｜裸名
    const re = new RegExp('(?:@|' + escRe(a.trigger || '') + ')?' + name)
    const m = re.exec(text)
    if (m) out.push({
      name: a.name, trigger: a.trigger, type: a.type,
      url: api.assetImageUrl(store.current.id, a.ref_image), idx: m.index,
    })
  }
  return out.sort((x, y) => x.idx - y.idx)
}
// drag-reorder state (long-press / press-drag): {no, from}
const dragRef = reactive({ no: null, from: -1, over: -1 })
function onRefDragStart(no, idx, ev) {
  dragRef.no = no; dragRef.from = idx; dragRef.over = idx
  if (ev?.dataTransfer) { ev.dataTransfer.effectAllowed = 'move' }
}
function onRefDragOver(no, idx) { if (dragRef.no === no) dragRef.over = idx }
function onRefDrop(no, idx) {
  if (dragRef.no !== no || dragRef.from < 0 || dragRef.from === idx) { resetDragRef(); return }
  reorderRefs(no, dragRef.from, idx)
  resetDragRef()
}
function resetDragRef() { dragRef.no = null; dragRef.from = -1; dragRef.over = -1 }
// rewrite the prompt so the @tokens appear in the new order (first occurrences only)
function reorderRefs(no, from, to) {
  const refs = promptRefs(no)
  if (from < 0 || from >= refs.length || to < 0 || to >= refs.length) return
  const tokens = refs.map((r) => '@' + r.name)
  const next = tokens.slice()
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  const alt = refs.map((r) => escRe(r.name)).sort((a, b) => b.length - a.length).join('|')
  const re = new RegExp('@(' + alt + ')', 'g')
  let k = 0
  const text = currentPrompt(no).replace(re, (m) => (k < next.length ? next[k++] : m))
  setPrompt(no, text)
}

// Pull engine-grade image/video prompts from the backend (deterministic, no LLM
// call, spends no credits) and drop them into the editable textboxes so the user
// can see & adjust exactly what will be generated. `force` overwrites existing
// text (explicit AI推理); otherwise only blanks are filled (default display).
async function fillPrompts(shotNos, { force = false } = {}) {
  if (!store.current || !shotNos.length) return 0
  let res
  try {
    res = await api.previewShotPrompts(store.current.id, {
      episode_id: store.currentEpisodeId, shot_nos: shotNos,
    })
  } catch (e) { message.error('提示词推理失败：' + e.message); return 0 }
  const prompts = res?.prompts || {}
  let n = 0
  shotNos.forEach((no) => {
    const r = rowState[no]; const p = prompts[no]
    if (!r || !p) return
    if (force || !(r.imagePrompt || '').trim()) r.imagePrompt = p.image || ''
    if (force || !(r.videoPrompt || '').trim()) r.videoPrompt = p.video || ''
    n++
  })
  return n
}
// 真·LLM 逐镜推理：调用 /infer_shot_prompt，喂入连续性(上一镜 handoff)+本镜关联资产
// 说明+结构字段，产出干净规范的图片/视频提示词并回填（覆盖）。失败自动后端兜底确定性合成。
async function inferShot(no) {
  const r = rowState[no]
  if (!r || !store.current) return null
  const res = await api.inferShotPrompt(store.current.id, {
    episode_id: store.currentEpisodeId, shot_no: no,
  })
  if (res.image) r.imagePrompt = res.image
  if (res.video) r.videoPrompt = res.video
  return res
}
async function inferRow(no) {
  const r = rowState[no]
  if (!r) return
  r.inferring = true
  try {
    const res = await inferShot(no)
    if (res?.source === 'fallback') message.warning(`${no}：LLM 推理失败，已用确定性合成兜底`)
    else message.success(`已 AI 推理 ${no} 图片/视频提示词`)
    await reload()
  } catch (e) { message.error('AI 推理失败：' + e.message) }
  finally { r.inferring = false }
}
async function inferAll() {
  const targets = shots.value.map((s) => s.shot_no).filter((no) => !locks.value.has(no))
  if (!targets.length) return
  inferAllRunning.value = true
  let ok = 0, fb = 0
  for (const no of targets) {                 // 串行，避免并发打爆中转站
    const r = rowState[no]; if (!r) continue
    r.inferring = true
    try {
      const res = await inferShot(no)
      res?.source === 'fallback' ? fb++ : ok++
    } catch { fb++ } finally { r.inferring = false }
  }
  inferAllRunning.value = false
  message.success(`AI 推理完成：成功 ${ok}${fb ? `，兜底 ${fb}` : ''}（共 ${targets.length} 镜）`)
  await reload()
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
  loadAssets()
  tasks.setBatchProject(store.current.id)
  await tasks.refreshBatches()
  await rebuildMaterials()
  // show prompts by default (fill only blanks; keep any user edits)
  await fillPrompts(shots.value.map((s) => s.shot_no))
  // if a batch is already running (e.g. started before we navigated here), the
  // store keeps polling so its progress stays live.
  if (tasks.batchActive) tasks.ensureBatchPolling()
}

// Re-derive per-episode materials whenever the store's batch list changes
// (driven by the store's persistent polling loop) so progress stays live even
// when we return to this view after a tab switch.
watch(() => tasks.batches, async () => {
  await rebuildMaterials()
  shots.value.forEach((s) => {
    if (rowState[s.shot_no]?.running && (materialsByShot[s.shot_no] || []).length) {
      rowState[s.shot_no].running = false
    }
  })
  if (!tasks.batchActive) shots.value.forEach((s) => { if (rowState[s.shot_no]) rowState[s.shot_no].running = false })
}, { deep: true })

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
  let hasStatusChange = false
  for (const no of Object.keys(st)) {
    const cur = statusByShot[no]
    if (!cur || cur.status !== st[no].status || cur.error !== st[no].error
        || cur.attempts !== st[no].attempts
        || (cur.decision?.use_tail_frame) !== (st[no].decision?.use_tail_frame)) {
      statusByShot[no] = st[no]
      hasStatusChange = true
    }
  }
  if (hasStatusChange) notifyNewErrors()
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
  const source = d.source === 'llm' ? 'LLM' : '规则'
  const strategy = d.strategy ? `·${d.strategy}` : ''
  if (d.use_tail_frame) return `${source}${strategy}｜承接上一镜尾帧`
  if (d.use_staging) return `${source}${strategy}｜站位图`
  if (d.use_director_board) return `${source}${strategy}｜导演图`
  return `${source}${strategy}｜首镜直生`
}
const genPercent = computed(() => genProgress.total ? Math.round((genProgress.done / genProgress.total) * 100) : 0)
const elapsedText = computed(() => {
  // genStart/nowTick live in the tasks store so the elapsed timer keeps counting
  // across tab switches (§8.2).
  if (!tasks.genStart) return '0秒'
  const s = Math.max(0, Math.floor((tasks.nowTick - tasks.genStart) / 1000))
  const m = Math.floor(s / 60)
  return m ? `${m}分${s % 60}秒` : `${s}秒`
})
function shotProgressText(no) {
  const st = statusByShot[no]
  if (!st) return ''
  const bits = []
  if (genProgress.total) bits.push(`${genProgress.done}/${genProgress.total}`)
  if (st.attempts) bits.push(`第${st.attempts}次`)
  if (tasks.genStart && ['running', 'pending'].includes(st.status)) bits.push(elapsedText.value)
  return bits.join(' · ')
}
function statusTitle(no) {
  const st = statusByShot[no]
  if (!st) return ''
  if (st.status === 'error') return st.error || '生成失败，请检查提示词/参数后手动重新提交'
  return `当前进度：${genProgress.done}/${genProgress.total || 0}，已用时：${elapsedText.value}`
}
const notifiedErrors = new Set()
function notifyNewErrors() {
  for (const [no, st] of Object.entries(statusByShot)) {
    if (st?.status !== 'error' || !st.error) continue
    const key = `${no}:${st.kind}:${st.updated}:${st.error}`
    if (notifiedErrors.has(key)) continue
    notifiedErrors.add(key)
    const prefix = st.kind === 'video' ? '视频生成失败' : '生成失败'
    message.error(`${prefix}：${no}。已停止自动重试，请修改提示词或参数后手动重新生成。${st.error}`)
  }
}

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

// ordered playlist viewer: one player, play all shots sequentially for continuity checks
const showPlaylist = ref(false)
const playlistIndex = ref(0)
const playlistItems = computed(() =>
  shots.value
    .map((s) => ({ shot_no: s.shot_no, material: currentMaterial(s.shot_no) }))
    .filter((it) => it.material))
const playlistCurrent = computed(() => playlistItems.value[playlistIndex.value] || null)
const PLAYLIST_IMAGE_SECONDS = 3.5
let playlistTimer = null
function clearPlaylistTimer() {
  if (playlistTimer) { clearTimeout(playlistTimer); playlistTimer = null }
}
function schedulePlaylistAdvance() {
  clearPlaylistTimer()
  const cur = playlistCurrent.value
  if (!showPlaylist.value || !cur?.material) return
  if (isVideoFile(cur.material.filename)) return
  playlistTimer = setTimeout(() => {
    playlistNext()
  }, Math.max(800, PLAYLIST_IMAGE_SECONDS * 1000))
}
function openPlaylist() {
  if (!playlistItems.value.length) { message.warning('当前没有可播放的分镜素材'); return }
  playlistIndex.value = 0
  showPlaylist.value = true
  schedulePlaylistAdvance()
}
function playlistPrev() {
  if (!playlistItems.value.length) return
  playlistIndex.value = (playlistIndex.value - 1 + playlistItems.value.length) % playlistItems.value.length
  schedulePlaylistAdvance()
}
function playlistNext() {
  if (!playlistItems.value.length) return
  playlistIndex.value = (playlistIndex.value + 1) % playlistItems.value.length
  schedulePlaylistAdvance()
}
function onPlaylistEnded() { playlistNext() }
function jumpPlaylist(no) {
  const idx = playlistItems.value.findIndex((it) => it.shot_no === no)
  if (idx >= 0) {
    playlistIndex.value = idx
    schedulePlaylistAdvance()
  }
}

// col4: existing materials padded with empty placeholder slots (min 6, scrollable multi-select)
function optSlots(no) {
  const list = materialsByShot[no] || []
  const min = 6
  const pad = Math.max(0, min - list.length)
  return [...list, ...Array(pad).fill(null)]
}

// ── col1 序号/字幕 文本处理 ──
// 分镜的 scene/characters/props 由拆解模板带 #/@/$ 前缀存储；展示时先去掉已有
// 前缀再补一个，避免「##深山密林 / @@主角」这类双重符号。
function stripMark(v) { return String(v == null ? '' : v).replace(/^[\s#@$＠＃＄]+/, '').trim() }
function fmtScene(v) { const n = stripMark(v); return n ? '#' + n : '' }
function fmtChar(v) { const n = stripMark(v); return n ? '@' + n : '' }
function fmtProp(v) { const n = stripMark(v); return n ? '$' + n : '' }

// 字幕：优先台词（真正的字幕），其次动作描述；去掉「画面展示/镜头展示」等冗余引导词。
const _LEAD_FILLER = /^(画面|镜头|本镜|此镜|该镜)?(展示|呈现|显示|表现|描绘|描述|刻画|出现)[：:，,]?/
function subtitleText(s) {
  let t = (s.dialogue && s.dialogue.trim()) || (s.action || '').trim() || stripMark(s.scene)
  return t.replace(_LEAD_FILLER, '').trim()
}
function actionText(s) {
  // 当字幕取自台词时，额外显示动作行作为画面说明（去冗余引导词）
  if (!(s.dialogue && s.dialogue.trim())) return ''
  return (s.action || '').trim().replace(_LEAD_FILLER, '').trim()
}

// 原文：按 shot.seq 关联到本集解析后的源片段文本，供逐镜对照。
const segMap = computed(() => {
  const m = {}
  const segs = episode.value?.segments || episode.value?.parsed?.segments || []
  for (const sg of segs) if (sg.seq != null) m[sg.seq] = sg.text || ''
  return m
})
// 优先用拆解时按内容确定性匹配落库的 src_text（保证与本镜真正出处一致）；
// 旧数据无 src_text 时回退按 seq 关联源片段。
function sourceText(s) { return (s.src_text && s.src_text.trim()) || (s.seq != null && segMap.value[s.seq]) || '' }
const openSrc = reactive({})   // shot_no -> bool (展开原文)
function toggleSrc(no) { openSrc[no] = !openSrc[no] }

// ── generation ──
function buildParams(kind) {
  const p = kind === 'video'
    ? { duration: tb.duration, resolution: tb.resolution, aspect_ratio: tb.aspect }
    : { size: tb.imageSize }
  if (tb.model) p.model = tb.model
  if (kind === 'video' && tb.continuity) {
    Object.assign(p, {
      continuity: true, mode: tb.continuityMode, ai_review: tb.aiReview,
      decision_llm: tb.decisionLlm,
    })
  }
  return p
}

function overridesFor(shotNos, kind) {
  const field = kind === 'video' ? 'videoPrompt' : 'imagePrompt'
  const o = {}
  shotNos.forEach((no) => {
    const d = rowState[no]?.[field]
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
      prompt_overrides: overridesFor([no], kind),
      params: buildParams(kind), concurrency: 1,
    })
    await api.startBatch(store.current.id, b.id)
    message.success(`已提交 ${no} ${kind === 'video' ? '生视频' : '生图'}`)
    // surface the progress bar immediately; heavy polling (store) starts after 100s
    await tasks.ensureBatchPolling({ immediate: true, maxMinutes: tb.batchPollMaxMinutes })
    await reload()
  } catch (e) {
    message.error(e.message)
  } finally {
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
      prompt_overrides: overridesFor(targets, kind),
      params: buildParams(kind), concurrency: kind === 'video' && tb.continuity ? 1 : 2,
    })
    await api.startBatch(store.current.id, b.id)
    message.success(`已提交批量${kind === 'video' ? '生视频' : '生图'}（${targets.length} 镜${locks.value.size ? `，跳过 ${locks.value.size} 锁定` : ''}）`)
    // surface the progress bar immediately; heavy polling (store) starts after 100s
    await tasks.ensureBatchPolling({ immediate: true })
    await reload()
  } catch (e) { message.error(e.message) }
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

// ── 多集并发生成：单集串行 · 多集并发（每集独立 tab + 进度徽标）──
const showEpiGen = ref(false)
const epiGen = reactive({ kind: 'image', maxParallel: 2, selected: [] })
function epiShotCount(e) { return e.shot_count ?? (e.shots ? e.shots.length : 0) }
const decomposedEpisodes = computed(() =>
  (store.episodes || []).filter((e) => e.stage === 'decomposed' || epiShotCount(e) > 0))
function openEpiGen() {
  if (!decomposedEpisodes.value.length) { message.warning('没有已拆解的分集（请先在「剧本解析」拆解分镜）'); return }
  epiGen.selected = decomposedEpisodes.value.map((e) => e.id)
  showEpiGen.value = true
}
async function submitEpiGen() {
  if (!epiGen.selected.length) { message.warning('请至少选择一个分集'); return }
  // The worktable only holds edited prompts for the *current* episode; other
  // episodes fall back to engine-default prompts (image desc / video template).
  const episodes = epiGen.selected.map((eid) => {
    const e = { episode_id: eid }
    if (eid === store.currentEpisodeId) {
      const targets = shots.value.map((s) => s.shot_no).filter((no) => !locks.value.has(no))
      e.shot_nos = targets
      e.prompt_overrides = overridesFor(targets, epiGen.kind)
    }
    return e
  })
  try {
    const r = await api.createEpisodeBatches(store.current.id, {
      kind: epiGen.kind, episodes, params: buildParams(epiGen.kind), max_parallel: epiGen.maxParallel,
    })
    showEpiGen.value = false
    const n = r.created?.length || 0
    const sk = r.skipped?.length ? `，跳过 ${r.skipped.length} 集` : ''
    message.success(`已启动 ${n} 集并发${epiGen.kind === 'video' ? '生视频' : '生图'}（最多 ${r.max_parallel} 集同时运行，单集串行）${sk}`)
    await tasks.ensureBatchPolling({ immediate: true })
    await reload()
  } catch (e) { message.error(e.message) }
}

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
      return { shot_no: s.shot_no, bid: m.bid, filename: m.filename, subtitle: sourceText(s) || resolvedText(s) }
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
            <div class="sub">{{ subtitleText(s) }}</div>
            <div v-if="actionText(s)" class="sub-action">{{ actionText(s) }}</div>
            <div class="meta">
              <n-tag v-if="fmtScene(s.scene)" size="tiny" :bordered="false">{{ fmtScene(s.scene) }}</n-tag>
              <n-tag v-for="c in (s.characters || [])" :key="c" size="tiny" :bordered="false" type="success">{{ fmtChar(c) }}</n-tag>
              <n-tag v-for="pp in (s.props || [])" :key="pp" size="tiny" :bordered="false" type="warning">{{ fmtProp(pp) }}</n-tag>
            </div>
            <button v-if="sourceText(s)" class="src-toggle" @click="toggleSrc(s.shot_no)">
              <n-icon :component="openSrc[s.shot_no] ? ChevronDownOutline : ChevronForwardOutline" />
              原文
            </button>
            <div v-if="openSrc[s.shot_no] && sourceText(s)" class="src-text">{{ sourceText(s) }}</div>
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
            <!-- @引用参考图缩略图：移到提示词框上方作「本镜引用」图例；仅渲染资产库里
                 校验一致（确实能被调用、已有参考图）的资产，仅作附加显示不传输到生成端。
                 单击放大（n-image 内置灯箱）/ 长按拖动重排。 -->
            <div v-if="promptRefs(s.shot_no).length" class="ref-strip">
              <span class="ref-strip-cap" title="仅显示资产库中校验一致、可被调用的引用；缩略图不会传输到生成端">本镜引用</span>
              <div
                v-for="(r, ri) in promptRefs(s.shot_no)"
                :key="r.name"
                class="ref-thumb"
                :class="{ dragging: dragRef.no === s.shot_no && dragRef.from === ri, dropover: dragRef.no === s.shot_no && dragRef.over === ri && dragRef.from !== ri }"
                draggable="true"
                :title="`${r.trigger || '@'}${r.name}（长按拖动可调整顺序 · 单击放大）`"
                @dragstart="onRefDragStart(s.shot_no, ri, $event)"
                @dragover.prevent="onRefDragOver(s.shot_no, ri)"
                @drop.prevent="onRefDrop(s.shot_no, ri)"
                @dragend="resetDragRef"
              >
                <n-image
                  :src="r.url"
                  :width="40"
                  :height="40"
                  object-fit="cover"
                  class="ref-img"
                  :draggable="false"
                />
                <span class="ref-name">{{ r.trigger || '@' }}{{ r.name }}</span>
              </div>
            </div>
            <textarea
              class="prompt-area"
              :value="currentPrompt(s.shot_no)"
              @input="setPrompt(s.shot_no, $event.target.value)"
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
                <n-button size="tiny" :loading="rowState[s.shot_no].inferring" @click="inferRow(s.shot_no)">
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
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <span class="st-badge" :class="`st-${statusByShot[s.shot_no].status}`">
                      <n-spin v-if="statusByShot[s.shot_no].status === 'running'" :size="11" />
                      <n-icon v-else :component="statusMeta(s.shot_no).icon" />
                      <span>{{ statusMeta(s.shot_no).label }}</span>
                      <small v-if="shotProgressText(s.shot_no)">{{ shotProgressText(s.shot_no) }}</small>
                    </span>
                  </template>
                  <div class="st-tip">
                    <div>{{ statusTitle(s.shot_no) }}</div>
                    <div v-if="statusByShot[s.shot_no].kind">类型：{{ statusByShot[s.shot_no].kind === 'video' ? '视频' : '图片' }}</div>
                    <div v-if="decisionLabel(s.shot_no)">{{ decisionLabel(s.shot_no) }}</div>
                  </div>
                </n-tooltip>
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
                <div v-if="!isVideoFile(currentMaterial(s.shot_no).filename)" class="preview-actions">
                  <button class="view-btn" title="查看大图" @click.stop="openViewer(s.shot_no)">
                    <n-icon :component="ExpandOutline" />
                    <span>预览</span>
                  </button>
                </div>
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
          <button class="pbtn" @click="showPreview = true"><n-icon :component="EyeOutline" /><span>分镜预览</span></button>
          <button class="pbtn accent" :disabled="!playlistItems.length" @click="openPlaylist"><n-icon :component="PlayOutline" /><span>顺播检查</span></button>
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
              <div class="pp-row"><span class="pp-l">轮询最长分钟数</span><n-input-number size="small" v-model:value="tb.batchPollMaxMinutes" :min="5" :max="180" style="width: 150px" /> </div>
              <div class="pp-row"><span class="pp-l">连续性</span><n-switch size="small" v-model:value="tb.continuity" /></div>
              <div class="pp-row" v-if="tb.continuity"><span class="pp-l">LLM升级决策</span><n-switch size="small" v-model:value="tb.decisionLlm" /></div>
              <div class="pp-actions">
                <n-button size="small" quaternary @click="resetTb">恢复默认</n-button>
                <span class="pp-tip">开启连续性：视频批次串行执行。LLM升级决策默认开启：每镜由 LLM 在切镜头、尾帧、站位图、导演图等策略中平权判断，按剧情连续性选择最自然的衔接方式；无凭据时自动回退规则。</span>
              </div>
            </div>
          </n-popover>
          <button class="pbtn" :disabled="inferAllRunning" @click="inferAll"><n-icon :component="SparklesOutline" /><span>{{ inferAllRunning ? 'AI推理中…' : 'AI推理全部' }}</span></button>
          <n-dropdown trigger="click" placement="top" :options="batchMenuOptions" @select="onBatchMenu">
            <button class="pbtn accent"><n-icon :component="FlashOutline" /><span>批量生成</span><n-icon :component="ChevronDownOutline" size="12" /></button>
          </n-dropdown>
          <button class="pbtn" @click="openEpiGen"><n-icon :component="AlbumsOutline" /><span>多集并发</span></button>
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

    <!-- ordered playlist viewer (single-player sequential playback) -->
    <n-modal v-model:show="showPlaylist">
      <n-card style="width: min(96vw, 1280px)" :title="`顺播检查 · ${episode?.name || ''}（${playlistItems.length} 段）`" :bordered="false" role="dialog" closable @close="clearPlaylistTimer(); showPlaylist = false">
        <div class="playlist-shell" v-if="playlistCurrent">
          <div class="playlist-player">
            <div class="playlist-stage">
              <video
                v-if="isVideoFile(playlistCurrent.material.filename)"
                :src="matUrl(playlistCurrent.material)"
                controls autoplay
                class="playlist-media"
                @ended="onPlaylistEnded"
              />
              <img v-else :src="matUrl(playlistCurrent.material)" class="playlist-media" />
              <div class="playlist-overlay">
                <span class="playlist-shot">{{ playlistCurrent.shot_no }}</span>
                <span class="playlist-type">{{ isVideoFile(playlistCurrent.material.filename) ? '视频' : '图片' }}</span>
              </div>
            </div>
            <div class="playlist-nav">
              <button class="nav-btn" @click="playlistPrev">上一段</button>
              <button class="nav-btn" @click="schedulePlaylistAdvance">继续自动播放</button>
              <button class="nav-btn" @click="playlistNext">下一段</button>
              <button class="nav-btn primary" @click="clearPlaylistTimer(); showPlaylist = false">关闭</button>
            </div>
          </div>
          <div class="playlist-list">
            <button
              v-for="(it, idx) in playlistItems"
              :key="it.shot_no"
              class="plist-item"
              :class="{ active: idx === playlistIndex }"
              @click="jumpPlaylist(it.shot_no)"
            >
              <span class="plist-no">{{ it.shot_no }}</span>
              <span class="plist-kind">{{ isVideoFile(it.material.filename) ? '视频' : '图片' }}</span>
            </button>
          </div>
        </div>
      </n-card>
    </n-modal>

    <!-- continuity pipeline drawer -->
    <n-drawer v-model:show="showPipeline" :width="760" placement="right">
      <n-drawer-content title="分镜流水线 · 连续性引擎" closable :native-scrollbar="false">
        <PipelineView />
      </n-drawer-content>
    </n-drawer>

    <!-- 多集并发生成 modal -->
    <n-modal v-model:show="showEpiGen">
      <n-card style="width: 560px; max-width: 94vw" title="多集并发生成" :bordered="false" role="dialog" closable @close="showEpiGen = false">
        <div class="epi-gen">
          <div class="eg-row">
            <span class="eg-label">生成类型</span>
            <n-select v-model:value="epiGen.kind" style="width: 160px"
              :options="[{ label: '生成图片', value: 'image' }, { label: '生成视频', value: 'video' }]" />
          </div>
          <div class="eg-row">
            <span class="eg-label">最大并发集数</span>
            <n-input-number v-model:value="epiGen.maxParallel" :min="1" :max="8" style="width: 120px" />
            <span class="eg-hint">单集内串行，多集并行（越大越占内存）</span>
          </div>
          <div class="eg-row eg-top">
            <span class="eg-label">选择分集</span>
            <n-checkbox-group v-model:value="epiGen.selected" class="eg-eps">
              <n-checkbox v-for="e in decomposedEpisodes" :key="e.id" :value="e.id">
                {{ e.name }} · {{ epiShotCount(e) }}镜
              </n-checkbox>
            </n-checkbox-group>
          </div>
          <div class="eg-note">
            仅当前分集会带上工作台已编辑/锁定的提示词；其余分集使用引擎默认提示词（视频按「视频生成提示词」模板合成）。
          </div>
        </div>
        <template #footer>
          <n-space justify="end">
            <n-button @click="showEpiGen = false">取消</n-button>
            <n-button type="primary" :disabled="!epiGen.selected.length" @click="submitEpiGen">
              启动并发（{{ epiGen.selected.length }} 集）
            </n-button>
          </n-space>
        </template>
      </n-card>
    </n-modal>
  </div>
</template>

<style scoped>
.wt { display: flex; flex-direction: column; --wt-media-h: 200px; }
.epi-gen { display: flex; flex-direction: column; gap: 16px; }
.eg-row { display: flex; align-items: center; gap: 12px; }
.eg-row.eg-top { align-items: flex-start; }
.eg-label { width: 88px; flex-shrink: 0; font-size: 13px; color: var(--app-text-muted); }
.eg-hint, .eg-note { font-size: 12px; color: var(--app-text-muted); }
.eg-eps { display: flex; flex-wrap: wrap; gap: 8px 16px; }
.eg-note { line-height: 1.6; padding: 8px 10px; border-radius: 8px; background: var(--app-bg-soft); }
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
.sub { font-size: 12.5px; line-height: 1.5; color: var(--app-text); max-height: 150px; overflow: auto; }
.sub-action { font-size: 11.5px; line-height: 1.45; color: var(--app-text-muted); margin-top: -2px; }
.meta { display: flex; flex-wrap: wrap; gap: 4px; }
.src-toggle {
  align-self: flex-start; display: inline-flex; align-items: center; gap: 3px;
  border: none; background: transparent; cursor: pointer; padding: 0;
  font-size: 11px; color: var(--app-text-muted);
}
.src-toggle:hover { color: var(--app-accent); }
.src-toggle :deep(.n-icon) { font-size: 12px; }
.src-text {
  font-size: 11.5px; line-height: 1.55; color: var(--app-text-secondary);
  white-space: pre-wrap; background: var(--app-fill-2, rgba(255,255,255,.04));
  border-left: 2px solid var(--app-accent); border-radius: 4px;
  padding: 6px 8px; max-height: 160px; overflow: auto;
}

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
/* @引用缩略图条 */
.ref-strip {
  display: flex; flex-wrap: wrap; gap: 8px;
  padding: 2px 0 8px; align-items: center;
}
.ref-strip-cap {
  font-size: 11px; color: var(--app-text-muted);
  align-self: center; margin-right: 2px; white-space: nowrap;
}
.ref-thumb {
  display: flex; flex-direction: column; align-items: center; gap: 3px;
  width: 48px; cursor: grab; user-select: none;
  border-radius: 8px; padding: 3px; transition: background .15s, transform .12s, opacity .12s;
}
.ref-thumb:hover { background: var(--app-accent-soft); }
.ref-thumb:active { cursor: grabbing; }
.ref-thumb.dragging { opacity: .45; transform: scale(.94); }
.ref-thumb.dropover { background: var(--app-accent-soft); box-shadow: inset 0 0 0 2px var(--app-accent); }
.ref-img :deep(img) { border-radius: 6px; }
.ref-name {
  max-width: 46px; font-size: 10px; line-height: 1.1; text-align: center;
  color: var(--app-text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
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
.st-badge small {
  font-size: 10px; font-weight: 600; opacity: .82;
  padding-left: 3px; font-variant-numeric: tabular-nums;
}
.st-tip { max-width: 360px; white-space: pre-wrap; line-height: 1.6; }
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
.playlist-shell {
  display: grid; grid-template-columns: minmax(0, 1fr) 220px; gap: 12px;
  align-items: stretch;
}
.playlist-player {
  display: flex; flex-direction: column; gap: 10px;
}
.playlist-media {
  width: 100%; max-height: 72vh; border-radius: 10px; object-fit: contain;
  background: #000;
}
.playlist-nav { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }
.nav-btn {
  border: 1px solid var(--app-border); background: var(--app-bg-soft); color: var(--app-text);
  padding: 6px 12px; border-radius: 999px; cursor: pointer;
}
.nav-btn.primary { background: var(--app-accent); color: #06210f; border-color: transparent; }
.playlist-list {
  max-height: 72vh; overflow: auto; padding-left: 4px; display: flex; flex-direction: column; gap: 6px;
}
.plist-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  width: 100%; border: 1px solid var(--app-border); background: var(--app-bg-soft);
  color: var(--app-text); border-radius: 10px; padding: 8px 10px; cursor: pointer;
}
.plist-item.active { border-color: var(--app-accent); box-shadow: 0 0 0 1px var(--app-accent) inset; }
.plist-no { font-weight: 700; font-family: var(--font-mono, monospace); }
.plist-kind { font-size: 12px; color: var(--app-text-muted); }
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
