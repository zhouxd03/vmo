// Thin fetch wrapper around the Flask JSON API.

async function req(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const resp = await fetch(path, opts)
  const text = await resp.text()
  let data
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    data = { error: text }
  }
  if (!resp.ok) {
    throw new Error(data.error || `HTTP ${resp.status}`)
  }
  return data
}

// POST that expects a binary (zip) response; triggers a browser download.
// On error the server returns JSON, which we surface as an Error.
async function downloadBlob(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const resp = await fetch(path, opts)
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`
    try { msg = (await resp.json()).error || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  const blob = await resp.blob()
  const dispo = resp.headers.get('Content-Disposition') || ''
  // Prefer the RFC 5987 UTF-8 name (filename*=UTF-8''…); fall back to plain filename=.
  const mUtf8 = /filename\*=UTF-8''([^";]+)/i.exec(dispo)
  const mPlain = /filename="?([^";]+)"?/i.exec(dispo)
  const raw = (mUtf8 && mUtf8[1]) || (mPlain && mPlain[1]) || ''
  let name = 'draft.zip'
  if (raw) { try { name = decodeURIComponent(raw) } catch { name = raw } }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return { ok: true, name }
}

export const api = {
  get: (p) => req('GET', p),
  post: (p, b) => req('POST', p, b),
  del: (p) => req('DELETE', p),

  health: () => req('GET', '/api/health'),
  logs: (sinceId = 0) => req('GET', `/api/logs?since_id=${sinceId}`),

  getSettings: () => req('GET', '/api/settings'),
  saveSettings: (patch) => req('POST', '/api/settings', patch),

  listCredentials: () => req('GET', '/api/credentials'),
  upsertCredential: (category, entry) => req('POST', `/api/credentials/${category}`, entry),
  deleteCredential: (category, id) => req('DELETE', `/api/credentials/${category}/${id}`),
  setDefaultCredential: (category, id) => req('POST', `/api/credentials/${category}/${id}/default`),
  testCredential: (category, body) => req('POST', `/api/credentials/${category}/test`, body),
  llmModels: (body) => req('POST', '/api/llm/models', body),

  // projects (Phase 2)
  listProjects: () => req('GET', '/api/projects'),
  getProject: (pid) => req('GET', `/api/projects/${pid}`),
  deleteProject: (pid) => req('DELETE', `/api/projects/${pid}`),
  importProject: (body) => req('POST', '/api/projects/import', body),
  analyzeProject: (pid, body) => req('POST', `/api/projects/${pid}/analyze`, body),
  decomposeProject: (pid, body) => req('POST', `/api/projects/${pid}/decompose`, body),
  getJob: (jobId) => req('GET', `/api/jobs/${jobId}`),

  // episodes (集) — Phase 8
  listEpisodes: (pid) => req('GET', `/api/projects/${pid}/episodes`),
  getEpisode: (pid, eid) => req('GET', `/api/projects/${pid}/episodes/${eid}`),
  addEpisode: (pid, body) => req('POST', `/api/projects/${pid}/episodes`, body),
  renameEpisode: (pid, eid, name) => req('POST', `/api/projects/${pid}/episodes/${eid}`, { name }),
  deleteEpisode: (pid, eid) => req('DELETE', `/api/projects/${pid}/episodes/${eid}`),
  reorderEpisodes: (pid, order) => req('POST', `/api/projects/${pid}/episodes/reorder`, { order }),
  exportJianying: (pid, eid, body) => downloadBlob('POST', `/api/projects/${pid}/episodes/${eid}/export-jianying`, body),

  // assets (Phase 3)
  listAssets: (pid) => req('GET', `/api/projects/${pid}/assets`),
  addAsset: (pid, body) => req('POST', `/api/projects/${pid}/assets`, body),
  updateAsset: (pid, aid, body) => req('POST', `/api/projects/${pid}/assets/${aid}`, body),
  deleteAsset: (pid, aid) => req('DELETE', `/api/projects/${pid}/assets/${aid}`),
  seedAssets: (pid) => req('POST', `/api/projects/${pid}/assets/seed`),
  genAssetRefImage: (pid, aid, body) => req('POST', `/api/projects/${pid}/assets/${aid}/refimage`, body),
  assetImageUrl: (pid, filename) => `/api/projects/${pid}/asset-image/${filename}`,
  resolveMentions: (pid, text) => req('POST', `/api/projects/${pid}/resolve`, { text }),

  // batches (Phase 4)
  listBatches: (pid) => req('GET', `/api/projects/${pid}/batches`),
  createBatch: (pid, body) => req('POST', `/api/projects/${pid}/batches`, body),
  getBatch: (pid, bid) => req('GET', `/api/projects/${pid}/batches/${bid}`),
  deleteBatch: (pid, bid) => req('DELETE', `/api/projects/${pid}/batches/${bid}`),
  startBatch: (pid, bid) => req('POST', `/api/projects/${pid}/batches/${bid}/start`),
  pauseBatch: (pid, bid) => req('POST', `/api/projects/${pid}/batches/${bid}/pause`),
  retryBatch: (pid, bid) => req('POST', `/api/projects/${pid}/batches/${bid}/retry`),
  outputUrl: (pid, bid, filename) => `/api/output/${pid}/${bid}/${filename}`,

  // continuity engine (Phase 5)
  getContinuity: (pid) => req('GET', `/api/projects/${pid}/continuity`),
  resetContinuity: (pid) => req('POST', `/api/projects/${pid}/continuity/reset`),
  decideHandoff: (pid, body) => req('POST', `/api/projects/${pid}/continuity/decide`, body),
  extractTailFrame: (pid, body) => req('POST', `/api/projects/${pid}/continuity/tailframe`, body),
  genStaging: (pid, body) => req('POST', `/api/projects/${pid}/continuity/staging`, body),
  genDirectorBoard: (pid, body) => req('POST', `/api/projects/${pid}/continuity/director`, body),
  reviewContinuity: (pid, body) => req('POST', `/api/projects/${pid}/continuity/review`, body),
  continuityImageUrl: (pid, filename) => `/api/projects/${pid}/continuity-image/${filename}`,

  // prompt templates (multi-preset)
  listTemplates: () => req('GET', '/api/templates'),
  saveTemplate: (key, body, presetId) => req('POST', `/api/templates/${key}`, { body, preset_id: presetId }),
  resetTemplate: (key) => req('POST', `/api/templates/${key}/reset`),
  previewTemplate: (key, variables, presetId) => req('POST', `/api/templates/${key}/preview`, { variables, preset_id: presetId }),
  addPreset: (key, name, body, baseId) => req('POST', `/api/templates/${key}/presets`, { name, body, base_id: baseId }),
  renamePreset: (key, presetId, name) => req('PATCH', `/api/templates/${key}/presets/${presetId}`, { name }),
  deletePreset: (key, presetId) => req('DELETE', `/api/templates/${key}/presets/${presetId}`),
  setActivePreset: (key, presetId) => req('POST', `/api/templates/${key}/active`, { preset_id: presetId }),
}
