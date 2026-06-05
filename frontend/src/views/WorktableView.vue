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
  SparklesOutline, AlbumsOutline, EyeOutline, PlayOutline,
  DownloadOutline, RefreshOutline, SettingsOutline, PauseOutline,
  FilmOutline, AddOutline, FlashOutline, ChevronDownOutline, ChevronForwardOutline, GitMergeOutline,
  ExpandOutline, CheckmarkCircleOutline, AlertCircleOutline, TimeOutline,
  SyncOutline, HourglassOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import EpisodeBar from '../components/EpisodeBar.vue'
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
const genProgress = reactive({ active: false, done: 0, total: 0, running: '', kind: '', name: '', stageLabel: '', runningProgress: 0, batchIds: [], stopping: false })
const fullBatchCache = reactive({}) // bid -> full batch detail used by error drawer
const showErrorDrawer = ref(false)
const errorDrawerMode = ref('episode') // episode | shot
const errorDrawerShot = ref('')
const continuityState = ref(null)
const bridgeBusy = reactive({}) // transition key -> action
const manualBridge = reactive({}) // next shot_no -> manual continuity decision

// toolbar batch params
const tb = reactive({
  imageSize: '1024x1024',
  duration: 10,
  resolution: '720p',
  aspect: '16:9',
  model: '',
  continuity: false,
  continuityMode: 'auto',
  directorBoardOnly: false,
  aiReview: true,
  decisionLlm: true,  // 默认直接开启 LLM 升级决策（无凭据时后端自动回退规则）
  batchPollMaxMinutes: 30,
})
const TB_STORAGE_PREFIX = 'wt:tb:'
function tbStorageKey(pid = store.currentId) { return `${TB_STORAGE_PREFIX}${pid || 'none'}` }
const CONT_STORAGE_PREFIX = 'wt:continuity-settings:'
function continuityStorageKey(pid = store.currentId, eid = store.currentEpisodeId) {
  return `${CONT_STORAGE_PREFIX}${pid || 'none'}:${eid || 'none'}`
}
const CONTINUITY_DEFAULTS = {
  continuity: false,
  continuityMode: 'auto',
  directorBoardOnly: false,
  aiReview: true,
  decisionLlm: true,
}
function saveTb() {
  try {
    if (!store.currentId) return
    localStorage.setItem(tbStorageKey(), JSON.stringify({
      imageSize: tb.imageSize,
      duration: tb.duration,
      resolution: tb.resolution,
      aspect: tb.aspect,
      model: tb.model,
      batchPollMaxMinutes: tb.batchPollMaxMinutes,
    }))
  } catch { /* ignore */ }
}
function projectContinuityFallback(pid = store.currentId) {
  try {
    const raw = localStorage.getItem(tbStorageKey(pid))
    const data = raw ? JSON.parse(raw) : null
    if (!data || typeof data !== 'object') return { ...CONTINUITY_DEFAULTS }
    return {
      continuity: data.continuity !== undefined ? !!data.continuity : CONTINUITY_DEFAULTS.continuity,
      continuityMode: data.continuityMode || CONTINUITY_DEFAULTS.continuityMode,
      directorBoardOnly: data.directorBoardOnly !== undefined ? !!data.directorBoardOnly : CONTINUITY_DEFAULTS.directorBoardOnly,
      aiReview: data.aiReview !== undefined ? !!data.aiReview : CONTINUITY_DEFAULTS.aiReview,
      decisionLlm: data.decisionLlm !== undefined ? !!data.decisionLlm : CONTINUITY_DEFAULTS.decisionLlm,
    }
  } catch { return { ...CONTINUITY_DEFAULTS } }
}
function loadContinuitySettings(pid = store.currentId, eid = store.currentEpisodeId) {
  const base = projectContinuityFallback(pid)
  try {
    const raw = localStorage.getItem(continuityStorageKey(pid, eid))
    const data = raw ? JSON.parse(raw) : null
    Object.assign(tb, {
      continuity: data?.continuity !== undefined ? !!data.continuity : base.continuity,
      continuityMode: data?.continuityMode || base.continuityMode,
      directorBoardOnly: data?.directorBoardOnly !== undefined ? !!data.directorBoardOnly : base.directorBoardOnly,
      aiReview: data?.aiReview !== undefined ? !!data.aiReview : base.aiReview,
      decisionLlm: data?.decisionLlm !== undefined ? !!data.decisionLlm : base.decisionLlm,
    })
  } catch { Object.assign(tb, base) }
}
function saveContinuitySettings() {
  try {
    if (!store.currentId || !store.currentEpisodeId) return
    localStorage.setItem(continuityStorageKey(), JSON.stringify({
      continuity: tb.continuity,
      continuityMode: tb.continuityMode,
      directorBoardOnly: tb.directorBoardOnly,
      aiReview: tb.aiReview,
      decisionLlm: tb.decisionLlm,
    }))
  } catch { /* ignore */ }
}
async function loadTb() {
  try {
    const defaults = await api.getSettings().catch(() => null)
    if (defaults) {
      Object.assign(tb, {
        imageSize: defaults.image_size || tb.imageSize,
        duration: Number.isFinite(Number(defaults.video_duration)) ? Number(defaults.video_duration) : tb.duration,
        resolution: defaults.video_resolution || tb.resolution,
        aspect: defaults.video_aspect_ratio || tb.aspect,
      })
    }
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
      batchPollMaxMinutes: Number.isFinite(Number(data.batchPollMaxMinutes)) ? Number(data.batchPollMaxMinutes) : tb.batchPollMaxMinutes,
    })
  } catch { /* ignore */ }
  loadContinuitySettings()
}
function resetTb() {
  Object.assign(tb, {
    imageSize: '1024x1024',
    duration: 10,
    resolution: '720p',
    aspect: '16:9',
    model: '',
    batchPollMaxMinutes: 30,
  })
}

function bridgeStorageKey(pid = store.currentId, eid = store.currentEpisodeId) {
  return `wt:continuity-bridge:${pid || 'none'}:${eid || 'none'}`
}
function defaultBridgeDecision() {
  return {
    use_tail_frame: false,
    use_staging: false,
    use_director_board: false,
    scene_cut: true,
    strategy: 'manual',
    source: 'manual',
    reason: 'Manual continuity bridge selection',
  }
}
function loadManualBridge() {
  Object.keys(manualBridge).forEach((k) => { delete manualBridge[k] })
  try {
    const raw = localStorage.getItem(bridgeStorageKey())
    const data = raw ? JSON.parse(raw) : {}
    if (!data || typeof data !== 'object') return
    Object.entries(data).forEach(([shotNo, value]) => {
      manualBridge[shotNo] = { ...defaultBridgeDecision(), ...(value || {}) }
    })
  } catch { /* ignore */ }
}
function saveManualBridge() {
  try {
    localStorage.setItem(bridgeStorageKey(), JSON.stringify(manualBridge))
  } catch { /* ignore */ }
}
function materialSelectionKey(pid = store.currentId, eid = store.currentEpisodeId) {
  return `wt:current-material:${pid || 'none'}:${eid || 'none'}`
}
function loadMaterialSelections() {
  try {
    const raw = localStorage.getItem(materialSelectionKey())
    const data = raw ? JSON.parse(raw) : {}
    if (!data || typeof data !== 'object') return
    Object.entries(data).forEach(([no, sel]) => {
      if (!rowState[no] || !sel || rowState[no].chosen?.bid) return
      rowState[no].chosen = { bid: sel.bid || '', filename: sel.filename || '', kind: sel.kind || '' }
    })
  } catch { /* ignore */ }
}
function saveMaterialSelections() {
  try {
    if (!store.currentId || !store.currentEpisodeId) return
    const out = {}
    for (const [no, r] of Object.entries(rowState)) {
      const c = r?.chosen
      if (c?.bid && c?.filename) out[no] = { bid: c.bid, filename: c.filename, kind: c.kind || '' }
    }
    localStorage.setItem(materialSelectionKey(), JSON.stringify(out))
  } catch { /* ignore */ }
}
function restoreMaterialSelections() {
  let changed = false
  for (const [no, r] of Object.entries(rowState)) {
    const c = r?.chosen
    if (!c?.bid || !c?.filename) continue
    const found = (materialsByShot[no] || []).find((m) => !isVirtualMaterial(m) && m.bid === c.bid && m.filename === c.filename)
    if (found && found !== c) { r.chosen = found; changed = true }
  }
  if (changed) saveMaterialSelections()
}

const sizeOptions = ['1024x1024', '1280x720', '720x1280'].map((v) => ({ label: v, value: v }))
const resOptions = ['720p', '1080p'].map((v) => ({ label: v, value: v }))
const aspectOptions = ['16:9', '9:16', '1:1'].map((v) => ({ label: v, value: v }))
const imageModelOptions = [
  { label: '默认模型', value: '' }, { label: 'gpt-image-2', value: 'gpt-image-2' }, { label: 'jimeng-4.6', value: 'jimeng-4.6' },
]
const videoModelOptions = [
  { label: '默认模型', value: '' },
  { label: 'doubao-seedance-2-0-fast-260128', value: 'doubao-seedance-2-0-fast-260128' },
  { label: 'doubao-seedance-2-0-260128', value: 'doubao-seedance-2-0-260128' },
  { label: 'doubao-seedance-2-0-260128-1', value: 'doubao-seedance-2-0-260128-1' },
  { label: 'doubao-seedance-2-0-260128-2', value: 'doubao-seedance-2-0-260128-2' },
  { label: 'doubao-seedance-2-0-260128-3', value: 'doubao-seedance-2-0-260128-3' },
  { label: 'seed-2.0fast（旧接口兼容）', value: 'seed-2.0fast' },
]

