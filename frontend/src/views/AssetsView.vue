<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
const lineStyleOptions = [
  { label: '错位分割十字', value: 'split-cross' },
  { label: '三分构图线', value: 'thirds' },
  { label: '中心十字线', value: 'center-cross' },
  { label: '基础方格', value: 'square' },
  { label: '斜切菱形网', value: 'isometric' },
  { label: '蜂窝蛛网', value: 'spider' },
  { label: '雷达同心圆', value: 'radar' },
]
const lineResolutionOptions = [
  { label: '保持原图', value: 'original' },
  { label: '长边 1080', value: '1080' },
  { label: '长边 720', value: '720' },
  { label: '长边 480', value: '480' },
]
const lineFilterOptions = [
  { label: '无滤镜', value: 'none' },
  { label: '全彩插画', value: 'illustration' },
  { label: '彩色铅笔', value: 'pencil' },
]
const collageLayoutOptions = [
  { label: '方案A 画廊卡片', value: 'gallery' },
  { label: '方案B 清爽设定表', value: 'sheet' },
  { label: '方案C 紧凑索引板', value: 'compact' },
]
const collageSizeOptions = [
  { label: 'AI兼容 3072', value: 3072 },
  { label: '高清 2048', value: 2048 },
  { label: '接近4K 3840', value: 3840 },
  { label: '最高4K 4096', value: 4096 },
]
const collageAspectOptions = [
  { label: '16:9 横向画板', value: '16:9' },
  { label: '4:3 图集画板', value: '4:3' },
  { label: '1:1 方形画板', value: '1:1' },
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

// ── local character line/grid tool ──────────────────────────────────────────
const lineModal = ref(false)
const linePreviewCanvas = ref(null)
const lineBusy = ref(false)
const lineBatchBusy = ref(false)
const lineTarget = ref(null)
const lineMode = ref('single')
const lineOptions = ref({
  style: 'split-cross',
  color: '#ffffff',
  width: 5,
  spacing: 180,
  opacity: 0.72,
  resolution: 'original',
  filter: 'none',
})
const characterRefs = computed(() => assets.value.filter((a) => a.type === 'character' && a.ref_image))
const restorableCharacterRefs = computed(() => characterRefs.value.filter(hasRestorableOriginal))

function openLineTool(a) {
  if (!a?.ref_image) {
    message.warning('这个角色还没有参考图，先生成或导入一张再划线')
    return
  }
  lineMode.value = 'single'
  lineTarget.value = a
  lineModal.value = true
  nextTick(renderLinePreview)
}

function openBatchLineTool() {
  const first = characterRefs.value[0]
  if (!first) {
    message.info('当前没有带参考图的角色')
    return
  }
  lineMode.value = 'batch'
  lineTarget.value = first
  lineModal.value = true
  nextTick(renderLinePreview)
}

function clampNumber(v, min, max, fallback) {
  const n = Number(v)
  if (!Number.isFinite(n)) return fallback
  return Math.min(max, Math.max(min, n))
}

function normalizedLineOptions() {
  return {
    ...lineOptions.value,
    width: clampNumber(lineOptions.value.width, 1, 40, 5),
    spacing: clampNumber(lineOptions.value.spacing, 30, 800, 180),
    opacity: clampNumber(lineOptions.value.opacity, 0.1, 1, 0.72),
  }
}

function assetImageSrc(a) {
  if (!store.current || !a?.ref_image) return ''
  return api.assetImageUrl(store.current.id, a.ref_image)
}

function lineBaseImageName(a) {
  return a?.ref_original_image || a?.ref_image || ''
}

function lineAssetImageSrc(a) {
  const name = lineBaseImageName(a)
  if (!store.current || !name) return ''
  return api.assetImageUrl(store.current.id, name)
}

function hasRestorableOriginal(a) {
  return !!(a?.ref_original_image && a.ref_image && a.ref_original_image !== a.ref_image)
}

function loadCanvasImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('图片加载失败'))
    img.src = src
  })
}

function targetSize(img, resolution) {
  const maxSide = resolution === 'original' ? 0 : Number(resolution || 0)
  if (!maxSide || Math.max(img.width, img.height) <= maxSide) {
    return { width: img.width, height: img.height }
  }
  const scale = maxSide / Math.max(img.width, img.height)
  return { width: Math.round(img.width * scale), height: Math.round(img.height * scale) }
}

