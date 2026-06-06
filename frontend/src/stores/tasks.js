import { defineStore } from 'pinia'
import { api } from '../api'
import { useProjectStore } from './project'

// Cross-tab task persistence (§8.2).
//
// Background jobs (batch generation, script decompose, global analyze) run as
// independent threads on the Flask backend and are NOT affected by switching
// the frontend tab/page. The original bug was purely client-side: each view
// owned its own polling timers in onMounted/onUnmounted, so leaving the page
// tore the polling down and the progress "looked" lost. This store hoists the
// polling + progress state to the (always-mounted) app level, so polling keeps
// running across tab switches and any view that mounts simply reads live state.

// Module-level timers live independent of any component lifecycle.
let batchTimer = null
let batchClock = null
let decomposeTimer = null
let assetMissingTimer = null
const assetTimers = new Map()
let batchPollStart = 0
let batchRefreshing = false

// Batch generation is slow (esp. video), but the UI must clear promptly when
// a batch finishes. Poll quickly at first, then back off to avoid hammering.
const BATCH_POLL_INITIAL_DELAY = 4_000
const BATCH_POLL_INTERVAL = 20_000
const BATCH_POLL_MAX_MINUTES_DEFAULT = 30
const DECOMPOSE_POLL_INTERVAL = 1_000
const ASSET_POLL_INTERVAL = 1_500