onMounted(async () => {
  await store.refreshList()
  if (!store.current && store.projects.length) await store.select(store.projects[0].id)
  await loadTb()
  loadContinuitySettings()
  loadManualBridge()
  loadMaterialSelections()
  await reload()
})

watch(() => store.currentId, () => { loadTb(); loadContinuitySettings(); loadManualBridge(); loadMaterialSelections() })
watch(() => store.currentEpisodeId, () => { loadLocks(); loadContinuitySettings(); loadManualBridge(); loadMaterialSelections(); reload() })
watch(tb, () => { saveTb(); saveContinuitySettings() }, { deep: true })
watch(() => tb.continuity, () => {
  if (store.current && shots.value.length) refreshVideoPrompts(shots.value.map((s) => s.shot_no))
})
// NOTE: we intentionally do NOT stop polling on unmount — the tasks store keeps
// polling across tab switches so an in-flight batch keeps updating (§8.2).

const projectOptions = computed(() =>
  store.projects.map((p) => ({ label: `${p.name}（${p.episode_count || 1}集·${p.shot_count || 0}镜）`, value: p.id })))

const episode = computed(() => store.currentEpisode)
const shots = computed(() => episode.value?.shots || [])

// ensure a row-state entry exists for every shot BEFORE render (avoid undefined access)
function ensureRows(list) {
  ;(list || []).forEach((s) => {
    if (!rowState[s.shot_no]) rowState[s.shot_no] = { imagePrompt: '', videoPrompt: '', refs: [], chosen: null, running: false, inferring: false, fallbacking: false, mode: 'image' }
    else {
      if (!rowState[s.shot_no].mode) rowState[s.shot_no].mode = 'image'
      if (rowState[s.shot_no].imagePrompt === undefined) rowState[s.shot_no].imagePrompt = ''
      if (rowState[s.shot_no].videoPrompt === undefined) rowState[s.shot_no].videoPrompt = ''
      if (!Array.isArray(rowState[s.shot_no].refs)) rowState[s.shot_no].refs = []
      if (rowState[s.shot_no].fallbacking === undefined) rowState[s.shot_no].fallbacking = false
    }
    if (s.selected_material?.bid && s.selected_material?.filename) {
      const cur = rowState[s.shot_no].chosen
      if (!cur || cur.bid !== s.selected_material.bid || cur.filename !== s.selected_material.filename) {
        rowState[s.shot_no].chosen = {
          bid: s.selected_material.bid,
          filename: s.selected_material.filename,
          kind: s.selected_material.kind || '',
        }
      }
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
  const engineRefs = rowState[no]?.refs
  if (Array.isArray(engineRefs) && engineRefs.length) {
    return engineRefs
      .filter((r) => r && r.url)
      .map((r, idx) => ({
        ...r,
        engine: true,
        idx,
        trigger: r.token || `@图${idx + 1}`,
        name: r.name || refRoleLabel(r.role),
      }))
  }
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
function refRoleLabel(role) {
  if (role === 'first_frame') return '上一镜尾帧'
  if (role === 'staging') return '站位图'
  if (role === 'director') return '导演图'
  if (role === 'scene_aerial') return '鸟瞰图'
  if (role === 'scene') return '场景'
  if (role === 'prop') return '道具'
  return '角色'
}
function refDisplay(r) {
  return r.engine ? `${r.token || ''} ${r.name || refRoleLabel(r.role)}`.trim() : `${r.trigger || '@'}${r.name}`
}
function refKey(r, i) {
  return `${r.token || r.trigger || '@'}:${r.role || r.type || ''}:${r.asset_id || r.filename || r.name || i}`
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
      continuity: !!tb.continuity,
      manual_continuity: manualContinuityPayload(),
    })
  } catch (e) { message.error('提示词推理失败：' + e.message); return 0 }
  const prompts = res?.prompts || {}
  let n = 0
  shotNos.forEach((no) => {
    const r = rowState[no]; const p = prompts[no]
    if (!r || !p) return
    if (force || !(r.imagePrompt || '').trim()) r.imagePrompt = p.image || ''
    if (force || !(r.videoPrompt || '').trim()) r.videoPrompt = p.video || ''
    r.refs = Array.isArray(p.refs) ? p.refs : []
    n++
  })
  return n
}

async function refreshVideoPrompts(shotNos) {
  if (!store.current || !shotNos.length) return 0
  try {
    const res = await api.previewShotPrompts(store.current.id, {
      episode_id: store.currentEpisodeId,
      shot_nos: shotNos,
      continuity: !!tb.continuity,
      manual_continuity: manualContinuityPayload(),
    })
    const prompts = res?.prompts || {}
    let n = 0
    shotNos.forEach((no) => {
      const r = rowState[no]; const p = prompts[no]
      if (!r || !p) return
      r.videoPrompt = p.video || r.videoPrompt || ''
      r.refs = Array.isArray(p.refs) ? p.refs : []
      n++
    })
    return n
  } catch (e) {
    message.error('刷新连续性画面定义失败：' + e.message)
    return 0
  }
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
  if (Array.isArray(res.refs)) r.refs = res.refs
  return res
}
async function inferRow(no) {
  const r = rowState[no]
  if (!r) return
  r.inferring = true
  try {
    const res = await inferShot(no)
    if (res?.source === 'fallback') message.warning(`${no}：LLM 推理失败，已用确定性合成兜底`)
    else if (res?.source === 'llm_partial_fallback') message.warning(`${no}：AI 仅返回部分结果，视频词已用默认动态补齐`)
    else message.success(`已 AI 推理并保存 ${no} 图片/视频提示词`)
    await reload()
  } catch (e) { message.error('AI 推理失败：' + e.message) }
  finally { r.inferring = false }
}

async function useFallbackPrompt(no) {
  const r = rowState[no]
  if (!r || !store.current) return
  r.fallbacking = true
  try {
    const res = await api.previewShotPrompts(store.current.id, {
      episode_id: store.currentEpisodeId,
      shot_nos: [no],
      ignore_saved: true,
    })
    const p = res?.prompts?.[no]
    if (!p) throw new Error('未获取到兜底提示词')
    r.imagePrompt = p.image || ''
    r.videoPrompt = p.video || ''
    r.refs = Array.isArray(p.refs) ? p.refs : []
    await api.saveShotPrompts(store.current.id, {
      episode_id: store.currentEpisodeId,
      shot_no: no,
      image: r.imagePrompt,
      video: r.videoPrompt,
      source: 'fallback',
    })
    message.success(`已切换并保存 ${no} 兜底提示词`)
  } catch (e) {
    message.error('切换兜底提示词失败：' + e.message)
  } finally {
    r.fallbacking = false
  }
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
      res?.source === 'fallback' || res?.source === 'llm_partial_fallback' ? fb++ : ok++
    } catch { fb++ } finally { r.inferring = false }
  }
  inferAllRunning.value = false
  message.success(`AI 推理并保存完成：成功 ${ok}${fb ? `，兜底 ${fb}` : ''}（共 ${targets.length} 镜）`)
  await reload()
}

