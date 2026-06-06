import { defineStore } from 'pinia'
import { api } from '../api'

let healthTimer = null

const HEALTH_POLL_MS = 15_000
const OFFLINE_POLL_MS = 5_000

export const useHealthStore = defineStore('health', {
  state: () => ({
    status: 'checking', // checking | online | offline
    info: null,
    error: '',
    lastOkAt: 0,
    lastCheckedAt: 0,
    checking: false,
    failCount: 0,
  }),
  getters: {
    online: (s) => s.status === 'online',
    showBanner: (s) => s.status === 'offline' || (s.status === 'checking' && !s.lastOkAt),
    label: (s) => {
      if (s.status === 'online') return `后端已连接 · ${s.info?.version || 'unknown'}`
      if (s.status === 'checking') return '正在检查后端连接'
      return '后端连接异常'
    },
    detail: (s) => {
      if (s.status === 'online') {
        const up = Math.floor(Number(s.info?.uptime_sec || 0))
        return up ? `已运行 ${Math.floor(up / 60)}分${String(up % 60).padStart(2, '0')}秒` : ''
      }
      return s.error || '无法连接本地后端服务'
    },
  },
  actions: {
    start() {
      if (healthTimer) return
      this.check()
      healthTimer = window.setInterval(() => {
        this.check({ silent: true })
      }, this.status === 'offline' ? OFFLINE_POLL_MS : HEALTH_POLL_MS)
    },

    stop() {
      if (healthTimer) window.clearInterval(healthTimer)
      healthTimer = null
    },

    _reschedule() {
      if (!healthTimer) return
      window.clearInterval(healthTimer)
      healthTimer = window.setInterval(() => {
        this.check({ silent: true })
      }, this.status === 'offline' ? OFFLINE_POLL_MS : HEALTH_POLL_MS)
    },

    async check() {
      if (this.checking) return
      this.checking = true
      if (!this.lastOkAt) this.status = 'checking'
      try {
        const info = await api.health()
        this.info = info
        this.error = ''
        this.status = info?.ok ? 'online' : 'offline'
        this.lastOkAt = info?.ok ? Date.now() : this.lastOkAt
        this.failCount = info?.ok ? 0 : this.failCount + 1
      } catch (e) {
        this.status = 'offline'
        this.error = e.message || String(e)
        this.failCount += 1
      } finally {
        this.lastCheckedAt = Date.now()
        this.checking = false
        this._reschedule()
      }
    },
  },
})