function drawProcessedImage(img, opts) {
  const { width, height } = targetSize(img, opts.resolution)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')

  if (opts.filter === 'illustration') {
    ctx.filter = 'contrast(1.16) saturate(1.55) brightness(1.04)'
  } else if (opts.filter === 'pencil') {
    ctx.filter = 'grayscale(0.25) contrast(1.35) saturate(0.82) sepia(0.18)'
  }

  if (opts.style === 'split-cross') {
    drawSplitCross(ctx, width, height, opts, img)
    return canvas
  }

  ctx.drawImage(img, 0, 0, width, height)
  ctx.filter = 'none'
  ctx.save()
  ctx.strokeStyle = opts.color
  ctx.fillStyle = opts.color
  ctx.lineWidth = opts.width
  ctx.globalAlpha = opts.opacity
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  switch (opts.style) {
    case 'center-cross': drawCenterCross(ctx, width, height); break
    case 'square': drawSquareGrid(ctx, width, height, opts.spacing); break
    case 'isometric': drawIsometricGrid(ctx, width, height, opts.spacing); break
    case 'spider': drawSpiderGrid(ctx, width, height, opts.spacing); break
    case 'radar': drawRadarGrid(ctx, width, height, opts.spacing); break
    default: drawThirdsGrid(ctx, width, height, opts.width); break
  }
  ctx.restore()
  return canvas
}

function drawSplitCross(ctx, width, height, opts, img) {
  const gap = opts.width * 2
  const cx = width * 0.52
  const cy = height * 0.38
  const offsetX = Math.max(4, opts.width * 1.6)
  const offsetY = Math.max(6, opts.width * 2.4)
  const sx = img.width / width
  const sy = img.height / height
  ctx.filter = 'none'
  ctx.fillStyle = opts.color
  ctx.globalAlpha = 1
  ctx.fillRect(0, 0, width, height)
  if (opts.filter === 'illustration') ctx.filter = 'contrast(1.16) saturate(1.55) brightness(1.04)'
  if (opts.filter === 'pencil') ctx.filter = 'grayscale(0.25) contrast(1.35) saturate(0.82) sepia(0.18)'
  ctx.drawImage(img, 0, 0, cx * sx, cy * sy, 0, 0, cx, cy)
  ctx.drawImage(img, cx * sx, 0, (width - cx) * sx, cy * sy, cx + gap, offsetY, width - cx - gap, cy)
  ctx.drawImage(img, 0, cy * sy, cx * sx, (height - cy) * sy, offsetX, cy + gap, cx, height - cy - gap)
  ctx.drawImage(img, cx * sx, cy * sy, (width - cx) * sx, (height - cy) * sy, cx + gap + offsetX, cy + gap + offsetY, width - cx - gap, height - cy - gap)
  ctx.filter = 'none'
}

function drawCenterCross(ctx, width, height) {
  ctx.beginPath()
  ctx.moveTo(width / 2, 0); ctx.lineTo(width / 2, height)
  ctx.moveTo(0, height / 2); ctx.lineTo(width, height / 2)
  ctx.stroke()
}

function drawThirdsGrid(ctx, width, height, lineWidth) {
  ctx.beginPath()
  ctx.moveTo(width / 3, 0); ctx.lineTo(width / 3, height)
  ctx.moveTo(width * 2 / 3, 0); ctx.lineTo(width * 2 / 3, height)
  ctx.moveTo(0, height / 3); ctx.lineTo(width, height / 3)
  ctx.moveTo(0, height * 2 / 3); ctx.lineTo(width, height * 2 / 3)
  ctx.stroke()
  ;[[width / 3, height / 3], [width * 2 / 3, height / 3], [width / 3, height * 2 / 3], [width * 2 / 3, height * 2 / 3]].forEach(([x, y]) => {
    ctx.beginPath(); ctx.arc(x, y, lineWidth * 2, 0, Math.PI * 2); ctx.fill()
  })
}

function drawSquareGrid(ctx, width, height, spacing) {
  ctx.beginPath()
  for (let x = 0; x <= width; x += spacing) { ctx.moveTo(x, 0); ctx.lineTo(x, height) }
  for (let y = 0; y <= height; y += spacing) { ctx.moveTo(0, y); ctx.lineTo(width, y) }
  ctx.stroke()
}

function drawIsometricGrid(ctx, width, height, spacing) {
  ctx.beginPath()
  for (let i = -height; i <= width; i += spacing) { ctx.moveTo(i, 0); ctx.lineTo(i + height, height) }
  for (let i = 0; i <= width + height; i += spacing) { ctx.moveTo(i, 0); ctx.lineTo(i - height, height) }
  ctx.stroke()
}