export const useTasksStore = defineStore('tasks', {
  state: () => ({
    // ── batch generation (WorktableView) ──
    batchProjectId: null,
    batches: [],
    batchActive: false,
    genStart: 0,        // ms timestamp when the current run began (0 = idle)
    nowTick: Date.now(), // advanced by a 1s clock so elapsed time stays live
    batchPollMaxMinutes: BATCH_POLL_MAX_MINUTES_DEFAULT,
    batchPollLastSetAt: 0,

    // ── script decompose (ScriptView) ──
    decomposeProjectId: null,
    decomposeEpisodeId: null,
    decomposeJobId: null,
    decomposeStatus: 'idle', // idle | running | done | error
    decomposeDone: 0,
    decomposeTotal: 0,
    decomposeError: '',

    // ── global analyze (ScriptView) ──
    analyzeProjectId: null,
    analyzeEpisodeId: null,
    analyzeStatus: 'idle', // idle | running | done | error
    analyzeError: '',

    // asset reference image generation (AssetsView)
    assetProjectId: null,
    assetBatchGen: {
      running: false, done: 0, total: 0, failed: [], message: '', startedAt: null, jobId: '',
    },
    assetJobs: {},
  }),
  getters: {
    decomposePercent: (s) =>
      (s.decomposeTotal ? Math.round((s.decomposeDone / s.decomposeTotal) * 100) : 0),
    // Is a decompose running for this exact project+episode?
    isDecomposing: (s) => (pid, eid) =>
      s.decomposeStatus === 'running' && s.decomposeProjectId === pid && s.decomposeEpisodeId === eid,
    isAnalyzing: (s) => (pid, eid) =>
      s.analyzeStatus === 'running' && s.analyzeProjectId === pid && s.analyzeEpisodeId === eid,

    // Per-episode generation status aggregated from the (episode_id-tagged)
    // batch summaries. Returns null when an episode has no batches. status:
    // running | queued | error | done. Used by the episode tabs + worktable so
    // multiple episodes generating concurrently each show their own progress.
    episodeStat: (s) => (eid) => {
      if (!eid) return null
      const bs = s.batches.filter((b) => b.episode_id === eid)
      if (!bs.length) return null
      let total = 0, done = 0, error = 0
      let running = false, queued = false
      for (const b of bs) {
        total += b.total || 0
        done += b.done || 0
        error += b.error || 0
        if (b.status === 'running') running = true
        else if (b.status === 'pending') queued = true
      }
      const status = running ? 'running' : queued ? 'queued' : error ? 'error' : 'done'
      return { total, done, error, status, percent: total ? Math.round((done / total) * 100) : 0 }
    },
  },
  actions: {
    // ───────────────────────── batch generation ─────────────────────────
    setBatchProject(pid) {
      if (this.batchProjectId === pid) return
      this._stopBatchTimers()
      this.batchProjectId = pid
      this.batches = []
      this.batchActive = false
      this.genStart = 0
    },

    async refreshBatches() {
      if (!this.batchProjectId) return
      if (batchRefreshing) return
      batchRefreshing = true
      try {
        this.batches = await api.listBatches(this.batchProjectId)
      } catch {
        return // keep last-known batches on a transient fetch error
      } finally {
        batchRefreshing = false
      }
      this.batchActive = this.batches.some((b) => ['running', 'pending'].includes(b.status))
      if (this.batchActive) {
        if (!this.genStart) this.genStart = Date.now()
      } else {
        this.genStart = 0
        this._stopBatchTimers()
      }
    },

    // Start (or keep) the persistent batch polling loop. `immediate` forces a
    // one-shot refresh right away (used after the user starts a batch).
    async ensureBatchPolling({ immediate = false, maxMinutes } = {}) {
      if (Number.isFinite(Number(maxMinutes)) && Number(maxMinutes) > 0) {
        this.batchPollMaxMinutes = Math.max(1, Number(maxMinutes))
        this.batchPollLastSetAt = Date.now()
      }
      if (immediate) await this.refreshBatches()
      this._startBatchTimers()
    },

    _startBatchTimers() {
      if (!batchPollStart) batchPollStart = Date.now()
      if (!batchClock) {
        batchClock = setInterval(() => {
          if (this.batchActive) this.nowTick = Date.now()
        }, 1000)
      }
      if (!batchTimer) {
        batchTimer = setTimeout(() => this._batchTick(), BATCH_POLL_INITIAL_DELAY)
      }
    },

    async _batchTick() {
      this.nowTick = Date.now()
      const elapsedMs = batchPollStart ? (Date.now() - batchPollStart) : 0
      const maxMs = Math.max(1, this.batchPollMaxMinutes || BATCH_POLL_MAX_MINUTES_DEFAULT) * 60 * 1000
      if (elapsedMs > maxMs) {
        this._stopBatchTimers()
        return
      }
      await this.refreshBatches()
      if (this.batchActive) {
        batchTimer = setTimeout(() => this._batchTick(), BATCH_POLL_INTERVAL)
      } else {
        this._stopBatchTimers()
      }
    },

    _stopBatchTimers() {
      if (batchTimer) { clearTimeout(batchTimer); batchTimer = null }
      if (batchClock) { clearInterval(batchClock); batchClock = null }
    },

    // ───────────────────────── script decompose ─────────────────────────
    async startDecompose(pid, eid, opts = {}) {
      this.decomposeProjectId = pid
      this.decomposeEpisodeId = eid
      this.decomposeStatus = 'running'
      this.decomposeDone = 0
      this.decomposeTotal = 0
      this.decomposeError = ''
      this.decomposeJobId = null
      try {
        const { job_id } = await api.decomposeProject(pid, { episode_id: eid, ...opts })
        this.decomposeJobId = job_id
        this._startDecomposeTimer()
      } catch (e) {
        this._stopDecomposeTimer()
        this.decomposeStatus = 'error'
        this.decomposeError = e.message || String(e)
      }
    },

    _startDecomposeTimer() {
      if (decomposeTimer) return
      decomposeTimer = setInterval(() => this._decomposeTick(), DECOMPOSE_POLL_INTERVAL)
    },

    _stopDecomposeTimer() {
      if (decomposeTimer) { clearInterval(decomposeTimer); decomposeTimer = null }
    },

    async _decomposeTick() {
      if (!this.decomposeJobId) return
      let job
      try {
        job = await api.getJob(this.decomposeJobId)
      } catch {
        return // transient; try again next tick
      }
      this.decomposeDone = job.progress || 0
      this.decomposeTotal = job.total || 0
      if (job.status === 'done') {
        this._stopDecomposeTimer()
        this.decomposeStatus = 'done'
        await this._reloadIfCurrent(this.decomposeProjectId)
      } else if (job.status === 'error') {
        this._stopDecomposeTimer()
        this.decomposeStatus = 'error'
        this.decomposeError = job.error || '未知错误'
      }
    },

    // ───────────────────────── global analyze ─────────────────────────
    async runAnalyze(pid, eid) {
      this.analyzeProjectId = pid
      this.analyzeEpisodeId = eid
      this.analyzeStatus = 'running'
      this.analyzeError = ''
      try {
        await api.analyzeProject(pid, { episode_id: eid })
        await this._reloadIfCurrent(pid)
        this.analyzeStatus = 'done'
      } catch (e) {
        this.analyzeStatus = 'error'
        this.analyzeError = e.message || String(e)
      }
    },

    async startMissingAssetRefs(pid, payload, totalHint = 0) {
      this.assetProjectId = pid
      this.assetBatchGen = {
        running: true,
        done: 0,
        total: totalHint || 0,
        failed: [],
        message: `提交中 · ${payload?.model || '默认模型'} · ${payload?.size || '默认尺寸'}`,
        startedAt: Date.now(),
        jobId: '',
      }
      try {
        const r = await api.startMissingAssets(pid, payload || {})
        this.assetBatchGen = {
          ...this.assetBatchGen,
          jobId: r.job_id,
          total: r.total || totalHint || 0,
          message: '已提交，等待生成服务响应',
        }
        this._startAssetMissingTimer()
        await this._pollMissingAssetRefs()
      } catch (e) {
        this._stopAssetMissingTimer()
        this.assetBatchGen = { ...this.assetBatchGen, running: false, message: e.message || String(e) }
      }
    },

    _startAssetMissingTimer() {
      if (assetMissingTimer) return
      assetMissingTimer = setInterval(() => this._pollMissingAssetRefs(), ASSET_POLL_INTERVAL)
    },

    _stopAssetMissingTimer() {
      if (assetMissingTimer) { clearInterval(assetMissingTimer); assetMissingTimer = null }
    },

    async _pollMissingAssetRefs() {
      const jobId = this.assetBatchGen.jobId
      if (!jobId) return
      let job
      try {
        job = await api.getJob(jobId)
      } catch (e) {
        this.assetBatchGen = { ...this.assetBatchGen, message: e.message || String(e) }
        return
      }
      this.assetBatchGen = {
        ...this.assetBatchGen,
        done: job.progress || 0,
        total: job.total || this.assetBatchGen.total,
        message: job.message || this.assetBatchGen.message,
      }
      if (job.status === 'done') {
        this._stopAssetMissingTimer()
        const r = job.result || {}
        this.assetBatchGen = {
          ...this.assetBatchGen,
          running: false,
          done: r.generated || job.progress || 0,
          total: r.total || job.total || 0,
          failed: r.failed || [],
          message: '参考图补全完成',
        }
        await this._reloadIfCurrent(this.assetProjectId)
      } else if (job.status === 'error') {
        this._stopAssetMissingTimer()
        this.assetBatchGen = {
          ...this.assetBatchGen,
          running: false,
          message: job.error || '生成失败',
        }
      }
    },

    assetJob(assetId) {
      return this.assetJobs[assetId] || null
    },

    _setAssetJob(assetId, patch) {
      this.assetJobs = {
        ...this.assetJobs,
        [assetId]: { ...(this.assetJobs[assetId] || {}), ...patch },
      }
    },

    async startAssetRef(pid, asset, payload) {
      if (!asset?.id) return
      const assetId = asset.id
      const label = `${asset.trigger || ''}${asset.name || ''}`
      this.assetProjectId = pid
      this._setAssetJob(assetId, {
        running: true,
        status: 'starting',
        message: `提交中 · ${payload?.model || '默认模型'} · ${payload?.size || '默认尺寸'}`,
        model: payload?.model || '默认模型',
        size: payload?.size,
        startedAt: Date.now(),
        progress: 0,
        total: 1,
        error: '',
        assetName: label,
      })
      try {
        const r = await api.startAssetRefImage(pid, assetId, payload || {})
        this._setAssetJob(assetId, { jobId: r.job_id, message: '已提交，等待生成服务响应' })
        this._startAssetTimer(r.job_id, assetId)
        await this._pollAssetRef(r.job_id, assetId)
      } catch (e) {
        this._setAssetJob(assetId, {
          running: false,
          status: 'error',
          message: e.message || String(e),
          error: e.message || String(e),
          finishedAt: Date.now(),
        })
      }
    },

    _startAssetTimer(jobId, assetId) {
      this._stopAssetTimerByJob(jobId)
      const timer = setInterval(() => this._pollAssetRef(jobId, assetId), ASSET_POLL_INTERVAL)
      assetTimers.set(jobId, timer)
    },

    _stopAssetTimerByJob(jobId) {
      const timer = assetTimers.get(jobId)
      if (timer) clearInterval(timer)
      assetTimers.delete(jobId)
    },

    async _pollAssetRef(jobId, assetId) {
      if (!jobId || !assetId) return
      let job
      try {
        job = await api.getJob(jobId)
      } catch (e) {
        this._setAssetJob(assetId, { message: e.message || String(e) })
        return
      }
      this._setAssetJob(assetId, {
        jobId,
        status: job.status,
        message: job.message || (job.status === 'running' ? '生成中' : ''),
        progress: job.progress || 0,
        total: job.total || 1,
      })
      if (job.status === 'done') {
        this._stopAssetTimerByJob(jobId)
        this._setAssetJob(assetId, {
          running: false,
          status: 'done',
          message: '参考图已更新',
          finishedAt: Date.now(),
        })
        await this._reloadIfCurrent(this.assetProjectId)
      } else if (job.status === 'error') {
        this._stopAssetTimerByJob(jobId)
        this._setAssetJob(assetId, {
          running: false,
          status: 'error',
          message: job.error || '生成失败',
          error: job.error || '生成失败',
          finishedAt: Date.now(),
        })
      }
    },

    async _reloadIfCurrent(pid) {
      // Refresh project data only if the user is still on the same project, so a
      // job that finishes after the user navigated away doesn't clobber state.
      const projectStore = useProjectStore()
      if (projectStore.currentId === pid) await projectStore.reloadCurrent()
    },
  },
})
