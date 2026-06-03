import { defineStore } from 'pinia'
import { api } from '../api'

export const useProjectStore = defineStore('project', {
  state: () => ({
    projects: [],
    currentId: null,
    current: null,
    // episodes (集) of the current novel
    episodes: [],
    currentEpisodeId: null,
    loading: false,
  }),
  getters: {
    hasCurrent: (s) => !!s.current,
    currentEpisode: (s) =>
      (s.current?.episodes || []).find((e) => e.id === s.currentEpisodeId) || null,
  },
  actions: {
    async refreshList() {
      this.projects = await api.listProjects()
    },
    _syncEpisodes(keepEid) {
      const eps = this.current?.episodes || []
      this.episodes = eps
      if (keepEid && eps.some((e) => e.id === keepEid)) {
        this.currentEpisodeId = keepEid
      } else if (!eps.some((e) => e.id === this.currentEpisodeId)) {
        this.currentEpisodeId = eps[0]?.id || null
      }
    },
    async select(pid) {
      this.currentId = pid
      this.current = await api.getProject(pid)
      this._syncEpisodes()
      return this.current
    },
    async reloadCurrent() {
      if (this.currentId) {
        const keep = this.currentEpisodeId
        this.current = await api.getProject(this.currentId)
        this._syncEpisodes(keep)
      }
    },
    selectEpisode(eid) {
      this.currentEpisodeId = eid
    },
    async addEpisode(payload) {
      const ep = await api.addEpisode(this.currentId, payload)
      await this.reloadCurrent()
      this.currentEpisodeId = ep.id
      return ep
    },
    async renameEpisode(eid, name) {
      await api.renameEpisode(this.currentId, eid, name)
      await this.reloadCurrent()
    },
    async removeEpisode(eid) {
      await api.deleteEpisode(this.currentId, eid)
      await this.reloadCurrent()
    },
    async importProject(payload) {
      const project = await api.importProject(payload)
      await this.refreshList()
      this.currentId = project.id
      this.current = project
      this._syncEpisodes()
      return project
    },
    async remove(pid) {
      await api.deleteProject(pid)
      if (this.currentId === pid) {
        this.currentId = null
        this.current = null
        this.episodes = []
        this.currentEpisodeId = null
      }
      await this.refreshList()
    },
  },
})