function drawSpiderGrid(ctx, width, height, radius) {
  const rowHeight = radius * Math.sin(Math.PI / 3)
  const colWidth = radius * 1.5
  for (let y = -radius; y < height + radius; y += rowHeight) {
    const rowNum = Math.round(y / rowHeight)
    const xOffset = rowNum % 2 === 0 ? 0 : colWidth / 2 + radius * 0.25
    for (let x = -radius + xOffset; x < width + radius; x += colWidth) {
      for (let i = 0; i < 6; i += 1) {
        const angle = i * (Math.PI * 2 / 6)
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + Math.cos(angle) * radius, y + Math.sin(angle) * radius); ctx.stroke()
      }
      for (let r = 1; r <= 3; r += 1) {
        ctx.beginPath()
        for (let i = 0; i < 6; i += 1) {
          const angle = i * (Math.PI * 2 / 6)
          const px = x + Math.cos(angle) * (radius / 3) * r
          const py = y + Math.sin(angle) * (radius / 3) * r
          if (i === 0) ctx.moveTo(px, py)
          else ctx.lineTo(px, py)
        }
        ctx.closePath(); ctx.stroke()
      }
    }
  }
}

function drawRadarGrid(ctx, width, height, radius) {
  for (let y = 0; y < height + radius; y += radius) {
    for (let x = 0; x < width + radius; x += radius) {
      ctx.beginPath(); ctx.moveTo(x - radius, y); ctx.lineTo(x + radius, y); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(x, y - radius); ctx.lineTo(x, y + radius); ctx.stroke()
      for (let r = 1; r <= 3; r += 1) {
        ctx.beginPath(); ctx.arc(x, y, (radius / 3) * r, 0, Math.PI * 2); ctx.stroke()
      }
    }
  }
}

async function renderLinePreview() {
  if (!lineModal.value || !lineTarget.value || !linePreviewCanvas.value) return
  try {
    const img = await loadCanvasImage(lineAssetImageSrc(lineTarget.value))
    const full = drawProcessedImage(img, normalizedLineOptions())
    const preview = linePreviewCanvas.value
    const maxW = 760
    const maxH = 520
    const scale = Math.min(1, maxW / full.width, maxH / full.height)
    preview.width = Math.max(1, Math.round(full.width * scale))
    preview.height = Math.max(1, Math.round(full.height * scale))
    const pctx = preview.getContext('2d')
    pctx.clearRect(0, 0, preview.width, preview.height)
    pctx.drawImage(full, 0, 0, preview.width, preview.height)
  } catch (e) {
    message.error(e.message || '预览失败')
  }
}

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('导出图片失败'))), 'image/png', 1)
  })
}

