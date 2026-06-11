(function () {
  const CHANNEL = 'page-service-channel';
  const READY_KEY = '__vmoDoubaoNetworkCaptureReady';
  const STORE_KEY = '__vmoDoubaoNetworkVideoCandidates';
  const FEATURE_KEY = '__vmoDoubaoNetworkFeatureTrace';
  if (window[READY_KEY]) return;
  window[READY_KEY] = true;

  const store = window[STORE_KEY] = Array.isArray(window[STORE_KEY]) ? window[STORE_KEY] : [];
  const featureTrace = window[FEATURE_KEY] = Array.isArray(window[FEATURE_KEY]) ? window[FEATURE_KEY] : [];
  const originalFetch = window.fetch;
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  let activeAnchor = window.__vmoDoubaoNetworkAnchor || null;
  const UNANCHORED_SLACK_MS = 2500;
  const FEATURE_TTL_MS = 10 * 60 * 1000;
  const TRUSTED_FEATURE_TTL_MS = 30 * 60 * 1000;

  function currentAnchor() {
    return activeAnchor && typeof activeAnchor === 'object' ? activeAnchor : {};
  }

  function normalizeUrl(value) {
    let raw = String(value || '').trim();
    if (!raw || raw.startsWith('blob:') || raw.startsWith('data:')) return '';
    const decodedBase64 = decodeMaybeBase64Url(raw);
    if (decodedBase64) raw = decodedBase64;
    raw = raw.replace(/\\u0026/g, '&').replace(/&amp;/g, '&').replace(/\\\//g, '/');
    if (/^https%3A%2F%2F/i.test(raw)) {
      try { raw = decodeURIComponent(raw); } catch (error) {}
    }
    try { return new URL(raw, location.href).toString(); } catch (error) { return ''; }
  }

  function decodeMaybeBase64Url(value) {
    const text = String(value || '').trim();
    if (!text || /https?:\/\//i.test(text) || text.length < 24 || text.length > 6000) return '';
    if (!/^[a-zA-Z0-9+/_=-]+$/.test(text)) return '';
    try {
      const normalized = text.replace(/-/g, '+').replace(/_/g, '/');
      const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
      const decoded = atob(padded).trim();
      if (/^https?:\/\//i.test(decoded)) return decoded;
    } catch (error) {}
    return '';
  }

  function isLikelyVideoResourceUrl(value) {
    const url = normalizeUrl(value);
    if (!url) return false;
    try {
      const parsed = new URL(url);
      if (/\/(?:api|samantha|alice|im|service|biz)\//i.test(parsed.pathname)
        && !/\.(?:mp4|mov|webm)(?:$|[?#])/i.test(parsed.pathname)
        && !/(?:[?&](?:download|media_url|play_url|main_url|backup_url)=https?%3A)/i.test(parsed.search)) {
        return false;
      }
    } catch (error) {}
    if (/\.(?:png|jpe?g|webp|gif|avif|svg|heic|heif)(?:~|$|[?#])/i.test(url)) return false;
    if (/image_generation|tplv-[^/?#]*image|\.image(?:$|[?#~])/i.test(url) && !/video|play|media/i.test(url)) return false;
    return /\.mp4(?:$|[?#])|(?:[?&](?:vid|video_id|videoId|item_id|itemId|media_id|play_id)=)|\/video(?:[_/-]|$)|video_generation|play_?url|main_?url|download_?url|backup_?url|media_?url|tos-[^/?#]+\/obj\/[^?#]*(?:video|media|mp4)|byteimg\.com\/[^?#]*(?:video|tos|obj|mp4)|bytevod|voddos|vod-|video\.?tos/i.test(url);
  }

  function isExplicitWatermarkUrl(value) {
    const text = String(value || '');
    return /video_gen_watermark(?:_[a-z0-9]+)*/i.test(text)
      || /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|logo_type=(?!video_gen_no_watermark)[^&#]*watermark)/i.test(text);
  }

  function isBlockedWatermarkVariantUrl(value) {
    return /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|(?:[?&]logo_type=(?!video_gen_no_watermark)[^&#]*watermark))/i.test(String(value || ''));
  }

  function isNoWatermarkUrl(value) {
    const text = String(value || '');
    return /(?:[?&]lr=video_gen_no_watermark|video_gen_no_watermark)/i.test(text)
      || isDolaCleanVideoUrl(text);
  }

  function isDolaCleanVideoUrl(value) {
    const url = normalizeUrl(value);
    if (!url || isExplicitWatermarkUrl(url)) return false;
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.replace(/^www\./i, '').toLowerCase();
      const hostOk = host === 'dola.com' || host.endsWith('.dola.com') || host === 'byteintlapi.com' || host.endsWith('.byteintlapi.com');
      if (!hostOk) return false;
      const lr = String(parsed.searchParams.get('lr') || '');
      const logoType = String(parsed.searchParams.get('logo_type') || '');
      const markerOk = /^cici_ai$/i.test(lr) || /^cici_ai$/i.test(logoType) || /(?:[?&](?:lr|logo_type)=cici_ai)(?:[&#]|$)/i.test(url);
      const videoOk = /\/video(?:\/|_|-)|mime_type=video|download=true|\.mp4(?:$|[?#])|\/fplay\/|tos-mya|tos-[^/?#]+/i.test(`${parsed.pathname}${parsed.search}`);
      return markerOk && videoOk;
    } catch (error) {
      return false;
    }
  }

  function isLikelyNoWatermarkSource(source) {
    const text = String(source || '').toLowerCase();
    if (!text) return false;
    if (/no[_-]?watermark|without[_-]?watermark|watermark[_-]?free|clean[_-]?video|source[_-]?video/.test(text)) return true;
    if (/original[_-]?media[_-]?info|originalmediainfo|origin[_-]?video|raw[_-]?video/.test(text)) return true;
    if (/encoded[_-]?video.*origin|origin.*encoded[_-]?video|video[_-]?model.*video[_-]?[1-9]|origin\.(main|play|download|url)/.test(text)) return true;
    if (/(samantha[-_/]?(?:media|video)[-_/]?get[-_]?play[-_]?info|get[-_]?play[-_]?info|mget_play_info|mget-play-info|generation_task_list|generation-task-list)/.test(text)) return true;
    if (/(original_video_info|originalvideoinfo|play_infos?|playinfo|play_info)/.test(text) && /(api\/biz|common|video|download|play|media)/.test(text)) return true;
    if (/(alice[-_/]?share[-_]?save|creativity[-_/]?share|generate_video_share_info|share_info)/.test(text) && /(samantha|alice|creativity|share|media|video|download|play)/.test(text)) return true;
    return false;
  }

  function trustedNoWatermarkUrlFromSource(value, source) {
    const url = toNoWatermarkUrl(value);
    if (!url || !isLikelyVideoResourceUrl(url) || isExplicitWatermarkUrl(url)) return '';
    if (isNoWatermarkUrl(url)) return url;
    return '';
  }

  function toNoWatermarkUrl(value) {
    const url = normalizeUrl(value);
    if (!url || isBlockedWatermarkVariantUrl(url)) return '';
    return url.replace(/video_gen_watermark(?:_[a-z0-9]+)*/ig, 'video_gen_no_watermark');
  }

  function hashString(value) {
    let hash = 2166136261;
    const text = String(value || '');
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return `net-${(hash >>> 0).toString(16)}`;
  }

  function extractVideoId(url) {
    const match = String(url || '').match(/(?:vid=|video_id=|videoId=|item_id=|itemId=|media_id=|mediaId=|play_id=|playId=|\/)(v0[a-zA-Z0-9_-]{6,}|[a-f0-9]{16,}|[0-9]{8,})/i);
    return match ? match[1] : hashString(url);
  }

  function normalizeIdentityKey(prefix, value) {
    const text = String(value || '').trim();
    if (!text) return '';
    if (/^(result|task|message|video|vid|url):/i.test(text)) return text;
    return `${prefix}:${text}`;
  }

  function featureKeys(candidate) {
    const keys = [
      normalizeIdentityKey('task', candidate && candidate.doubaoInternalTaskId),
      normalizeIdentityKey('video', candidate && candidate.doubaoInternalVideoId),
      normalizeIdentityKey('message', candidate && (candidate.doubaoInternalMessageId || candidate.messageId)),
      normalizeIdentityKey('vid', candidate && candidate.vid),
      normalizeIdentityKey('url', candidate && candidate.noWatermarkUrl),
    ].filter(Boolean);
    const metaKeys = candidate && candidate.doubaoResultMeta && Array.isArray(candidate.doubaoResultMeta.resultKeys)
      ? candidate.doubaoResultMeta.resultKeys
      : [];
    for (const key of metaKeys) {
      if (/^(task|video|message|vid|result):/i.test(String(key || '')) && !keys.includes(key)) keys.push(key);
    }
    return keys.slice(-40);
  }

  function isSensitiveIdKey(key) {
    return /token|cookie|secret|auth|csrf|passport|session|credential|password|web_id|device_id|user_id|uid|open_id/i.test(String(key || ''));
  }

  function classifyIdKey(key) {
    const name = String(key || '').replace(/[^a-zA-Z0-9]+/g, '_').toLowerCase();
    if (!name || isSensitiveIdKey(name)) return '';
    if (/(^|_)(task|job|generation|aigc|generate)(_|$)/.test(name) && /id|vid|item|task|job/.test(name)) return 'task';
    if (/(^|_)(message|msg|conversation|chat|dialog)(_|$)/.test(name) && /id|message|conversation|chat/.test(name)) return 'message';
    if (/(^|_)(vid|video|media|item)(_|$)/.test(name) && /id|vid|video|media|item/.test(name)) return 'video';
    if (/^(vid|video_id|videoid|item_id|itemid|media_id|mediaid|play_id|playid)$/.test(name)) return 'video';
    if (/^(message_id|messageid|msg_id|msgid|conversation_id|conversationid|chat_id|chatid|dialog_id|dialogid)$/.test(name)) return 'message';
    if (/^(task_id|taskid|job_id|jobid|generation_id|generationid|aigc_id|aigcid)$/.test(name)) return 'task';
    return '';
  }

  function normalizeInternalId(value, kind) {
    const text = String(value ?? '').trim();
    if (!text || text.length > 500) return '';
    const fromUrl = extractIdFromUrlText(text, kind);
    if (fromUrl) return fromUrl;
    const safe = text.match(/^(v0[a-zA-Z0-9_-]{6,}|[a-f0-9]{16,}|[0-9]{8,}|[a-zA-Z0-9][a-zA-Z0-9_-]{7,119})$/i);
    if (safe) return safe[1].slice(0, 120);
    const embedded = text.match(/(?:^|[^\w-])(v0[a-zA-Z0-9_-]{6,}|[a-f0-9]{16,}|[0-9]{8,})(?:$|[^\w-])/i);
    return embedded ? embedded[1].slice(0, 120) : '';
  }

  function extractIdFromUrlText(value, kind) {
    const text = String(value || '').trim();
    if (!text) return '';
    const names = kind === 'task'
      ? ['task_id', 'taskId', 'job_id', 'jobId', 'generation_id', 'generationId', 'aigc_id', 'aigcId', 'id']
      : kind === 'message'
        ? ['message_id', 'messageId', 'msg_id', 'msgId', 'conversation_id', 'conversationId', 'chat_id', 'chatId', 'dialog_id', 'dialogId', 'id']
        : ['vid', 'video_id', 'videoId', 'item_id', 'itemId', 'media_id', 'mediaId', 'play_id', 'playId', 'id'];
    try {
      if (/^https?:|^\/\//i.test(text)) {
        const url = new URL(text, location.href);
        for (const name of names) {
          const item = normalizeInternalId(url.searchParams.get(name) || '', kind);
          if (item) return item;
        }
      }
    } catch (error) {}
    for (const name of names) {
      const pattern = new RegExp(`(?:[?&#/]|^)${name}=([^&#/?]+)`, 'i');
      const match = text.match(pattern);
      if (match) {
        const item = normalizeInternalId(decodeURIComponent(match[1] || ''), kind);
        if (item) return item;
      }
    }
    return '';
  }

  function newCandidate(seed = {}) {
    const anchor = currentAnchor();
    return {
      vid: '',
      messageId: '',
      assetUrl: '',
      backupUrl: '',
      title: document.title || 'doubao-video',
      source: seed.source || 'network-capture',
      extractedAt: Date.now(),
      cardText: '',
      score: 950,
      noWatermarkUrl: '',
      doubaoWatermarkSource: '',
      doubaoWatermarkResolved: false,
      doubaoInternalTaskId: '',
      doubaoInternalMessageId: '',
      doubaoInternalVideoId: '',
      doubaoResultKey: '',
      doubaoResultMeta: { sourceKeys: [], resultKeys: [] },
      networkAnchorTaskId: String(anchor.taskId || ''),
      networkAnchorAccountId: String(anchor.accountId || ''),
      networkAnchorSubmittedAt: Number(anchor.submittedAt || 0),
      networkAnchorTrusted: false,
      networkPromptMatched: false,
      ...seed,
    };
  }

  function addMeta(candidate, key, source) {
    if (!key) return;
    const meta = candidate.doubaoResultMeta || (candidate.doubaoResultMeta = { sourceKeys: [], resultKeys: [] });
    meta.resultKeys = Array.isArray(meta.resultKeys) ? meta.resultKeys : [];
    meta.sourceKeys = Array.isArray(meta.sourceKeys) ? meta.sourceKeys : [];
    if (!meta.resultKeys.includes(key)) meta.resultKeys.push(key);
    const sourceKey = `${key}@${String(source || 'network').slice(0, 80)}`;
    if (!meta.sourceKeys.includes(sourceKey)) meta.sourceKeys.push(sourceKey);
    meta.resultKeys = meta.resultKeys.slice(-40);
    meta.sourceKeys = meta.sourceKeys.slice(-40);
  }

  function addId(candidate, kind, value, source) {
    const id = normalizeInternalId(value, kind);
    if (!id) return false;
    const field = kind === 'task' ? 'doubaoInternalTaskId' : kind === 'message' ? 'doubaoInternalMessageId' : 'doubaoInternalVideoId';
    if (!candidate[field]) candidate[field] = id;
    if (kind === 'message' && !candidate.messageId) candidate.messageId = id;
    if (kind === 'video' && !candidate.vid) candidate.vid = id;
    addMeta(candidate, normalizeIdentityKey(kind, id), source);
    return true;
  }

  function addUrl(candidate, value, source) {
    const url = normalizeUrl(value);
    if (!url || !isLikelyVideoResourceUrl(url)) return false;
    const noWatermarkUrl = trustedNoWatermarkUrlFromSource(url, source);
    if (noWatermarkUrl) {
      candidate.noWatermarkUrl = noWatermarkUrl;
      candidate.doubaoWatermarkSource = candidate.doubaoWatermarkSource
        || (isNoWatermarkUrl(noWatermarkUrl) ? 'network-no-watermark-url' : 'network-no-watermark-api');
      candidate.doubaoWatermarkResolved = true;
      if (!candidate.assetUrl || isExplicitWatermarkUrl(candidate.assetUrl)) candidate.assetUrl = noWatermarkUrl;
      else if (!candidate.backupUrl && candidate.assetUrl !== noWatermarkUrl) candidate.backupUrl = candidate.assetUrl;
      if (!candidate.vid) candidate.vid = extractVideoId(noWatermarkUrl);
      addMeta(candidate, normalizeIdentityKey('url', noWatermarkUrl), source);
      hydrateCandidateFromRecentFeatures(candidate, source);
      return true;
    }
    if (!candidate.assetUrl) candidate.assetUrl = url;
    else if (!candidate.backupUrl && candidate.assetUrl !== url) candidate.backupUrl = url;
    if (!candidate.vid) candidate.vid = extractVideoId(url);
    addMeta(candidate, normalizeIdentityKey('url', url), source);
    hydrateCandidateFromRecentFeatures(candidate, source);
    return true;
  }

  function mergeCandidateIds(target, source, sourceName = '') {
    if (!target || !source) return;
    if (!target.networkAnchorTaskId && source.networkAnchorTaskId) target.networkAnchorTaskId = String(source.networkAnchorTaskId || '');
    if (!target.networkAnchorAccountId && source.networkAnchorAccountId) target.networkAnchorAccountId = String(source.networkAnchorAccountId || '');
    if (!target.networkAnchorSubmittedAt && source.networkAnchorSubmittedAt) target.networkAnchorSubmittedAt = Number(source.networkAnchorSubmittedAt || 0);
    if (source.networkAnchorTrusted) target.networkAnchorTrusted = true;
    if (source.networkPromptMatched) target.networkPromptMatched = true;
    if (!target.noWatermarkUrl && source.noWatermarkUrl) {
      target.noWatermarkUrl = source.noWatermarkUrl;
      target.doubaoWatermarkSource = target.doubaoWatermarkSource || source.doubaoWatermarkSource || 'network-no-watermark-api';
      target.doubaoWatermarkResolved = true;
      if (!target.assetUrl || isExplicitWatermarkUrl(target.assetUrl)) target.assetUrl = target.noWatermarkUrl;
    }
    for (const kind of ['task', 'message', 'video']) {
      const field = kind === 'task' ? 'doubaoInternalTaskId' : kind === 'message' ? 'doubaoInternalMessageId' : 'doubaoInternalVideoId';
      if (!target[field] && source[field]) addId(target, kind, source[field], sourceName || source.source || 'network-feature');
    }
    if (!target.messageId && source.messageId) target.messageId = source.messageId;
    if (!target.vid && source.vid) target.vid = source.vid;
    for (const key of featureKeys(source)) addMeta(target, key, sourceName || source.source || 'network-feature');
  }

  function rememberFeature(candidate) {
    const keys = featureKeys(candidate);
    if (!keys.length) return;
    const anchor = currentAnchor();
    const item = {
      doubaoInternalTaskId: candidate.doubaoInternalTaskId || '',
      doubaoInternalMessageId: candidate.doubaoInternalMessageId || '',
      doubaoInternalVideoId: candidate.doubaoInternalVideoId || '',
      messageId: candidate.messageId || '',
      vid: candidate.vid || '',
      source: candidate.source || 'network-feature',
      sourceUrl: candidate.sourceUrl || '',
      extractedAt: Date.now(),
      noWatermarkUrl: candidate.noWatermarkUrl || '',
      doubaoWatermarkSource: candidate.doubaoWatermarkSource || '',
      doubaoWatermarkResolved: Boolean(candidate.doubaoWatermarkResolved || candidate.noWatermarkUrl),
      networkAnchorTaskId: String(candidate.networkAnchorTaskId || anchor.taskId || ''),
      networkAnchorAccountId: String(candidate.networkAnchorAccountId || anchor.accountId || ''),
      networkAnchorSubmittedAt: Number(candidate.networkAnchorSubmittedAt || anchor.submittedAt || 0),
      networkAnchorTrusted: Boolean(candidate.networkAnchorTrusted),
      networkPromptMatched: Boolean(candidate.networkPromptMatched),
      doubaoResultMeta: {
        ...(candidate.doubaoResultMeta || {}),
        watermarkSource: candidate.doubaoWatermarkSource || candidate.doubaoResultMeta && candidate.doubaoResultMeta.watermarkSource || '',
        resultKeys: keys,
      },
    };
    const signature = `${item.networkAnchorTaskId}:${keys.join('|')}`;
    const existingIndex = featureTrace.findIndex((entry) => `${String(entry.networkAnchorTaskId || '')}:${featureKeys(entry).join('|')}` === signature);
    if (existingIndex >= 0) featureTrace.splice(existingIndex, 1);
    featureTrace.push(item);
    while (featureTrace.length > 120) featureTrace.shift();
  }

  function hydrateCandidateFromRecentFeatures(candidate, source = '') {
    const now = Date.now();
    const anchorTaskId = String(candidate.networkAnchorTaskId || currentAnchor().taskId || '');
    const submittedAt = Number(candidate.networkAnchorSubmittedAt || currentAnchor().submittedAt || 0);
    const candidateKeys = featureKeys(candidate);
    const recent = featureTrace
      .filter((item) => now - Number(item.extractedAt || 0) < (item.networkAnchorTrusted ? TRUSTED_FEATURE_TTL_MS : FEATURE_TTL_MS))
      .filter((item) => !anchorTaskId || String(item.networkAnchorTaskId || '') === anchorTaskId)
      .filter((item) => {
        if (!submittedAt) return true;
        const itemSubmittedAt = Number(item.networkAnchorSubmittedAt || 0);
        const itemExtractedAt = Number(item.extractedAt || 0);
        return itemSubmittedAt >= submittedAt - UNANCHORED_SLACK_MS || itemExtractedAt >= submittedAt - UNANCHORED_SLACK_MS;
      })
      .filter((item) => {
        if (item.networkAnchorTrusted || item.networkPromptMatched) return true;
        const itemKeys = featureKeys(item);
        return candidateKeys.some((key) => itemKeys.includes(key));
      })
      .slice(-16);
    for (const item of recent) mergeCandidateIds(candidate, item, `recent:${String(source || item.source || 'network').slice(0, 80)}`);
  }

  function finalizeCandidate(candidate) {
    if (!candidate || !(candidate.assetUrl || candidate.backupUrl)) return null;
    if (candidate.noWatermarkUrl && !isExplicitWatermarkUrl(candidate.noWatermarkUrl)) {
      candidate.doubaoWatermarkSource = candidate.doubaoWatermarkSource || 'network-no-watermark-api';
      candidate.doubaoWatermarkResolved = true;
      if (!candidate.assetUrl || isExplicitWatermarkUrl(candidate.assetUrl)) candidate.assetUrl = candidate.noWatermarkUrl;
    }
    candidate.doubaoResultMeta = candidate.doubaoResultMeta || { sourceKeys: [], resultKeys: [] };
    if (candidate.doubaoWatermarkSource) candidate.doubaoResultMeta.watermarkSource = candidate.doubaoWatermarkSource;
    const keys = [
      normalizeIdentityKey('task', candidate.doubaoInternalTaskId),
      normalizeIdentityKey('video', candidate.doubaoInternalVideoId),
      normalizeIdentityKey('message', candidate.doubaoInternalMessageId || candidate.messageId),
      normalizeIdentityKey('vid', candidate.vid),
      normalizeIdentityKey('url', candidate.noWatermarkUrl),
      normalizeIdentityKey('url', candidate.assetUrl || candidate.backupUrl),
    ].filter(Boolean);
    candidate.doubaoResultKey = candidate.doubaoResultKey || keys[0] || '';
    keys.forEach((key) => addMeta(candidate, key, candidate.source));
    return candidate;
  }

  function remember(candidate) {
    rememberFeature(candidate);
    const item = finalizeCandidate(candidate);
    if (!item) return;
    const key = item.assetUrl || item.backupUrl || item.doubaoResultKey || `${item.vid}:${item.messageId}`;
    const existingIndex = store.findIndex((entry) => {
      const entryKeys = [entry.assetUrl, entry.backupUrl, entry.doubaoResultKey].filter(Boolean);
      return entryKeys.includes(key) || entryKeys.includes(item.assetUrl) || entryKeys.includes(item.backupUrl);
    });
    if (existingIndex >= 0) store.splice(existingIndex, 1);
    store.push(item);
    while (store.length > 120) store.shift();
    window.postMessage({ source: CHANNEL, type: 'network-candidates-updated', videos: [item] }, '*');
  }

  function collectFromValue(value, candidate, source, depth = 0, seen = null, budget = null) {
    if (value === null || value === undefined || depth > 6) return;
    const nextBudget = budget || { count: 0 };
    if (++nextBudget.count > 4500) return;
    if (typeof value === 'string') {
      collectIdsFromText(value, candidate, source);
      collectUrlsFromText(value, candidate, source);
      collectFromNestedText(value, candidate, source, depth, seen, nextBudget);
      return;
    }
    if (typeof value === 'number') return;
    if (typeof value !== 'object') return;
    const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
    if (nextSeen) {
      if (nextSeen.has(value)) return;
      nextSeen.add(value);
    }
    if (Array.isArray(value)) {
      const list = value.length > 180 ? value.slice(-180) : value;
      list.forEach((item, index) => collectFromValue(item, candidate, `${source}[${index}]`, depth + 1, nextSeen, nextBudget));
      return;
    }
    for (const [key, item] of Object.entries(value).slice(-900)) {
      if (isSensitiveIdKey(key)) continue;
      const kind = classifyIdKey(key);
      if (kind && (typeof item === 'string' || typeof item === 'number')) addId(candidate, kind, item, `${source}.${key}`);
      if (typeof item === 'string') {
        collectIdsFromText(item, candidate, `${source}.${key}`);
        if (/url|uri|src|play|main|backup|download|media|video|item/i.test(key)) addUrl(candidate, item, `${source}.${key}`);
        collectUrlsFromText(item, candidate, `${source}.${key}`);
        collectFromNestedText(item, candidate, `${source}.${key}`, depth, nextSeen, nextBudget);
      } else if (item && typeof item === 'object' && depth < 5) {
        collectFromValue(item, candidate, `${source}.${key}`, depth + 1, nextSeen, nextBudget);
      }
    }
  }

  function collectFromNestedText(text, candidate, source, depth = 0, seen = null, budget = null) {
    if (depth > 5) return;
    const raw = String(text || '').trim();
    if (!raw || raw.length > 1_000_000) return;
    if (!/[\[{]|https?:|mp4|video|media|play|main|backup|download|origin|watermark/i.test(raw)) return;
    if ((raw.startsWith('{') || raw.startsWith('[')) && /video|media|play|main|backup|download|origin|watermark|url/i.test(raw)) {
      try {
        collectFromValue(JSON.parse(raw), candidate, `${source}:json`, depth + 1, seen, budget);
        return;
      } catch (error) {}
    }
    const decoded = decodeMaybeBase64Url(raw);
    if (decoded) addUrl(candidate, decoded, `${source}:base64`);
  }

  function collectUrlsFromText(text, candidate, source) {
    const raw = String(text || '').replace(/\\u0026/g, '&').replace(/&amp;/g, '&').replace(/\\\//g, '/');
    if (!raw || !/(mp4|video|media|play|tos|bytevod|voddos|download|backup)/i.test(raw)) return;
    const urlPattern = /https?:\/\/[^\s"'<>\\]+/gi;
    for (const match of raw.match(urlPattern) || []) addUrl(candidate, match, source);
  }

  function collectIdsFromText(text, candidate, source) {
    const raw = String(text || '');
    if (!raw || !/(id|vid|video|message|conversation|task|job|generation|item|media)/i.test(raw)) return;
    const patterns = [
      /["']?([a-zA-Z0-9_]*(?:task|job|generation|aigc)[a-zA-Z0-9_]*(?:id|vid|item)?)["']?\s*[:=]\s*["']?([a-zA-Z0-9_-]{8,120})/gi,
      /["']?([a-zA-Z0-9_]*(?:message|msg|conversation|chat|dialog)[a-zA-Z0-9_]*(?:id)?)["']?\s*[:=]\s*["']?([a-zA-Z0-9_-]{8,120})/gi,
      /["']?((?:vid|video_id|videoId|item_id|itemId|media_id|mediaId|video)[a-zA-Z0-9_]*)["']?\s*[:=]\s*["']?([a-zA-Z0-9_-]{8,120})/gi,
    ];
    for (const pattern of patterns) {
      for (const match of raw.matchAll(pattern)) {
        const kind = classifyIdKey(match[1]);
        if (kind) addId(candidate, kind, match[2], `${source}.${match[1]}`);
      }
    }
  }

  function normalizeProbeText(value) {
    return String(value || '')
      .replace(/\\u0026/g, '&')
      .replace(/&amp;/g, '&')
      .replace(/\\\//g, '/')
      .replace(/\\"/g, '"')
      .replace(/\\n/g, '\n')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase();
  }

  function decodeLooseText(value) {
    const raw = String(value || '');
    try {
      if (/%[0-9a-f]{2}/i.test(raw)) return decodeURIComponent(raw);
    } catch (error) {}
    return raw;
  }

  function matchesPromptProbe(value) {
    const anchor = currentAnchor();
    const probes = Array.isArray(anchor.promptProbe) ? anchor.promptProbe : [];
    if (!probes.length) return false;
    const haystack = normalizeProbeText(`${value || ''}\n${decodeLooseText(value || '')}`);
    if (!haystack) return false;
    return probes.some((probe) => {
      const needle = normalizeProbeText(probe);
      return needle.length >= 12 && haystack.includes(needle);
    });
  }

  function requestUrlFromInput(input) {
    try {
      if (typeof input === 'string') return normalizeUrl(input);
      if (input && input.url) return normalizeUrl(input.url);
    } catch (error) {}
    return '';
  }

  function bodyToInspectableText(body) {
    if (body === null || body === undefined) return '';
    if (typeof body === 'string') return body;
    if (body instanceof URLSearchParams) return body.toString();
    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      const parts = [];
      try {
        for (const [key, value] of body.entries()) {
          if (typeof value === 'string') parts.push(`${key}=${value}`);
        }
      } catch (error) {}
      return parts.join('&');
    }
    if (typeof body === 'object' && !(body instanceof Blob) && !(body instanceof ArrayBuffer)) {
      try { return JSON.stringify(body); } catch (error) {}
    }
    return '';
  }

  function inspectRequest(input, initOrBody, source) {
    const sourceUrl = requestUrlFromInput(input);
    const body = initOrBody && Object.prototype.hasOwnProperty.call(initOrBody, 'body') ? initOrBody.body : initOrBody;
    const bodyText = bodyToInspectableText(body);
    const candidate = newCandidate({ source, sourceUrl });
    const combined = `${sourceUrl}\n${bodyText}`;
    if (matchesPromptProbe(combined)) {
      candidate.networkPromptMatched = true;
      candidate.networkAnchorTrusted = true;
    }
    collectIdsFromText(sourceUrl, candidate, `${source}.url`);
    collectIdsFromText(bodyText, candidate, `${source}.body`);
    collectUrlsFromText(bodyText, candidate, `${source}.body`);
    if (bodyText && /^[\s{\[]/.test(bodyText)) {
      try { collectFromValue(JSON.parse(bodyText), candidate, `${source}.body`); } catch (error) {}
    }
    rememberFeature(candidate);
    return candidate;
  }

  function inspectPayload(payload, source, seed = {}) {
    const candidate = newCandidate({ ...seed, source, sourceUrl: seed.sourceUrl || '' });
    mergeCandidateIds(candidate, seed, `${source}.request`);
    collectIdsFromText(seed.sourceUrl || '', candidate, `${source}.url`);
    if (typeof payload === 'string') {
      collectIdsFromText(payload, candidate, source);
      collectUrlsFromText(payload, candidate, source);
      try {
        const parsed = JSON.parse(payload);
        collectFromValue(parsed, candidate, source);
      } catch (error) {}
    } else {
      collectFromValue(payload, candidate, source);
    }
    remember(candidate);
  }

  function shouldInspectResponse(url, contentType) {
    if (isLikelyVideoResourceUrl(url)) return true;
    if (!/json|text|javascript|x-www-form-urlencoded/i.test(String(contentType || ''))) return false;
    return /doubao|dola|aigc|video|media|generation|generate|conversation|message|chat|creation|seedance|item|bot|api/i.test(String(url || ''));
  }

  async function inspectFetchResponse(input, response, requestSeed = {}) {
    try {
      const url = normalizeUrl(response && response.url || (typeof input === 'string' ? input : input && input.url) || '');
      if (isLikelyVideoResourceUrl(url)) {
        const candidate = newCandidate({ ...requestSeed, assetUrl: url, backupUrl: url, vid: extractVideoId(url), source: 'network-fetch-url', sourceUrl: url });
        mergeCandidateIds(candidate, requestSeed, 'network-fetch-url.request');
        remember(candidate);
      }
      const contentType = response && response.headers && response.headers.get ? response.headers.get('content-type') : '';
      if (!response || !shouldInspectResponse(url, contentType)) return;
      const text = await response.clone().text();
      if (text && text.length <= 2_500_000) inspectPayload(text, `network-fetch:${url.slice(0, 120)}`, { ...requestSeed, sourceUrl: url });
    } catch (error) {}
  }

  window.fetch = async function (...args) {
    let requestSeed = {};
    try { requestSeed = inspectRequest(args[0], args[1] || {}, 'network-fetch-request'); } catch (error) {}
    const response = await originalFetch.apply(this, args);
    inspectFetchResponse(args[0], response, requestSeed);
    return response;
  };

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    try { this.__vmoDoubaoUrl = normalizeUrl(url); } catch (error) {}
    return originalOpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function (...args) {
    try {
      this.__vmoDoubaoRequestSeed = inspectRequest(this.__vmoDoubaoUrl || '', args[0], 'network-xhr-request');
      this.addEventListener('loadend', () => {
        try {
          const url = this.__vmoDoubaoUrl || '';
          const requestSeed = this.__vmoDoubaoRequestSeed || {};
          if (isLikelyVideoResourceUrl(url)) {
            const candidate = newCandidate({ ...requestSeed, assetUrl: url, backupUrl: url, vid: extractVideoId(url), source: 'network-xhr-url', sourceUrl: url });
            mergeCandidateIds(candidate, requestSeed, 'network-xhr-url.request');
            remember(candidate);
          }
          const contentType = this.getResponseHeader && this.getResponseHeader('content-type') || '';
          if (!shouldInspectResponse(url, contentType)) return;
          if (typeof this.responseText === 'string' && this.responseText.length <= 2_500_000) {
            inspectPayload(this.responseText, `network-xhr:${url.slice(0, 120)}`, { ...requestSeed, sourceUrl: url });
          }
        } catch (error) {}
      });
    } catch (error) {}
    return originalSend.apply(this, args);
  };

  function candidateBelongsToRequest(candidate, request = {}) {
    if (!candidate) return false;
    const taskId = String(request.taskId || '');
    const accountId = String(request.accountId || '');
    const submittedAt = Number(request.submittedAt || 0);
    const candidateTaskId = String(candidate.networkAnchorTaskId || '');
    const candidateAccountId = String(candidate.networkAnchorAccountId || '');
    const candidateSubmittedAt = Number(candidate.networkAnchorSubmittedAt || 0);
    const extractedAt = Number(candidate.extractedAt || 0);
    if (taskId && candidateTaskId && candidateTaskId !== taskId) return false;
    if (accountId && candidateAccountId && candidateAccountId !== accountId) return false;
    if (submittedAt) {
      const lowerBound = submittedAt - UNANCHORED_SLACK_MS;
      if (candidateSubmittedAt && candidateSubmittedAt < lowerBound) return false;
      if (extractedAt && extractedAt < lowerBound) return false;
      if (taskId && !candidateTaskId && !candidate.networkAnchorTrusted && !candidate.networkPromptMatched) return false;
    }
    return true;
  }

  window.addEventListener('message', (event) => {
    const data = event && event.data;
    if (!data || data.source !== CHANNEL || data.type !== 'network-candidates-request') return;
    const videos = store.slice(-120).filter((item) => candidateBelongsToRequest(item, data));
    window.postMessage({
      source: CHANNEL,
      type: 'network-candidates-result',
      requestId: data.requestId || '',
      videos,
    }, '*');
  });

  window.addEventListener('message', (event) => {
    const data = event && event.data;
    if (!data || data.source !== CHANNEL || data.type !== 'network-capture-reset') return;
    const taskId = String(data.taskId || '');
    if (taskId) {
      for (let index = store.length - 1; index >= 0; index -= 1) {
        if (String(store[index] && store[index].networkAnchorTaskId || '') === taskId) store.splice(index, 1);
      }
      for (let index = featureTrace.length - 1; index >= 0; index -= 1) {
        if (String(featureTrace[index] && featureTrace[index].networkAnchorTaskId || '') === taskId) featureTrace.splice(index, 1);
      }
    }
    activeAnchor = {
      taskId,
      accountId: String(data.accountId || ''),
      submittedAt: Number(data.submittedAt || Date.now()),
      promptFingerprint: String(data.promptFingerprint || ''),
      promptProbe: Array.isArray(data.promptProbe) ? data.promptProbe.map((item) => String(item || '').slice(0, 240)).filter(Boolean).slice(0, 6) : [],
      resetAt: Date.now(),
    };
    window.__vmoDoubaoNetworkAnchor = activeAnchor;
  });
})();