watch(shots, (list) => ensureRows(list), { immediate: true })

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
  try { continuityState.value = await api.getContinuity(store.current.id) }
  catch { continuityState.value = null }
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
    if (rowState[s.shot_no]?.running && (materialsByShot[s.shot_no] || []).some((m) => !isVirtualMaterial(m))) {
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
  let pgStageLabel = '', pgRunningProgress = 0
  const pgBatchIds = new Set()
  let pgStopping = false
  for (const b of batches.value) {
    if (b.episode_id && b.episode_id !== store.currentEpisodeId) continue
    // list endpoint omits params/tasks → always fetch detail, then decide episode membership
    const cached = fullBatchCache[b.id]
    const full = (b.params && b.tasks && b.tasks.length)
      ? b
      : (cached && cached.updated_at === b.updated_at ? cached : await api.getBatch(store.current.id, b.id))
    fullBatchCache[full.id] = full
    if (!isEpisodeBatch(full)) continue
    const batchActive = ['running', 'pending'].includes(full.status)
    if (batchActive) {
      pgBatchIds.add(full.id)
      if (full.pause_requested) pgStopping = true
    }
    ;(full.tasks || []).forEach((t) => {
      const no = t.shot_no
      if (!no) return
      if (!mat[no]) mat[no] = []
      if (t.status === 'done' && t.result?.filename) {
        mat[no].push({ bid: full.id, filename: t.result.filename, kind: full.kind, status: 'done' })
      }
      if (full.kind === 'video' && t.status === 'running') {
        mat[no].push({
          bid: full.id,
          kind: 'video',
          status: 'running',
          virtual: true,
          taskId: t.id,
          stage: t.stage || 'running',
          stageLabel: t.stage_label || '生成中',
          progress: Number(t.progress || 0),
        })
      }
      // track latest task status per shot (newest wins) for the live status badge
      const cand = {
        status: t.status, attempts: t.attempts || 0, error: taskError(t),
        kind: full.kind, decision: t.decision || null, updated: t.updated_at || 0,
        stage: t.stage || '', stageLabel: t.stage_label || '', progress: Number(t.progress || 0),
      }
      if (!st[no] || cand.updated >= (st[no].updated || 0)) st[no] = cand
      // overall progress: only count tasks belonging to currently-active batches
      if (batchActive) {
        pgActive = true; pgTotal++; pgKind = full.kind; pgName = full.name || ''
        if (t.status === 'done' || t.status === 'error') pgDone++
        if (t.status === 'running') {
          pgRunning = no
          pgStageLabel = t.stage_label || ''
          pgRunningProgress = Number(t.progress || 0)
        }
      }
    })
  }
  // commit materials incrementally (replace a shot's list only when it actually changed)
  for (const no of Object.keys(materialsByShot)) { if (!(no in mat)) delete materialsByShot[no] }
  for (const no of Object.keys(mat)) {
    const cur = materialsByShot[no]
    const changed = !cur || cur.length !== mat[no].length
      || cur.some((m, i) => materialKey(m) !== materialKey(mat[no][i]))
    if (changed) materialsByShot[no] = mat[no]
  }
  restoreMaterialSelections()
  // commit status badges incrementally
  for (const no of Object.keys(statusByShot)) { if (!(no in st)) delete statusByShot[no] }
  let hasStatusChange = false
  for (const no of Object.keys(st)) {
    const cur = statusByShot[no]
    if (!cur || cur.status !== st[no].status || cur.error !== st[no].error
        || cur.attempts !== st[no].attempts
        || cur.stageLabel !== st[no].stageLabel
        || cur.progress !== st[no].progress
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
  genProgress.stageLabel = pgStageLabel
  genProgress.runningProgress = pgRunningProgress
  genProgress.batchIds = [...pgBatchIds]
  genProgress.stopping = pgStopping
}

// ── status badge helpers ──
const STATUS_META = {
  pending: { label: '排队中', type: 'default', icon: HourglassOutline },
  running: { label: '生成中', type: 'info', icon: SyncOutline },
  paused: { label: '已暂停', type: 'warning', icon: PauseOutline },
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
  if (st.stageLabel && ['running', 'pending'].includes(st.status)) bits.push(st.stageLabel)
  if (st.progress && ['running', 'pending'].includes(st.status)) bits.push(`${Math.round(st.progress)}%`)
  if (genProgress.total) bits.push(`${genProgress.done}/${genProgress.total}`)
  if (st.attempts) bits.push(`第${st.attempts}次`)
  if (tasks.genStart && ['running', 'pending'].includes(st.status)) bits.push(elapsedText.value)
  return bits.join(' · ')
}
function statusTitle(no) {
  const st = statusByShot[no]
  if (!st) return ''
  if (st.status === 'error') return st.error || '生成失败，请检查提示词/参数后手动重新提交'
  const stage = st.stageLabel ? `${st.stageLabel}${st.progress ? ` ${Math.round(st.progress)}%` : ''}，` : ''
  return `${stage}当前进度：${genProgress.done}/${genProgress.total || 0}，已用时：${elapsedText.value}`
}
const notifiedErrors = new Set()
const notifiedErrorEpisodes = new Set()
const dismissedErrorIds = reactive(new Set())
const episodeErrors = computed(() => collectErrors())
const visibleErrors = computed(() => {
  if (errorDrawerMode.value === 'shot') {
    return episodeErrors.value.filter((e) => e.shotNo === errorDrawerShot.value)
  }
  return episodeErrors.value
})
const errorDrawerTitle = computed(() => {
  if (errorDrawerMode.value === 'shot') return `错误详情 · ${errorDrawerShot.value}`
  return `本集错误 · ${episode.value?.name || ''}`
})

function collectErrors() {
  const out = []
  for (const full of Object.values(fullBatchCache)) {
    if (!full || !isEpisodeBatch(full)) continue
    for (const t of full.tasks || []) {
      if (t.status !== 'error') continue
      const shotNo = t.shot_no || t.id
      const id = `${full.id}:${t.id || shotNo}:${t.updated_at || 0}:${taskError(t) || ''}`
      if (dismissedErrorIds.has(id)) continue
      out.push({
        id,
        batchId: full.id,
        batchName: full.name || '',
        shotNo,
        kind: full.kind,
        attempts: t.attempts || 0,
        updated: t.updated_at || full.updated_at || 0,
        message: taskError(t) || '生成失败',
        raw: t.error,
      })
    }
  }
  return out.sort((a, b) => (b.updated || 0) - (a.updated || 0))
}

function notifyNewErrors() {
  const errs = collectErrors()
  if (!errs.length) return
  const epKey = `${store.currentEpisodeId}:${errs.map((e) => e.id).join('|')}`
  if (!notifiedErrorEpisodes.has(epKey)) {
    notifiedErrorEpisodes.add(epKey)
    message.error(`本集有 ${errs.length} 个生成错误，已收纳到失败标记；点击“部分失败”或单镜“失败”查看详情。`)
  }
  for (const [no, st] of Object.entries(statusByShot)) {
    if (st?.status !== 'error' || !st.error) continue
    const key = `${no}:${st.kind}:${st.updated}:${st.error}`
    if (notifiedErrors.has(key)) continue
    notifiedErrors.add(key)
  }
}

async function openEpisodeErrors(epId = store.currentEpisodeId) {
  if (epId && epId !== store.currentEpisodeId) store.selectEpisode(epId)
  await rebuildMaterials()
  errorDrawerMode.value = 'episode'
  errorDrawerShot.value = ''
  showErrorDrawer.value = true
}

function openShotError(no) {
  if (statusByShot[no]?.status !== 'error') return
  errorDrawerMode.value = 'shot'
  errorDrawerShot.value = no
  showErrorDrawer.value = true
}

function isVideoFile(fn) { return /\.(mp4|webm|mov)$/i.test(fn || '') }
function isVirtualMaterial(m) { return !!m?.virtual }
function materialKey(m) {
  if (!m) return 'empty'
  return [m.bid, m.filename || '', m.status || '', m.virtual ? 'virtual' : 'real', m.stageLabel || '', m.progress || 0].join('|')
}
function materialSame(a, b) {
  return !!a && !!b && a.bid === b.bid && a.filename === b.filename
}

function clearVisibleErrors() {
  const errs = visibleErrors.value
  if (!errs.length) return
  errs.forEach((e) => dismissedErrorIds.add(e.id))
  for (const e of errs) {
    const st = statusByShot[e.shotNo]
    if (st?.status === 'error' && st.kind === e.kind) delete statusByShot[e.shotNo]
  }
  message.success(`已清空 ${errs.length} 条错误提示`)
}
function materialStatusLabel(m) { return m?.stageLabel || (m?.status === 'running' ? '生成中' : '') }
function materialProgress(m) {
  const n = Number(m?.progress || 0)
  return Math.max(0, Math.min(100, Number.isFinite(n) ? Math.round(n) : 0))
}
function matUrl(m) { return api.outputUrl(store.current.id, m.bid, m.filename) }
function currentMaterial(no) {
  const list = (materialsByShot[no] || []).filter((m) => !isVirtualMaterial(m))
  const chosen = rowState[no]?.chosen
  if (chosen?.bid && chosen?.filename) {
    return list.find((m) => materialSame(m, chosen)) || chosen
  }
  // default: prefer the latest video (this is a video-first preview), else latest image
  const vids = list.filter((m) => isVideoFile(m.filename))
  return vids.at(-1) || list.at(-1) || null
}
function patchShotSelectedMaterial(no, selected) {
  const ep = store.currentEpisode
  const shot = (ep?.shots || []).find((s) => String(s.shot_no) === String(no))
  if (shot) shot.selected_material = selected
}
async function setCurrent(no, m) {
  if (isVirtualMaterial(m)) return
  rowState[no].chosen = m
  saveMaterialSelections()
  patchShotSelectedMaterial(no, { bid: m.bid, filename: m.filename, kind: m.kind || '', updated_at: Date.now() / 1000 })
  if (!store.current?.id || !store.currentEpisodeId) return
  try {
    const res = await api.saveShotMaterial(store.current.id, {
      episode_id: store.currentEpisodeId,
      shot_no: no,
      bid: m.bid,
      filename: m.filename,
      kind: m.kind || '',
    })
    if (res?.selected_material) patchShotSelectedMaterial(no, res.selected_material)
  } catch (e) {
    message.error(`当前素材保存失败：${e.message}`)
  }
}

const previewMode = ref('image')
function previewMaterial(no) {
  const list = (materialsByShot[no] || []).filter((m) => !isVirtualMaterial(m))
  const matched = previewMode.value === 'video'
    ? list.filter((m) => isVideoFile(m.filename))
    : list.filter((m) => !isVideoFile(m.filename))
  return matched.at(-1) || null
}
function openPreviewViewer(no) {
  const m = previewMaterial(no)
  if (!m) return
  viewerMat.value = m
  viewerShot.value = no
  showViewer.value = true
}

function snap(no) { return continuityState.value?.shots?.[no] || null }
function continuityImg(filename) {
  if (!filename || !store.current) return ''
  const v = encodeURIComponent(continuityState.value?.updated_at || Date.now())
  return `${api.continuityImageUrl(store.current.id, filename)}?v=${v}`
}
function complexDirectorShot(s) {
  const text = [s?.scene, s?.action, s?.camera, s?.handoff, s?.dialogue].filter(Boolean).join(' ').toLowerCase()
  const kws = ['打斗', '战斗', '搏斗', '追逐', '枪战', '刀战', '爆炸', '复杂运镜', '长镜头', '环绕', '跟拍', '多机位', '复杂剧情', '多线', '反转', '关键转折', '多人对峙', '群像', 'fight', 'battle', 'chase', 'orbit', 'tracking']
  if (kws.some((kw) => text.includes(kw.toLowerCase()))) return true
  const chars = Array.isArray(s?.characters) ? s.characters.length : 0
  const props = Array.isArray(s?.props) ? s.props.length : 0
  return chars >= 4 || (chars >= 3 && props >= 2)
}
function bridgeKey(prev, next) { return `${prev?.shot_no || 'none'}>${next?.shot_no || 'none'}` }
function bridgeDecision(nextNo) {
  const d = manualBridge[nextNo]
    || (tb.continuityMode === 'manual' ? defaultBridgeDecision() : (snap(nextNo)?.decision || statusByShot[nextNo]?.decision || null))
  if (!tb.directorBoardOnly || !d) return d
  if (!complexDirectorShot(shots.value.find((s) => s.shot_no === nextNo))) {
    return {
      ...d,
      scene_cut: false,
      use_tail_frame: false,
      use_staging: true,
      use_director_board: false,
      strategy: 'staging',
      reason: '非复杂镜头不使用导演图，改用站位图+资产图',
    }
  }
  return {
    ...d,
    scene_cut: false,
    use_tail_frame: false,
    use_staging: false,
    use_director_board: true,
    strategy: 'director_board',
    reason: '复杂镜头导演图：导演图作为参考图之一参与视频引导',
  }
}
function bridgeSelected(nextNo, key) {
  const d = bridgeDecision(nextNo)
  return !!d?.[key]
}
function bridgeReady(prev, next, key) {
  if (key === 'use_tail_frame') return !!snap(prev?.shot_no)?.tail_frame
  if (key === 'use_staging') return !!snap(next?.shot_no)?.staging_image
  if (key === 'use_director_board') return !!snap(next?.shot_no)?.director_board
  return false
}
function bridgeThumb(prev, next, key) {
  if (key === 'use_tail_frame') return continuityImg(snap(prev?.shot_no)?.tail_frame)
  if (key === 'use_staging') return continuityImg(snap(next?.shot_no)?.staging_image)
  if (key === 'use_director_board') return continuityImg(snap(next?.shot_no)?.director_board)
  return ''
}
function bridgeItemLabel(key) {
  if (key === 'use_tail_frame') return '上一镜尾帧'
  if (key === 'use_staging') return '站位图'
  if (key === 'use_director_board') return '导演图'
  return '连续性参考'
}
function bridgeItemDisabled(key) {
  return false
}
function bridgeItemState(prev, next, key) {
  const selected = bridgeSelected(next?.shot_no, key)
  const ready = bridgeReady(prev, next, key)
  if (selected && ready) return '已选中 · 将上传'
  if (selected && !ready) return '已选中 · 缺图'
  if (!selected && ready) return '已生成 · 未选'
  return '未生成'
}
function bridgeDecisionMode(nextNo) {
  if (manualBridge[nextNo]) return '手动覆盖'
  return tb.continuityMode === 'manual' ? '手动' : '自动'
}
function ensureManualBridge(nextNo) {
  if (!manualBridge[nextNo]) {
    manualBridge[nextNo] = {
      ...defaultBridgeDecision(),
      ...(snap(nextNo)?.decision || statusByShot[nextNo]?.decision || {}),
      source: 'manual',
    }
  }
  return manualBridge[nextNo]
}
async function toggleBridge(prev, next, key) {
  const d = ensureManualBridge(next.shot_no)
  d[key] = !d[key]
  d.scene_cut = !(d.use_tail_frame || d.use_staging || d.use_director_board)
  d.reason = 'Manual continuity bridge selection'
  saveManualBridge()
  if (d[key]) await prepareBridgeAsset(prev, next, key)
  await refreshVideoPrompts([next.shot_no])
}
function setBridgeBusy(prev, next, action) { bridgeBusy[bridgeKey(prev, next)] = action }
function clearBridgeBusy(prev, next) { delete bridgeBusy[bridgeKey(prev, next)] }
function isBridgeBusy(prev, next, action) { return bridgeBusy[bridgeKey(prev, next)] === action }
async function prepareBridgeAsset(prev, next, key) {
  if (!store.current || bridgeReady(prev, next, key)) return
  if (key === 'use_tail_frame') await extractBridgeTail(prev, next)
  else if (key === 'use_staging') await genBridgeStaging(prev, next)
  else if (key === 'use_director_board') await genBridgeDirector(prev, next)
}
async function extractBridgeTail(prev, next) {
  const mat = currentMaterial(prev.shot_no)
  if (!mat || !isVideoFile(mat.filename)) {
    message.warning('上一镜还没有可抽取尾帧的视频')
    return
  }
  setBridgeBusy(prev, next, 'tail')
  try {
    await api.extractTailFrame(store.current.id, {
      shot_no: prev.shot_no, bid: mat.bid, filename: mat.filename,
    })
    await reload()
    message.success(`${prev.shot_no} 尾帧已准备`)
  } catch (e) { message.error(e.message) } finally { clearBridgeBusy(prev, next) }
}
function prevStateForBridge(prev) {
  return snap(prev?.shot_no) || null
}
async function genBridgeStaging(prev, next) {
  const replacing = !!snap(next?.shot_no)?.staging_image
  setBridgeBusy(prev, next, 'staging')
  try {
    await api.genStaging(store.current.id, {
      shot: next, prev_state: prevStateForBridge(prev), aspect_ratio: tb.aspect,
      model: tb.model || undefined,
    })
    await reload()
    await refreshVideoPrompts([next.shot_no])
    message.success(`${next.shot_no} 站位图已${replacing ? '重新生成并覆盖' : '生成'}`)
  } catch (e) { message.error(e.message) } finally { clearBridgeBusy(prev, next) }
}
async function genBridgeDirector(prev, next) {
  const replacing = !!snap(next?.shot_no)?.director_board
  setBridgeBusy(prev, next, 'director')
  try {
    await api.genDirectorBoard(store.current.id, {
      shot: next, prev_state: prevStateForBridge(prev), aspect_ratio: tb.aspect,
      model: tb.model || undefined,
    })
    await reload()
    await refreshVideoPrompts([next.shot_no])
    message.success(`${next.shot_no} 导演图已${replacing ? '重新生成并覆盖' : '生成'}`)
  } catch (e) { message.error(e.message) } finally { clearBridgeBusy(prev, next) }
}
async function decideBridge(prev, next) {
  setBridgeBusy(prev, next, 'decide')
  try {
    await api.decideHandoff(store.current.id, {
      shot: next, prev_state: prevStateForBridge(prev),
      use_llm: tb.decisionLlm, model: tb.model || undefined, commit: true,
    })
    await reload()
    await refreshVideoPrompts([next.shot_no])
    message.success(`${prev.shot_no} -> ${next.shot_no} 连续性决策完成`)
  } catch (e) { message.error(e.message) } finally { clearBridgeBusy(prev, next) }
}
const bridgeItems = ['use_tail_frame', 'use_staging', 'use_director_board']

function bridgePairs() {
  const list = []
  for (let i = 0; i < shots.value.length - 1; i++) {
    list.push({ prev: shots.value[i], next: shots.value[i + 1] })
  }
  return list
}
async function runContinuityBatch(action) {
  const pairs = bridgePairs()
  if (!pairs.length) { message.warning('当前分集没有可处理的桥接点'); return }
  continuityBatchBusy.value = action
  let ok = 0, fail = 0
  try {
    for (const pair of pairs) {
      try {
        if (action === 'decide') await decideBridge(pair.prev, pair.next)
        else if (action === 'tail') await extractBridgeTail(pair.prev, pair.next)
        else if (action === 'staging') await genBridgeStaging(pair.prev, pair.next)
        else if (action === 'director') await genBridgeDirector(pair.prev, pair.next)
        ok++
      } catch { fail++ }
    }
    message.success(`连续性批量处理完成：成功 ${ok}${fail ? `，失败 ${fail}` : ''}`)
  } finally {
    continuityBatchBusy.value = ''
    await reload()
  }
}
function clearManualBridge() {
  Object.keys(manualBridge).forEach((k) => { delete manualBridge[k] })
  saveManualBridge()
  message.success('已清空本集手动桥接选择')
}
function setContinuityMode(mode) { tb.continuityMode = mode }
function resetContinuitySettings() {
  Object.assign(tb, CONTINUITY_DEFAULTS)
  saveContinuitySettings()
  message.success('连续性设置已恢复默认')
}

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
function sourceSeqs(s) {
  if (Array.isArray(s.src_seq) && s.src_seq.length) return s.src_seq.filter((v) => v != null)
  if (s.seq != null) return [s.seq]
  return []
}
function sourceSeqLabel(s) {
  const seqs = sourceSeqs(s)
  if (!seqs.length) return ''
  if (seqs.length === 1) return String(seqs[0])
  return `${seqs[0]}-${seqs[seqs.length - 1]}`
}
// 优先用拆解时落库的完整 src_text；旧数据无 src_text 时按 src_seq/seq 回退拼接源片段。
function sourceText(s) {
  const stored = (s.src_text && s.src_text.trim()) || ''
  if (stored) return stored
  const parts = sourceSeqs(s).map((seq) => segMap.value[seq]).filter(Boolean)
  return parts.join('\n')
}
function resolvedText(s) {
  return [subtitleText(s), actionText(s)].filter(Boolean).join(' ') || stripMark(s.scene) || ''
}
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
      decision_llm: tb.decisionLlm, director_board_only: tb.directorBoardOnly,
    })
    const manualPayload = manualContinuityPayload()
    if (Object.keys(manualPayload).length) p.manual_continuity = manualPayload
  }
  return p
}