async function processedAssetFile(a) {
  const img = await loadCanvasImage(lineAssetImageSrc(a))
  const canvas = drawProcessedImage(img, normalizedLineOptions())
  const blob = await canvasToBlob(canvas)
  const safeName = String(a.name || a.id || 'character').replace(/[\\/:*?"<>|]+/g, '_')
  return new File([blob], `${safeName}_line.png`, { type: 'image/png' })
}

async function saveLineTarget() {
  if (!store.current || !lineTarget.value) return
  lineBusy.value = true
  try {
    const file = await processedAssetFile(lineTarget.value)
    await api.importAssetImage(store.current.id, lineTarget.value.id, file, {
      purpose: 'line',
      base_image: lineBaseImageName(lineTarget.value),
      line_options: normalizedLineOptions(),
    })
    markImageFresh(lineTarget.value.id)
    message.success(`已保存 ${lineTarget.value.trigger}${lineTarget.value.name} 的划线图`)
    await loadAssets()
    lineModal.value = false
  } catch (e) {
    message.error('保存划线图失败: ' + e.message)
  } finally {
    lineBusy.value = false
  }
}

async function batchLineCharacters() {
  if (!store.current) return
  if (lineBatchBusy.value) return
  const list = characterRefs.value
  if (!list.length) {
    message.info('当前没有带参考图的角色')
    return
  }
  lineBatchBusy.value = true
  let done = 0
  try {
    for (const a of list) {
      const file = await processedAssetFile(a)
      await api.importAssetImage(store.current.id, a.id, file, {
        purpose: 'line',
        base_image: lineBaseImageName(a),
        line_options: normalizedLineOptions(),
      })
      markImageFresh(a.id)
      done += 1
    }
    message.success(`已批量转换 ${done} 张角色图`)
    await loadAssets()
    if (lineModal.value) await nextTick(renderLinePreview)
    if (lineMode.value === 'batch') lineModal.value = false
  } catch (e) {
    message.error(`批量转换中断：已完成 ${done}/${list.length}，${e.message}`)
  } finally {
    lineBatchBusy.value = false
  }
}

async function restoreOriginal(a) {
  if (!store.current || !a || !hasRestorableOriginal(a)) return
  importingId.value = a.id
  try {
    await api.restoreAssetOriginal(store.current.id, a.id)
    markImageFresh(a.id)
    message.success(`已恢复 ${a.trigger || '@'}${a.name || ''} 的原图`)
    await loadAssets()
    if (lineTarget.value?.id === a.id) {
      lineTarget.value = assets.value.find((item) => item.id === a.id) || lineTarget.value
      if (lineModal.value) await nextTick(renderLinePreview)
    }
  } catch (e) {
    message.error('恢复原图失败: ' + e.message)
  } finally {
    importingId.value = null
  }
}

async function batchRestoreOriginals() {
  if (!store.current || lineBatchBusy.value) return
  const list = restorableCharacterRefs.value
  if (!list.length) {
    message.info('当前没有需要恢复的角色图')
    return
  }
  lineBatchBusy.value = true
  let done = 0
  try {
    for (const a of list) {
      await api.restoreAssetOriginal(store.current.id, a.id)
      markImageFresh(a.id)
      done += 1
    }
    message.success(`已恢复 ${done} 张角色原图`)
    await loadAssets()
    if (lineModal.value) await nextTick(renderLinePreview)
  } catch (e) {
    message.error(`批量恢复中断：已完成 ${done}/${list.length}，${e.message}`)
  } finally {
    lineBatchBusy.value = false
  }
}

watch(lineOptions, () => {
  if (lineModal.value) nextTick(renderLinePreview)
}, { deep: true })

// ── local character collage board ───────────────────────────────────────────
const collageModal = ref(false)
const collagePreviewCanvas = ref(null)
const collageBusy = ref(false)
const collageSelectedIds = ref([])
const collageAssetName = ref('角色拼接画板')
const collageOptions = ref({
  layout: 'gallery',
  maxSize: 3072,
  aspect: '16:9',
  showTitle: true,
  title: '角色参考图合集',
  background: '#f6f7f8',
  nameColor: '#101318',
  labelColor: '#101318',
  labelSize: 30,
  accentColor: '#16f28b',
})
const selectedCharacterRefs = computed(() => {
  const selected = new Set(collageSelectedIds.value)
  return characterRefs.value.filter((a) => selected.has(a.id))
})

function openCollageTool() {
  if (!characterRefs.value.length) {
    message.info('当前没有带参考图的角色')
    return
  }
  const valid = new Set(characterRefs.value.map((a) => a.id))
  const kept = collageSelectedIds.value.filter((id) => valid.has(id))
  collageSelectedIds.value = kept.length ? kept : characterRefs.value.map((a) => a.id)
  if (!collageAssetName.value.trim()) collageAssetName.value = '角色拼接画板'
  collageModal.value = true
  nextTick(renderCollagePreview)
}

function selectAllCollageCharacters() {
  collageSelectedIds.value = characterRefs.value.map((a) => a.id)
}

function clearCollageCharacters() {
  collageSelectedIds.value = []
}

function collageSize() {
  const maxSize = clampNumber(collageOptions.value.maxSize, 1024, 4096, 3072)
  const aspect = collageOptions.value.aspect || '16:9'
  if (aspect === '1:1') return { width: maxSize, height: maxSize }
  if (aspect === '4:3') return { width: maxSize, height: Math.round(maxSize * 3 / 4) }
  return { width: maxSize, height: Math.round(maxSize * 9 / 16) }
}

function bestGrid(count, width, height) {
  let best = { cols: Math.ceil(Math.sqrt(count)), rows: Math.ceil(count / Math.ceil(Math.sqrt(count))), score: Infinity }
  for (let cols = 1; cols <= count; cols += 1) {
    const rows = Math.ceil(count / cols)
    const cellRatio = (width / cols) / (height / rows)
    const score = Math.abs(Math.log(cellRatio / 1.55)) + rows * 0.015
    if (score < best.score) best = { cols, rows, score }
  }
  return best
}

function drawRoundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2)
  ctx.beginPath()
  ctx.moveTo(x + rr, y)
  ctx.lineTo(x + w - rr, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + rr)
  ctx.lineTo(x + w, y + h - rr)
  ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h)
  ctx.lineTo(x + rr, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - rr)
  ctx.lineTo(x, y + rr)
  ctx.quadraticCurveTo(x, y, x + rr, y)
  ctx.closePath()
}

function drawContain(ctx, img, x, y, w, h) {
  const scale = Math.min(w / img.width, h / img.height)
  const dw = img.width * scale
  const dh = img.height * scale
  ctx.drawImage(img, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh)
}

