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

// Batch generation is slow (esp. video) so we don't hammer the server: the
// first heavy status query fires after an initial delay, then on an interval.
const BATCH_POLL_INITIAL_DELAY = 100_000
const BATCH_POLL_INTERVAL = 20_000
const DECOMPOSE_POLL_INTERVAL = 1_000

export const useTasksStore = defineStore('tasks', {
  state: () => ({
    // ── batch generation (WorktableView) ──
    batchProjectId: null,
    batches: [],
    batchActive: false,
    genStart: 0,        // ms timestamp when the current run began (0 = idle)
    nowTick: Date.now(), // advanced by a 1s clock so elapsed time stays live

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
      try {
        this.batches = await api.listBatches(this.batchProjectId)
      } catch {
        return // keep last-known batches on a transient fetch error
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
    async ensureBatchPolling({ immediate = false } = {}) {
      if (immediate) await this.refreshBatches()
      this._startBatchTimers()
    },

    _startBatchTimers() {
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

    async _reloadIfCurrent(pid) {
      // Refresh project data only if the user is still on the same project, so a
      // job that finishes after the user navigated away doesn't clobber state.
      const projectStore = useProjectStore()
      if (projectStore.currentId === pid) await projectStore.reloadCurrent()
    },
  },
})