function manualContinuityPayload() {
  const out = {}
  for (const s of shots.value) {
    const d = manualBridge[s.shot_no]
    if (!d) continue
    if (tb.directorBoardOnly) {
      const complex = complexDirectorShot(s)
      out[s.shot_no] = {
        ...defaultBridgeDecision(),
        ...d,
        use_tail_frame: false,
        use_staging: !complex,
        use_director_board: complex,
        scene_cut: false,
        strategy: complex ? 'director_board' : 'staging',
        source: 'manual',
      }
      continue
    }
    out[s.shot_no] = {
      ...defaultBridgeDecision(),
      ...d,
      scene_cut: !(d.use_tail_frame || d.use_staging || d.use_director_board),
      strategy: d.use_tail_frame ? 'tail_frame'
        : d.use_staging ? 'staging'
          : d.use_director_board ? 'director_board'
            : 'scene_cut',
      source: 'manual',
    }
  }
  return out
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
    rowState[no].batchId = b.id
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

function activeBatchIdsForShot(no) {
  const ids = []
  const directId = rowState[no]?.batchId
  if (directId) {
    const summary = batches.value.find((b) => b.id === directId)
    if (rowState[no]?.running || ['running', 'pending'].includes(summary?.status)) ids.push(directId)
  }
  for (const full of Object.values(fullBatchCache)) {
    if (!full || !isEpisodeBatch(full)) continue
    if (!['running', 'pending'].includes(full.status)) continue
    if ((full.tasks || []).some((t) => t.shot_no === no && ['running', 'pending', 'paused'].includes(t.status))) {
      ids.push(full.id)
    }
  }
  return ids
}

function isShotActive(no) {
  const st = statusByShot[no]
  return ['running', 'pending'].includes(st?.status) || activeBatchIdsForShot(no).length > 0
}

async function stopBatches(ids, label = '当前任务') {
  const unique = [...new Set(ids.filter(Boolean))]
  if (!unique.length) {
    message.info('没有正在运行的生成任务')
    return
  }
  try {
    await Promise.all(unique.map((bid) => api.pauseBatch(store.current.id, bid)))
    message.success(`已请求停止${label}，当前已提交给上游的单个任务会完成后停止后续队列`)
    await tasks.ensureBatchPolling({ immediate: true, maxMinutes: tb.batchPollMaxMinutes })
    await rebuildMaterials()
  } catch (e) {
    message.error('停止失败: ' + e.message)
  }
}

function stopActiveBatches() {
  return stopBatches(genProgress.batchIds, '当前分集生成')
}

function stopShot(no) {
  return stopBatches(activeBatchIdsForShot(no), `${no} 生成`)
}

function stopAllProjectBatches() {
  const ids = batches.value
    .filter((b) => ['running', 'pending'].includes(b.status))
    .map((b) => b.id)
  return stopBatches(ids, '全部生成')
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
const toolbarCollapsed = ref(false)
// continuity (pipeline) drawer
const showPipeline = ref(false)
const continuityBatchBusy = ref('')
const continuitySummary = computed(() => {
  const rows = shots.value.slice(1)
  let decided = 0, tail = 0, staging = 0, director = 0
  rows.forEach((s) => {
    const d = bridgeDecision(s.shot_no)
    if (d) decided++
    if (d?.use_tail_frame) tail++
    if (d?.use_staging) staging++
    if (d?.use_director_board) director++
  })
  return { total: rows.length, decided, tail, staging, director }
})

// bottom-bar 批量生成 dropdown
const batchMenuOptions = computed(() => [
  { label: inferAllRunning.value ? '批量推理中...' : '批量推理', key: 'infer', disabled: inferAllRunning.value },
  { label: tasks.batchActive ? '生成运行中' : '批量生图', key: 'image', disabled: tasks.batchActive },
  { label: tasks.batchActive ? '生成运行中' : '批量生视频', key: 'video', disabled: tasks.batchActive },
])
function onBatchMenu(key) {
  if (key === 'infer') return inferAll()
  return genBatch(key)
}
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

    <EpisodeBar v-if="store.current" @open-errors="openEpisodeErrors" />

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
          <em v-if="genProgress.stageLabel">· {{ genProgress.stageLabel }}{{ genProgress.runningProgress ? ` ${genProgress.runningProgress}%` : '' }}</em>
        </span>
        <span class="gp-elapsed"><n-icon :component="TimeOutline" /> 已用 {{ elapsedText }}</span>
        <button class="gp-stop" type="button" :disabled="genProgress.stopping" @click="stopActiveBatches">
          <n-icon :component="PauseOutline" /> {{ genProgress.stopping ? '停止中' : '停止' }}
        </button>
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
        <template v-for="(s, idx) in shots" :key="s.shot_no">
        <div class="row" :class="{ locked: locks.has(s.shot_no) }">
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
              原文<span v-if="sourceSeqLabel(s)"> {{ sourceSeqLabel(s) }}</span>
            </button>
            <div v-if="openSrc[s.shot_no] && sourceText(s)" class="src-text">{{ sourceText(s) }}</div>
            <!-- @引用参考图缩略图：放在原文区域下方作「本镜引用」图例；仅渲染资产库里
                 校验一致（确实能被调用、已有参考图）的资产，仅作附加显示不传输到生成端。
                 单击放大（n-image 内置灯箱）/ 长按拖动重排。 -->
            <div v-if="promptRefs(s.shot_no).length" class="ref-strip">
              <span class="ref-strip-cap" title="仅显示资产库中校验一致、可被调用的引用；缩略图不会传输到生成端">本镜引用</span>
              <div
                v-for="(r, ri) in promptRefs(s.shot_no)"
                :key="refKey(r, ri)"
                class="ref-thumb"
                :class="{ dragging: dragRef.no === s.shot_no && dragRef.from === ri, dropover: dragRef.no === s.shot_no && dragRef.over === ri && dragRef.from !== ri }"
                :draggable="!r.engine"
                :title="r.engine ? `${refDisplay(r)}：已编入 reference_image_urls，将随视频请求上传` : `${r.trigger || '@'}${r.name}（长按拖动可调整顺序 · 单击放大）`"
                @dragstart="!r.engine && onRefDragStart(s.shot_no, ri, $event)"
                @dragover.prevent="onRefDragOver(s.shot_no, ri)"
                @drop.prevent="onRefDrop(s.shot_no, ri)"
                @dragend="resetDragRef"
              >
                <n-image
                  :src="r.url"
                  :width="34"
                  :height="34"
                  object-fit="cover"
                  class="ref-img"
                  :draggable="false"
                />
                <span class="ref-name">{{ refDisplay(r) }}</span>
              </div>
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
                <n-button size="tiny" quaternary :loading="rowState[s.shot_no].fallbacking" title="切换为引擎兜底提示词并保存" @click="useFallbackPrompt(s.shot_no)">
                  <template #icon><n-icon :component="RefreshOutline" /></template>兜底词
                </n-button>
                <n-button size="tiny" :loading="rowState[s.shot_no].inferring" @click="inferRow(s.shot_no)">
                  <template #icon><n-icon :component="SparklesOutline" /></template>AI推理
                </n-button>
                <n-button v-if="rowState[s.shot_no].running || isShotActive(s.shot_no)" size="tiny" type="warning" @click="stopShot(s.shot_no)">
                  <template #icon><n-icon :component="PauseOutline" /></template>停止
                </n-button>
                <n-button v-else-if="rowState[s.shot_no].mode === 'image'" size="tiny" type="primary" @click="genRow(s.shot_no, 'image')">
                  <template #icon><n-icon :component="ImageOutline" /></template>生成图片
                </n-button>
                <n-button v-else size="tiny" type="primary" @click="genRow(s.shot_no, 'video')">
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
                    <button
                      class="st-badge"
                      :class="`st-${statusByShot[s.shot_no].status}`"
                      :title="statusByShot[s.shot_no].status === 'error' ? '查看本镜错误详情' : ''"
                      @click.stop="openShotError(s.shot_no)"
                    >
                      <n-spin v-if="statusByShot[s.shot_no].status === 'running'" :size="11" />
                      <n-icon v-else :component="statusMeta(s.shot_no).icon" />
                      <span>{{ statusMeta(s.shot_no).label }}</span>
                      <small v-if="shotProgressText(s.shot_no)">{{ shotProgressText(s.shot_no) }}</small>
                    </button>
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
                  :class="{ active: m && !isVirtualMaterial(m) && materialSame(currentMaterial(s.shot_no), m), empty: !m, generating: isVirtualMaterial(m) }"
                  @click="m && !isVirtualMaterial(m) && setCurrent(s.shot_no, m)"
                >
                  <template v-if="isVirtualMaterial(m)">
                    <div class="opt-generating">
                      <n-spin :size="16" />
                      <span>{{ materialStatusLabel(m) }}</span>
                      <small>{{ materialProgress(m) }}%</small>
                      <n-progress type="line" :percentage="materialProgress(m)" :height="5" :border-radius="3" :show-indicator="false" processing />
                    </div>
                  </template>
                  <template v-else-if="m">
                    <video v-if="isVideoFile(m.filename)" :src="matUrl(m)" muted class="opt-media" />
                    <img v-else :src="matUrl(m)" class="opt-media" />
                  </template>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="tb.continuity && idx < shots.length - 1" class="continuity-bridge">
          <div class="cb-rail">
            <span class="cb-node">{{ s.shot_no }}</span>
            <span class="cb-line"></span>
            <n-icon :component="GitMergeOutline" />
            <span class="cb-line"></span>
            <span class="cb-node">{{ shots[idx + 1].shot_no }}</span>
          </div>
          <div class="cb-main">
            <div class="cb-head">
              <div>
                <div class="cb-title">连续性桥接</div>
                <div class="cb-sub">
                  {{ tb.continuityMode === 'manual' ? '手动选择传给下一镜的连续性参考' : (bridgeDecision(shots[idx + 1].shot_no)?.reason || '等待自动分析结果') }}
                </div>
              </div>
              <div class="cb-actions">
                <n-tag size="tiny" :type="tb.continuityMode === 'manual' ? 'warning' : 'info'" :bordered="false">
                  {{ tb.continuityMode === 'manual' ? '手动' : '自动' }}
                </n-tag>
                <n-button v-if="tb.continuityMode !== 'manual'" size="tiny" :loading="isBridgeBusy(s, shots[idx + 1], 'decide')" @click="decideBridge(s, shots[idx + 1])">
                  分析
                </n-button>
              </div>
            </div>
            <div class="cb-items">
              <button
                v-for="key in bridgeItems"
                :key="key"
                class="cb-item"
                :class="{ selected: bridgeSelected(shots[idx + 1].shot_no, key), disabled: bridgeItemDisabled(key) }"
                :disabled="bridgeItemDisabled(key)"
                @click="toggleBridge(s, shots[idx + 1], key)"
              >
                <span class="cb-mark"><n-icon v-if="bridgeSelected(shots[idx + 1].shot_no, key)" :component="CheckmarkCircleOutline" /></span>
                <span class="cb-thumb" :class="{ empty: !bridgeThumb(s, shots[idx + 1], key) }">
                  <img v-if="bridgeThumb(s, shots[idx + 1], key)" :src="bridgeThumb(s, shots[idx + 1], key)" />
                  <n-icon v-else :component="key === 'use_tail_frame' ? FilmOutline : ImageOutline" />
                </span>
                <span class="cb-label">{{ bridgeItemLabel(key) }}</span>
                <span class="cb-state strong">{{ bridgeItemState(s, shots[idx + 1], key) }}</span>
                <span class="cb-state">{{ bridgeReady(s, shots[idx + 1], key) ? '已就绪' : '未生成' }}</span>
              </button>
            </div>
            <div class="cb-tools">
              <n-button size="tiny" :loading="isBridgeBusy(s, shots[idx + 1], 'tail')" @click="extractBridgeTail(s, shots[idx + 1])">抽取尾帧</n-button>
              <n-button size="tiny" :loading="isBridgeBusy(s, shots[idx + 1], 'staging')" @click="genBridgeStaging(s, shots[idx + 1])">{{ bridgeReady(s, shots[idx + 1], 'use_staging') ? '重生成站位图' : '生成站位图' }}</n-button>
              <n-button size="tiny" :loading="isBridgeBusy(s, shots[idx + 1], 'director')" @click="genBridgeDirector(s, shots[idx + 1])">{{ bridgeReady(s, shots[idx + 1], 'use_director_board') ? '重生成导演图' : '生成导演图' }}</n-button>
            </div>
          </div>
        </div>
        </template>
      </n-scrollbar>

      <!-- bottom toolbar: compact centered pill -->
      <Transition name="toolbar-morph">
        <button
          v-if="toolbarCollapsed"
          key="dock"
          class="toolbar-dock"
          type="button"
          title="展开快捷栏"
          @click="toolbarCollapsed = false"
        >
          <n-icon :component="PlayOutline" />
        </button>
        <div v-else key="bar" class="toolbar">
          <div class="pill">
            <button class="pbtn icon-only collapse-btn" type="button" title="收纳快捷栏" @click="toolbarCollapsed = true">
              <n-icon :component="ChevronForwardOutline" />
            </button>
            <button class="pbtn" @click="showPreview = true"><n-icon :component="EyeOutline" /><span>分镜预览</span></button>
            <button class="pbtn accent" :disabled="!playlistItems.length" @click="openPlaylist"><n-icon :component="PlayOutline" /><span>顺播检查</span></button>
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
                <div class="pp-actions">
                  <n-button size="small" quaternary @click="resetTb">恢复默认</n-button>
                  <span class="pp-tip">这里仅保留通用生成参数；连续性策略、批量分析和桥接素材控制请在「连续性」面板中设置。</span>
                </div>
              </div>
            </n-popover>
            <n-dropdown trigger="click" placement="top" :options="batchMenuOptions" @select="onBatchMenu">
              <button class="pbtn accent"><n-icon :component="FlashOutline" /><span>批量生成</span><n-icon :component="ChevronDownOutline" size="12" /></button>
            </n-dropdown>
            <button v-if="tasks.batchActive" class="pbtn danger" @click="stopAllProjectBatches"><n-icon :component="PauseOutline" /><span>停止全部</span></button>
            <button class="pbtn" @click="openEpiGen"><n-icon :component="AlbumsOutline" /><span>多集并发</span></button>
            <button class="pbtn" :disabled="exportingJy" @click="exportJianying"><n-icon :component="FilmOutline" /><span>{{ exportingJy ? '导出中…' : '导入剪映' }}</span></button>
            <span v-if="locks.size" class="lock-pill">🔒 {{ locks.size }}</span>
          </div>
        </div>
      </Transition>
    </template>

    <!-- preview storyboard modal -->
    <Teleport to="body">
    <div v-if="showPreview" class="story-preview-overlay" @click.self="showPreview = false">
      <section class="story-preview-card" role="dialog" aria-modal="true" @click.stop>
        <header class="story-preview-head">
          <div class="pv-head">
            <span class="pv-title">预览</span>
            <button class="pv-tab" :class="{ active: previewMode === 'image' }" type="button" @click="previewMode = 'image'">当前图片</button>
            <button class="pv-tab" :class="{ active: previewMode === 'video' }" type="button" @click="previewMode = 'video'">当前视频</button>
            <span class="pv-ep">{{ episode?.name || '当前分集' }}</span>
            <span class="pv-count">{{ shots.length }} 镜</span>
          </div>
          <button class="pv-close" type="button" title="关闭" @click="showPreview = false">×</button>
        </header>
        <div class="pv-scroll">
          <div class="pv-grid">
            <div
              v-for="s in shots"
              :key="s.shot_no"
              class="pv-cell"
              :class="{ clickable: previewMaterial(s.shot_no) }"
              @click="openPreviewViewer(s.shot_no)"
            >
              <div class="pv-no">
                {{ s.shot_no }}
                <span v-if="previewMaterial(s.shot_no)" class="pv-tag">{{ previewMode === 'video' ? '视频' : '图片' }}</span>
              </div>
              <div class="pv-frame">
                <template v-if="previewMaterial(s.shot_no)">
                  <video v-if="previewMode === 'video'" :src="matUrl(previewMaterial(s.shot_no))" muted class="pv-media" />
                  <img v-else :src="matUrl(previewMaterial(s.shot_no))" class="pv-media" />
                </template>
                <div v-else class="pv-ph">{{ String(s.shot_no || '').replace(/^S?0*/i, '') || '—' }}</div>
              </div>
              <div class="pv-sub">{{ resolvedText(s) }}</div>
            </div>
          </div>
        </div>
      </section>
    </div>
    </Teleport>

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
        <div class="continuity-drawer">
          <div class="cd-hero">
            <div>
              <div class="cd-kicker">连续性引擎</div>
              <div class="cd-hero-title">{{ tb.continuity ? '已接入本集视频生成链路' : '仅展示桥接观察，不参与生成' }}</div>
              <div class="cd-note">两镜之间的桥接框负责承上启下；这里集中管理策略、批量分析和中间素材准备。</div>
            </div>
            <n-switch size="large" v-model:value="tb.continuity" />
          </div>

          <div class="cd-section">
            <div class="cd-title">决策模式</div>
            <div class="cd-segment">
              <button :class="{ on: tb.continuityMode === 'auto' }" @click="setContinuityMode('auto')">自动分析</button>
              <button :class="{ on: tb.continuityMode === 'manual' }" @click="setContinuityMode('manual')">手动决策</button>
            </div>
            <div class="cd-toggles">
              <label><span>复杂镜头导演图</span><n-switch size="small" v-model:value="tb.directorBoardOnly" /></label>
              <label><span>LLM升级决策</span><n-switch size="small" v-model:value="tb.decisionLlm" /></label>
              <label><span>AI复核闸门</span><n-switch size="small" v-model:value="tb.aiReview" /></label>
            </div>
            <div class="cd-note">
              自动模式会按剧情和上一镜状态分析尾帧、站位图、导演图；复杂镜头导演图开启后，仅打斗、复杂运镜、复杂剧情等镜头使用导演图，普通镜头使用站位图+资产图。
            </div>
          </div>

          <div class="cd-section">
            <div class="cd-title">批量控制</div>
            <div class="cd-actions-grid">
              <n-button size="small" :loading="continuityBatchBusy === 'decide'" @click="runContinuityBatch('decide')">批量分析桥接</n-button>
              <n-button size="small" :loading="continuityBatchBusy === 'tail'" @click="runContinuityBatch('tail')">批量抽取尾帧</n-button>
              <n-button size="small" :loading="continuityBatchBusy === 'staging'" @click="runContinuityBatch('staging')">批量站位图</n-button>
              <n-button size="small" :loading="continuityBatchBusy === 'director'" @click="runContinuityBatch('director')">批量导演图</n-button>
              <n-button size="small" quaternary @click="clearManualBridge">清空手动选择</n-button>
              <n-button size="small" quaternary @click="resetContinuitySettings">恢复默认设置</n-button>
              <n-button size="small" quaternary @click="reload">刷新连续性状态</n-button>
            </div>
          </div>

          <div class="cd-section">
            <div class="cd-title">本集总览</div>
            <div class="cd-grid">
              <div><b>{{ continuitySummary.total }}</b><span>桥接点</span></div>
              <div><b>{{ continuitySummary.decided }}</b><span>已决策</span></div>
              <div><b>{{ continuitySummary.tail }}</b><span>尾帧</span></div>
              <div><b>{{ continuitySummary.staging }}</b><span>站位图</span></div>
              <div><b>{{ continuitySummary.director }}</b><span>导演图</span></div>
            </div>
          </div>
        </div>
      </n-drawer-content>
    </n-drawer>

    <!-- generation error drawer -->
    <n-drawer v-model:show="showErrorDrawer" :width="680" placement="right">
      <n-drawer-content :title="errorDrawerTitle" closable :native-scrollbar="false">
        <div class="err-panel">
          <div class="err-summary">
            <span>{{ visibleErrors.length }} 条错误</span>
            <n-button v-if="visibleErrors.length" size="tiny" type="warning" ghost @click="clearVisibleErrors">
              一键清空
            </n-button>
            <n-button v-if="errorDrawerMode === 'shot'" size="tiny" quaternary @click="openEpisodeErrors()">
              查看本集全部
            </n-button>
          </div>
          <n-empty v-if="!visibleErrors.length" description="当前没有收纳的错误" />
          <div v-for="err in visibleErrors" :key="err.id" class="err-item">
            <div class="err-head">
              <span class="err-shot">{{ err.shotNo }}</span>
              <span class="err-kind">{{ err.kind === 'video' ? '视频' : '图片' }}</span>
              <span v-if="err.attempts" class="err-attempt">第{{ err.attempts }}次</span>
            </div>
            <div class="err-msg">{{ err.message }}</div>
            <div class="err-meta">批次：{{ err.batchName || err.batchId }}</div>
          </div>
        </div>
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
.continuity-drawer { display: flex; flex-direction: column; gap: 14px; }
.cd-hero {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  padding: 14px; border: 1px solid color-mix(in srgb, var(--app-accent, #21fe84) 28%, var(--app-border));
  border-radius: 8px; background: color-mix(in srgb, var(--app-accent, #21fe84) 8%, var(--app-surface));
}
.cd-kicker { font-size: 11px; color: var(--app-accent); font-weight: 800; margin-bottom: 4px; }
.cd-hero-title { font-size: 14px; font-weight: 800; color: var(--app-text); }
.cd-section {
  padding: 14px; border: 1px solid var(--app-border); border-radius: 8px;
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
}
.cd-title { font-size: 13px; font-weight: 800; margin-bottom: 10px; }
.cd-segment {
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
  padding: 4px; border: 1px solid var(--app-border); border-radius: 8px; background: var(--app-bg-soft);
}
.cd-segment button {
  border: none; border-radius: 6px; background: transparent; color: var(--app-text-secondary);
  height: 30px; font: inherit; font-size: 12px; cursor: pointer;
}
.cd-segment button.on {
  background: color-mix(in srgb, var(--app-accent, #21fe84) 18%, transparent);
  color: var(--app-accent); font-weight: 800;
}
.cd-toggles { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.cd-toggles label {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 12px;
  min-height: 32px; padding: 6px 8px; border: 1px solid var(--app-border); border-radius: 8px;
  background: var(--app-bg-soft); font-size: 12px; color: var(--app-text-secondary);
}
.cd-toggles label > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cd-actions-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.cd-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.cd-grid > div {
  min-height: 62px; border: 1px solid var(--app-border); border-radius: 8px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: var(--app-bg-soft);
}
.cd-grid b { font-size: 20px; color: var(--app-accent); line-height: 1; }
.cd-grid span { margin-top: 6px; font-size: 11px; color: var(--app-text-muted); }
.cd-note { margin-top: 10px; font-size: 12px; color: var(--app-text-muted); line-height: 1.5; }
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
.continuity-bridge {
  display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 12px;
  align-items: stretch; margin: -2px 10px 10px; padding: 10px 12px;
  border: 1px dashed color-mix(in srgb, var(--app-accent, #46c98b) 38%, var(--app-border));
  border-radius: 8px; background: color-mix(in srgb, var(--app-surface) 72%, transparent);
}
.cb-rail { display: flex; align-items: center; justify-content: center; gap: 6px; color: var(--app-text-secondary); }
.cb-node {
  font-family: var(--font-mono, monospace); font-size: 11px; font-weight: 800;
  color: var(--app-accent); white-space: nowrap;
}
.cb-line { height: 1px; flex: 1; background: color-mix(in srgb, var(--app-accent, #46c98b) 35%, transparent); }
.cb-main { display: grid; grid-template-columns: minmax(220px, 1fr) auto auto; gap: 12px; align-items: center; min-width: 0; }
.cb-head { display: flex; justify-content: space-between; gap: 10px; min-width: 0; }
.cb-title { font-size: 12px; font-weight: 800; color: var(--app-text); }
.cb-sub {
  margin-top: 3px; font-size: 11px; color: var(--app-text-muted);
  line-height: 1.35; max-height: 32px; overflow: hidden;
}
.cb-actions, .cb-tools { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.cb-items { display: flex; gap: 8px; align-items: center; }
.cb-item {
  position: relative; width: 92px; height: 104px; padding: 5px;
  border: 1px solid var(--app-border); border-radius: 8px; background: var(--app-bg-soft);
  color: var(--app-text); cursor: pointer; display: flex; flex-direction: column; gap: 4px;
  align-items: stretch; font-family: inherit; overflow: hidden;
}
.cb-item.disabled { cursor: default; }
.cb-item.selected { border-color: var(--app-accent); box-shadow: 0 0 0 1px var(--app-accent) inset; }
.cb-mark {
  position: absolute; top: 4px; right: 4px; z-index: 2; color: var(--app-accent);
  width: 16px; height: 16px; display: flex; align-items: center; justify-content: center;
}
.cb-thumb {
  height: 46px; border-radius: 6px; overflow: hidden; background: rgba(255,255,255,.05);
  display: flex; align-items: center; justify-content: center; color: var(--app-text-muted);
}
.cb-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cb-label, .cb-state { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: center; }
.cb-label { font-size: 11px; font-weight: 700; }
.cb-state { font-size: 10px; color: var(--app-text-muted); }
.cb-state.strong { color: var(--app-accent); font-weight: 700; }
.cb-state.strong + .cb-state { display: none; }
@media (max-width: 1100px) {
  .continuity-bridge { grid-template-columns: 1fr; }
  .cb-main { grid-template-columns: 1fr; }
  .cb-items { flex-wrap: wrap; }
}
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
  display: flex; flex-wrap: wrap; gap: 6px;
  align-items: flex-start;
  margin-top: 4px;
  padding: 7px 8px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-bg-soft) 72%, transparent);
}
.ref-strip-cap {
  flex-basis: 100%;
  font-size: 11px; color: var(--app-text-muted);
  line-height: 1; white-space: nowrap;
}
.ref-thumb {
  display: flex; flex-direction: column; align-items: center; gap: 3px;
  width: 58px; cursor: grab; user-select: none;
  border-radius: 8px; padding: 3px; transition: background .15s, transform .12s, opacity .12s;
}
.ref-thumb:hover { background: var(--app-accent-soft); }
.ref-thumb:active { cursor: grabbing; }
.ref-thumb.dragging { opacity: .45; transform: scale(.94); }
.ref-thumb.dropover { background: var(--app-accent-soft); box-shadow: inset 0 0 0 2px var(--app-accent); }
.ref-img :deep(img) { border-radius: 6px; }
.ref-name {
  max-width: 56px; font-size: 10px; line-height: 1.1; text-align: center;
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
  border: 1px solid transparent; appearance: none; font-family: inherit;
  line-height: 1.25; cursor: default;
}
.st-badge:hover { border-color: color-mix(in srgb, currentColor 45%, transparent); }
.st-badge :deep(.n-icon) { font-size: 12px; }
.st-badge small {
  font-size: 10px; font-weight: 600; opacity: .82;
  padding-left: 3px; font-variant-numeric: tabular-nums;
}
.st-tip { max-width: 360px; white-space: pre-wrap; line-height: 1.6; }
.st-pending { color: var(--app-text-secondary); background: color-mix(in srgb, var(--app-text-secondary) 12%, transparent); }
.st-running { color: #4098fc; background: color-mix(in srgb, #4098fc 14%, transparent); }
.st-done { color: #46c98b; background: color-mix(in srgb, #46c98b 16%, transparent); }
.st-error { color: #f56c6c; background: color-mix(in srgb, #f56c6c 15%, transparent); cursor: pointer; }
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

.err-panel { display: flex; flex-direction: column; gap: 12px; }
.err-summary {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  color: var(--app-text-secondary); font-size: 12px;
}
.err-item {
  padding: 12px; border-radius: 10px;
  border: 1px solid color-mix(in srgb, #f56c6c 28%, var(--app-border));
  background: color-mix(in srgb, #f56c6c 9%, var(--app-surface));
}
.err-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.err-shot { font-family: var(--font-mono, monospace); color: #ff7b86; font-weight: 800; }
.err-kind, .err-attempt {
  font-size: 11px; padding: 1px 7px; border-radius: 999px;
  color: var(--app-text-secondary); background: rgba(255,255,255,.06);
}
.err-msg { white-space: pre-wrap; line-height: 1.65; font-size: 13px; color: var(--app-text); }
.err-meta { margin-top: 8px; font-size: 11px; color: var(--app-text-muted); }

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
.gp-stop {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border-radius: 6px;
  border: 1px solid color-mix(in srgb, #ffb454 55%, transparent);
  background: color-mix(in srgb, #ffb454 14%, transparent);
  color: #ffca7a;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.gp-stop:hover:not(:disabled) {
  background: color-mix(in srgb, #ffb454 22%, transparent);
}
.gp-stop:disabled {
  opacity: .58;
  cursor: default;
}
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
.story-preview-overlay {
  position: fixed; inset: 0; z-index: 4000;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.46);
  backdrop-filter: blur(2px);
}
.story-preview-card {
  display: flex; flex-direction: column;
  width: min(62vw, 1160px);
  min-width: min(760px, calc(100vw - 48px));
  max-height: min(82vh, 800px);
  overflow: hidden;
  border-radius: 12px;
  border: 1px solid var(--app-border);
  background: color-mix(in srgb, var(--app-surface) 96%, #000);
  box-shadow: 0 22px 60px rgba(0,0,0,.45);
}
.story-preview-head {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 18px 20px 12px;
}
.pv-close {
  flex-shrink: 0; width: 30px; height: 30px; border-radius: 8px;
  border: 1px solid transparent; background: transparent; color: var(--app-text-muted);
  font-size: 24px; line-height: 1; cursor: pointer;
}
.pv-close:hover {
  color: var(--app-text); background: var(--app-bg-soft); border-color: var(--app-border);
}
.pv-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.pv-title { margin-right: 6px; font-size: 18px; font-weight: 900; color: var(--app-text); white-space: nowrap; }
.pv-tab {
  height: 30px; padding: 0 14px; border-radius: 6px;
  border: 1px solid var(--app-border); background: var(--app-bg-soft);
  color: var(--app-text-secondary); font-size: 12px; font-weight: 800; cursor: pointer;
}
.pv-tab.active {
  border-color: transparent; background: #1688ff; color: #fff;
}
.pv-ep {
  margin-left: 8px;
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 12px; color: var(--app-text-muted);
}
.pv-count {
  flex-shrink: 0; font-size: 12px; color: var(--app-accent); font-weight: 800;
  padding: 2px 8px; border-radius: 999px; background: color-mix(in srgb, var(--app-accent) 14%, transparent);
}
.pv-scroll {
  flex: 1; min-height: 0;
  max-height: calc(min(82vh, 800px) - 60px);
  overflow: auto;
  padding: 10px 20px 20px;
}
.pv-tag {
  margin-left: 6px; font-size: 10px; padding: 1px 5px; border-radius: 4px;
  background: rgba(33, 254, 132, 0.2); color: var(--app-accent); font-weight: 700;
}
.pv-cell.clickable { cursor: pointer; transition: border-color 0.15s, transform 0.15s; }
.pv-cell.clickable:hover { border-color: var(--app-accent); transform: translateY(-1px); }
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
.opt-cell.generating {
  border: 1px solid color-mix(in srgb, var(--app-accent) 48%, transparent);
  background: color-mix(in srgb, var(--app-surface) 72%, transparent);
  cursor: default;
}
.opt-media { width: 100%; height: 100%; object-fit: cover; display: block; }
.opt-generating {
  height: 100%;
  display: grid;
  align-content: center;
  justify-items: center;
  gap: 5px;
  padding: 10px;
  color: var(--app-text-primary);
  font-size: 12px;
}
.opt-generating small {
  color: var(--app-text-secondary);
  font-size: 11px;
}
.opt-generating :deep(.n-progress) { width: 100%; }

/* ── bottom toolbar: compact centered pill ── */
.toolbar {
  position: sticky; bottom: 12px;
  margin-top: 14px;
  display: flex; justify-content: center;
  z-index: 60;
  pointer-events: none;
  transform-origin: left bottom;
  will-change: transform, opacity, filter;
}
.pill {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 6px 8px;
  background: color-mix(in srgb, var(--app-surface) 96%, transparent);
  backdrop-filter: blur(10px);
  border: 1px solid var(--app-border);
  border-radius: 999px;
  box-shadow: 0 8px 24px rgba(0,0,0,.28);
  pointer-events: auto;
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
.pbtn.danger {
  background: color-mix(in srgb, #ff6b6b 16%, transparent);
  color: #ff9b9b;
  border: 1px solid color-mix(in srgb, #ff6b6b 34%, transparent);
}
.pbtn.danger:hover {
  background: color-mix(in srgb, #ff6b6b 24%, transparent);
  color: #ffd0d0;
}
.pbtn:disabled { opacity: .45; cursor: not-allowed; }
.pbtn.icon-only {
  width: 34px; height: 34px; padding: 0; justify-content: center;
}
.collapse-btn {
  margin-right: 4px;
  background: rgba(0, 0, 0, .28);
  color: var(--app-accent);
  border: 1px solid color-mix(in srgb, var(--app-border) 75%, transparent);
}
.collapse-btn :deep(.n-icon) { transform: rotate(180deg); }
.toolbar-dock {
  position: fixed;
  left: 22px;
  bottom: 22px;
  z-index: 80;
  width: 48px;
  height: 48px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 999px;
  background: var(--app-accent);
  color: #04130b;
  box-shadow: 0 12px 34px rgba(0,0,0,.38);
  cursor: pointer;
  transform-origin: center;
  will-change: transform, opacity, filter;
}
.toolbar-dock:hover { filter: brightness(1.06); }
.toolbar-dock :deep(.n-icon) { font-size: 22px; }
.toolbar-morph-enter-active,
.toolbar-morph-leave-active {
  transition:
    transform .22s cubic-bezier(.22, .9, .3, 1),
    opacity .16s ease,
    filter .22s ease;
}
.toolbar-morph-enter-from.toolbar,
.toolbar-morph-leave-to.toolbar {
  opacity: 0;
  transform: translate(-22vw, 10px) scale(.72);
  filter: blur(2px);
}
.toolbar-morph-enter-to.toolbar,
.toolbar-morph-leave-from.toolbar {
  opacity: 1;
  transform: translate(0, 0) scale(1);
  filter: blur(0);
}
.toolbar-morph-enter-from.toolbar-dock,
.toolbar-morph-leave-to.toolbar-dock {
  opacity: 0;
  transform: translate(22vw, -10px) scale(.72);
  filter: blur(2px);
}
.toolbar-morph-enter-to.toolbar-dock,
.toolbar-morph-leave-from.toolbar-dock {
  opacity: 1;
  transform: translate(0, 0) scale(1) rotate(0);
  filter: blur(0);
}
@media (prefers-reduced-motion: reduce) {
  .toolbar-morph-enter-active,
  .toolbar-morph-leave-active {
    transition: opacity .12s ease;
  }
  .toolbar-morph-enter-from.toolbar,
  .toolbar-morph-leave-to.toolbar,
  .toolbar-morph-enter-from.toolbar-dock,
  .toolbar-morph-leave-to.toolbar-dock {
    transform: none;
    filter: none;
  }
}
.lock-pill { font-size: 12px; color: #ffb454; padding: 0 6px; }
.params-pop { display: flex; flex-direction: column; gap: 10px; min-width: 230px; }
.pp-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.pp-l { font-size: 12.5px; color: var(--app-text-secondary); }
.pp-tip { font-size: 11.5px; color: var(--app-text-muted); line-height: 1.5; }

.pv-grid { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 12px; }
.pv-cell {
  min-width: 0; border: 1px solid var(--app-border); border-radius: 8px; padding: 8px;
  background: var(--app-bg-soft);
}
.pv-no { font-family: var(--font-mono, monospace); font-weight: 800; color: var(--app-accent); font-size: 12px; margin-bottom: 6px; }
.pv-frame {
  width: 100%; aspect-ratio: 1 / 1; border-radius: 6px; overflow: hidden;
  background: #080a0c; border: 1px solid var(--app-border);
}
.pv-media, .pv-ph {
  width: 100%; height: 100%; object-fit: cover; display: block;
}
.pv-ph { display: flex; align-items: center; justify-content: center; color: var(--app-text-muted); font-size: 12px; }
.pv-sub { font-size: 11.5px; color: var(--app-text-muted); margin-top: 6px; line-height: 1.45; max-height: 50px; overflow: hidden; }
@media (max-width: 1200px) {
  .story-preview-card { width: min(86vw, 1040px); }
  .pv-grid { grid-template-columns: repeat(4, minmax(120px, 1fr)); }
}
@media (max-width: 760px) {
  .story-preview-overlay { padding: 12px; }
  .story-preview-card { min-width: 0; width: 100%; max-height: 88vh; }
  .story-preview-head { align-items: flex-start; }
  .pv-head { flex-wrap: wrap; }
  .pv-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