function fitFont(ctx, text, maxWidth, startSize, minSize = 18) {
  let size = startSize
  while (size > minSize) {
    ctx.font = `700 ${size}px "Microsoft YaHei", "PingFang SC", sans-serif`
    if (ctx.measureText(text).width <= maxWidth) break
    size -= 2
  }
  return size
}

async function buildCollageCanvas(preview = false) {
  const list = selectedCharacterRefs.value
  if (!list.length) throw new Error('请先选择至少一个角色')
  const opts = collageOptions.value
  const board = collageSize()
  const canvas = document.createElement('canvas')
  canvas.width = preview ? Math.round(board.width * Math.min(1, 980 / board.width, 620 / board.height)) : board.width
  canvas.height = preview ? Math.round(board.height * Math.min(1, 980 / board.width, 620 / board.height)) : board.height
  const scale = canvas.width / board.width
  const ctx = canvas.getContext('2d')
  ctx.scale(scale, scale)

  ctx.fillStyle = opts.background || '#f6f7f8'
  ctx.fillRect(0, 0, board.width, board.height)

  const margin = opts.layout === 'compact' ? 72 : 96
  const titleH = opts.showTitle ? 150 : 40
  if (opts.showTitle) {
    ctx.fillStyle = opts.nameColor || '#101318'
    ctx.font = '800 54px "Microsoft YaHei", "PingFang SC", sans-serif'
    ctx.textAlign = 'left'
    ctx.textBaseline = 'middle'
    ctx.fillText(opts.title || '角色参考图合集', margin, 78)
    ctx.fillStyle = opts.accentColor || '#16f28b'
    drawRoundRect(ctx, margin, 120, Math.min(520, board.width - margin * 2), 10, 5)
    ctx.fill()
  }

  const areaX = margin
  const areaY = titleH
  const areaW = board.width - margin * 2
  const areaH = board.height - areaY - margin
  const { cols, rows } = bestGrid(list.length, areaW, areaH)
  const gap = opts.layout === 'compact' ? 26 : 44
  const cellW = (areaW - gap * (cols - 1)) / cols
  const cellH = (areaH - gap * (rows - 1)) / rows
  const images = await Promise.all(list.map(async (a) => ({ asset: a, img: await loadCanvasImage(assetImageSrc(a)) })))

  images.forEach(({ asset, img }, idx) => {
    const col = idx % cols
    const row = Math.floor(idx / cols)
    const x = areaX + col * (cellW + gap)
    const y = areaY + row * (cellH + gap)
    const nameH = opts.layout === 'compact' ? 74 : 92
    const pad = opts.layout === 'compact' ? 14 : 24

    if (opts.layout !== 'compact') {
      ctx.save()
      ctx.shadowColor = 'rgba(15, 18, 24, 0.16)'
      ctx.shadowBlur = 24
      ctx.shadowOffsetY = 10
      drawRoundRect(ctx, x, y, cellW, cellH, 28)
      ctx.fillStyle = opts.layout === 'sheet' ? '#ffffff' : 'rgba(255,255,255,0.92)'
      ctx.fill()
      ctx.restore()
    }

    const imgX = x + pad
    const imgY = y + pad
    const imgW = cellW - pad * 2
    const imgH = cellH - nameH - pad * 1.5
    ctx.save()
    drawRoundRect(ctx, imgX, imgY, imgW, imgH, opts.layout === 'compact' ? 18 : 22)
    ctx.clip()
    ctx.fillStyle = opts.layout === 'compact' ? 'rgba(255,255,255,0.55)' : '#edf0f2'
    ctx.fillRect(imgX, imgY, imgW, imgH)
    drawContain(ctx, img, imgX, imgY, imgW, imgH)
    ctx.restore()

    const name = `${asset.trigger || '@'}${asset.name || ''}`
    const tagX = opts.layout === 'compact' ? x + 8 : x + pad
    const tagY = y + cellH - nameH + 18
    const tagW = cellW - pad * 2
    const baseLabelSize = clampNumber(opts.labelSize, 14, 72, opts.layout === 'compact' ? 28 : 32)
    const fontSize = fitFont(ctx, name, tagW - 36, baseLabelSize, 14)
    if (opts.layout === 'gallery') {
      ctx.fillStyle = opts.accentColor || '#16f28b'
      drawRoundRect(ctx, tagX, tagY, Math.min(tagW, ctx.measureText(name).width + 44), fontSize + 24, 18)
      ctx.fill()
      ctx.fillStyle = opts.labelColor || '#06130d'
    } else {
      ctx.fillStyle = opts.labelColor || opts.nameColor || '#101318'
    }
    ctx.font = `800 ${fontSize}px "Microsoft YaHei", "PingFang SC", sans-serif`
    ctx.textAlign = 'left'
    ctx.textBaseline = 'middle'
    ctx.fillText(name, tagX + (opts.layout === 'gallery' ? 22 : 0), tagY + (fontSize + 24) / 2)
  })
  return canvas
}

async function renderCollagePreview() {
  if (!collageModal.value || !collagePreviewCanvas.value) return
  collageBusy.value = true
  try {
    const rendered = await buildCollageCanvas(true)
    const preview = collagePreviewCanvas.value
    preview.width = rendered.width
    preview.height = rendered.height
    const ctx = preview.getContext('2d')
    ctx.clearRect(0, 0, preview.width, preview.height)
    ctx.drawImage(rendered, 0, 0)
  } catch (e) {
    message.error('拼接预览失败: ' + e.message)
  } finally {
    collageBusy.value = false
  }
}

async function downloadCollage() {
  if (!selectedCharacterRefs.value.length) {
    message.info('请先选择至少一个角色')
    return
  }
  collageBusy.value = true
  try {
    const canvas = await buildCollageCanvas(false)
    const blob = await canvasToBlob(canvas)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `角色拼接画板_${canvas.width}x${canvas.height}.png`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    message.success('角色拼接画板已导出')
  } catch (e) {
    message.error('导出拼接图失败: ' + e.message)
  } finally {
    collageBusy.value = false
  }
}

async function saveCollageToAssets() {
  if (!store.current) return
  const name = collageAssetName.value.trim()
  if (!name) {
    message.warning('请先填写拼接资产名称')
    return
  }
  if (!selectedCharacterRefs.value.length) {
    message.info('请先选择至少一个角色')
    return
  }
  collageBusy.value = true
  try {
    const canvas = await buildCollageCanvas(false)
    const blob = await canvasToBlob(canvas)
    const file = new File([blob], `${name.replace(/[\\/:*?"<>|]+/g, '_')}_${canvas.width}x${canvas.height}.png`, { type: 'image/png' })
    const asset = await api.addAsset(store.current.id, {
      type: 'character',
      name,
      desc: `角色拼接画板，包含 ${selectedCharacterRefs.value.map((a) => a.name).join('、')}`,
      appearance: '多角色拼接参考图，可直接 @ 引用',
      role: 'support',
    })
    await api.importAssetImage(store.current.id, asset.id, file, { purpose: 'collage' })
    markImageFresh(asset.id)
    message.success(`已保存为资产 @${name}`)
    await loadAssets()
    collageSelectedIds.value = [asset.id]
    collageModal.value = false
  } catch (e) {
    message.error('保存到资产库失败: ' + e.message)
  } finally {
    collageBusy.value = false
  }
}

watch(collageOptions, () => {
  if (collageModal.value) nextTick(renderCollagePreview)
}, { deep: true })

watch(collageSelectedIds, () => {
  if (collageModal.value) nextTick(renderCollagePreview)
}, { deep: true })

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
        <n-button
          size="small"
          secondary
          :disabled="!characterRefs.length"
          @click="openBatchLineTool"
        >
          <template #icon><n-icon :component="DownloadOutline" /></template>
          批量角色划线<span v-if="characterRefs.length">（{{ characterRefs.length }}）</span>
        </n-button>
        <n-button
          size="small"
          secondary
          :loading="collageBusy"
          :disabled="!characterRefs.length"
          @click="openCollageTool"
        >
          <template #icon><n-icon :component="ImageOutline" /></template>
          角色拼接画板<span v-if="characterRefs.length">（{{ characterRefs.length }}）</span>
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
                    <n-button
                      v-if="col.type === 'character'"
                      size="tiny"
                      secondary
                      :disabled="!a.ref_image"
                      title="角色图划线"
                      @click="openLineTool(a)"
                    >
                      <template #icon><n-icon :component="DownloadOutline" /></template>
                      划线
                    </n-button>
                    <n-button
                      v-if="col.type === 'character' && hasRestorableOriginal(a)"
                      size="tiny"
                      tertiary
                      :loading="importingId === a.id"
                      title="恢复成未划线原图"
                      @click="restoreOriginal(a)"
                    >
                      <template #icon><n-icon :component="RefreshOutline" /></template>
                      原图
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

    <n-modal
      v-model:show="lineModal"
      preset="card"
      :title="lineMode === 'batch' ? '批量角色划线设置' : '角色图划线工具'"
      class="line-modal"
      @after-enter="renderLinePreview"
    >
      <div class="line-tool">
        <div class="line-preview">
          <canvas ref="linePreviewCanvas"></canvas>
        </div>
        <div class="line-panel">
          <div class="line-target" v-if="lineTarget">
            <span>{{ lineMode === 'batch' ? `预览角色（共 ${characterRefs.length} 张）` : '当前角色' }}</span>
            <b>{{ lineTarget.trigger }}{{ lineTarget.name }}</b>
          </div>
          <label class="line-field">
            <span>线型</span>
            <n-select v-model:value="lineOptions.style" :options="lineStyleOptions" size="small" />
          </label>
          <label class="line-field">
            <span>输出</span>
            <n-select v-model:value="lineOptions.resolution" :options="lineResolutionOptions" size="small" />
          </label>
          <label class="line-field">
            <span>滤镜</span>
            <n-select v-model:value="lineOptions.filter" :options="lineFilterOptions" size="small" />
          </label>
          <label class="line-field compact">
            <span>颜色</span>
            <input v-model="lineOptions.color" type="color" />
          </label>
          <label class="line-field compact">
            <span>粗细 {{ lineOptions.width }}</span>
            <input v-model.number="lineOptions.width" type="range" min="1" max="24" />
          </label>
          <label class="line-field compact">
            <span>间距 {{ lineOptions.spacing }}</span>
            <input v-model.number="lineOptions.spacing" type="range" min="40" max="520" step="10" />
          </label>
          <label class="line-field compact">
            <span>透明度 {{ Math.round(lineOptions.opacity * 100) }}%</span>
            <input v-model.number="lineOptions.opacity" type="range" min="0.1" max="1" step="0.05" />
          </label>
          <div class="line-actions">
            <n-button
              v-if="lineMode === 'single' && lineTarget && hasRestorableOriginal(lineTarget)"
              tertiary
              :loading="!!importingId"
              @click="restoreOriginal(lineTarget)"
            >
              恢复当前原图
            </n-button>
            <n-button
              v-if="lineMode === 'batch' && restorableCharacterRefs.length"
              tertiary
              :loading="lineBatchBusy"
              @click="batchRestoreOriginals"
            >
              批量恢复原图（{{ restorableCharacterRefs.length }}）
            </n-button>
            <n-button
              :type="lineMode === 'batch' ? 'primary' : 'default'"
              secondary
              :loading="lineBatchBusy"
              :disabled="!characterRefs.length"
              @click="batchLineCharacters"
            >
              {{ lineMode === 'batch' ? `开始批量转换 ${characterRefs.length} 张` : '批量套用全部角色' }}
            </n-button>
            <n-button v-if="lineMode === 'single'" type="primary" :loading="lineBusy" @click="saveLineTarget">
              保存到当前角色
            </n-button>
          </div>
        </div>
      </div>
    </n-modal>

    <n-modal
      v-model:show="collageModal"
      preset="card"
      title="角色拼接画板"
      class="collage-modal"
      @after-enter="renderCollagePreview"
    >
      <div class="collage-tool">
        <div class="collage-preview">
          <n-spin :show="collageBusy">
            <canvas ref="collagePreviewCanvas"></canvas>
          </n-spin>
        </div>
        <div class="collage-panel">
          <div class="collage-summary">
            <b>{{ selectedCharacterRefs.length }} / {{ characterRefs.length }} 张角色图</b>
            <span>只会拼接已勾选的角色，并可保存成新的 @ 资产</span>
          </div>
          <label class="line-field">
            <span>保存名称</span>
            <n-input v-model:value="collageAssetName" size="small" placeholder="例如：主角团拼接参考" />
          </label>
          <div class="collage-picker">
            <div class="collage-picker-head">
              <span>选择角色</span>
              <div>
                <n-button size="tiny" quaternary @click="selectAllCollageCharacters">全选</n-button>
                <n-button size="tiny" quaternary @click="clearCollageCharacters">清空</n-button>
              </div>
            </div>
            <label v-for="a in characterRefs" :key="a.id" class="collage-check">
              <input v-model="collageSelectedIds" type="checkbox" :value="a.id" />
              <span>{{ a.trigger || '@' }}{{ a.name }}</span>
            </label>
          </div>
          <div class="collage-summary">
            <b>{{ characterRefs.length }} 张角色图</b>
            <span>{{ collageOptions.maxSize }} 长边，{{ collageOptions.aspect }}，PNG 导出</span>
          </div>
          <label class="line-field">
            <span>拼接方案</span>
            <n-select v-model:value="collageOptions.layout" :options="collageLayoutOptions" size="small" />
          </label>
          <label class="line-field">
            <span>画板清晰度</span>
            <n-select v-model:value="collageOptions.maxSize" :options="collageSizeOptions" size="small" />
          </label>
          <label class="line-field">
            <span>画板比例</span>
            <n-select v-model:value="collageOptions.aspect" :options="collageAspectOptions" size="small" />
          </label>
          <label class="line-field">
            <span>标题</span>
            <n-input v-model:value="collageOptions.title" size="small" />
          </label>
          <label class="line-field compact">
            <span>背景色</span>
            <input v-model="collageOptions.background" type="color" />
          </label>
          <label class="line-field compact">
            <span>标题文字色</span>
            <input v-model="collageOptions.nameColor" type="color" />
          </label>
          <label class="line-field compact">
            <span>角色名颜色</span>
            <input v-model="collageOptions.labelColor" type="color" />
          </label>
          <label class="line-field compact">
            <span>角色名字号 {{ collageOptions.labelSize }}</span>
            <input v-model.number="collageOptions.labelSize" type="range" min="14" max="72" step="2" />
          </label>
          <label class="line-field compact">
            <span>强调色</span>
            <input v-model="collageOptions.accentColor" type="color" />
          </label>
          <label class="collage-toggle">
            <input v-model="collageOptions.showTitle" type="checkbox" />
            <span>显示顶部标题</span>
          </label>
          <div class="collage-schemes">
            <b>方案说明</b>
            <span>A：卡片式，更适合发给 AI 识别单个角色。</span>
            <span>B：白底设定表，更接近角色资料页。</span>
            <span>C：紧凑索引板，角色多时更省空间。</span>
          </div>
          <div class="line-actions">
            <n-button secondary :loading="collageBusy" @click="renderCollagePreview">刷新预览</n-button>
            <n-button secondary :loading="collageBusy" :disabled="!selectedCharacterRefs.length" @click="saveCollageToAssets">保存到资产库</n-button>
            <n-button type="primary" :loading="collageBusy" @click="downloadCollage">下载拼接图</n-button>
          </div>
        </div>
      </div>
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
.line-modal { width: min(1120px, 94vw); }
.line-tool {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 16px;
}
.line-preview {
  min-height: 360px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  border: 1px solid var(--app-border);
  border-radius: 10px;
  background:
    linear-gradient(45deg, color-mix(in srgb, var(--app-bg-soft) 72%, transparent) 25%, transparent 25%),
    linear-gradient(-45deg, color-mix(in srgb, var(--app-bg-soft) 72%, transparent) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, color-mix(in srgb, var(--app-bg-soft) 72%, transparent) 75%),
    linear-gradient(-45deg, transparent 75%, color-mix(in srgb, var(--app-bg-soft) 72%, transparent) 75%);
  background-size: 18px 18px;
  background-position: 0 0, 0 9px, 9px -9px, -9px 0;
}
.line-preview canvas {
  max-width: 100%;
  height: auto;
  display: block;
}
.line-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.line-target {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-bg-soft);
}
.line-target span,
.line-field span {
  color: var(--app-text-muted);
  font-size: 12px;
}
.line-target b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.line-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.line-field.compact input[type="range"] { width: 100%; }
.line-field input[type="color"] {
  width: 42px;
  height: 30px;
  padding: 0;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  background: transparent;
}
.line-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 4px;
}
.collage-modal { width: min(1280px, 95vw); }
.collage-tool {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 16px;
}
.collage-preview {
  min-height: 420px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  border: 1px solid var(--app-border);
  border-radius: 10px;
  background: var(--app-bg-soft);
}
.collage-preview canvas {
  max-width: 100%;
  height: auto;
  display: block;
  border-radius: 8px;
  box-shadow: 0 18px 44px rgba(0,0,0,.22);
}
.collage-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.collage-summary,
.collage-schemes {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 10px 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-bg-soft);
}
.collage-summary span,
.collage-schemes span {
  color: var(--app-text-muted);
  font-size: 12px;
  line-height: 1.45;
}
.collage-picker {
  display: flex;
  flex-direction: column;
  gap: 7px;
  max-height: 180px;
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-bg-soft);
}
.collage-picker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--app-text-muted);
  font-size: 12px;
}
.collage-check {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--app-text-secondary);
  font-size: 13px;
}
.collage-check span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.collage-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--app-text-secondary);
  font-size: 13px;
}
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
  .line-tool { grid-template-columns: 1fr; }
  .line-preview { min-height: 260px; }
  .collage-tool { grid-template-columns: 1fr; }
  .collage-preview { min-height: 260px; }
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
