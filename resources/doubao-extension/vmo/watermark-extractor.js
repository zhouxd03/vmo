(function () {
  'use strict';

  const CHANNEL = 'doubao-video-download-extractor';
  const READY_KEY = '__vmoDoubaoWatermarkExtractorReady';
  const API_ORIGIN = /^https:\/\/(?:www\.)?(?:doubao|dola)\.com$/i.test(location.origin)
    ? location.origin
    : 'https://www.doubao.com';
  const SITE = siteProfile();

  if (window[READY_KEY]) return;
  window[READY_KEY] = true;

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

  function siteProfile() {
    const host = String(location.hostname || '').replace(/^www\./i, '').toLowerCase();
    const isDola = host === 'dola.com';
    const language = normalizeLanguage(isDola ? (observedQueryParam('language') || 'zh') : 'zh');
    const region = normalizeRegion(isDola ? (observedQueryParam('region') || observedQueryParam('sys_region') || 'TW') : 'CN');
    return {
      site: isDola ? 'dola' : 'doubao',
      isDola,
      origin: API_ORIGIN,
      aid: isDola ? '495671' : '497858',
      realAid: isDola ? '495671' : '497858',
      language,
      region,
      sysRegion: region,
      zone: observedQueryParam('zone') || '',
      deviceId: observedQueryParam('device_id') || '',
      pcVersion: observedQueryParam('pc_version') || (isDola ? '3.22.3' : '3.14.6'),
      versionCode: observedQueryParam('version_code') || '20800',
      webPlatform: observedQueryParam('web_platform') || (isDola ? 'browser' : ''),
    };
  }

  function observedQueryParam(name) {
    const direct = searchParam(name);
    if (direct) return direct;
    try {
      const entries = performance && performance.getEntriesByType ? performance.getEntriesByType('resource') : [];
      for (const entry of entries.slice(-600).reverse()) {
        const raw = String(entry && entry.name || '');
        if (!/(?:doubao|dola)\.com|byteintlapi\.com|byteoversea/i.test(raw)) continue;
        try {
          const value = new URL(raw, location.href).searchParams.get(name);
          if (value) return value;
        } catch (error) {}
      }
    } catch (error) {}
    return '';
  }

  function normalizeLanguage(fallback) {
    const candidates = [
      searchParam('language'),
      searchParam('lang'),
      document.documentElement && document.documentElement.lang,
      navigator.language,
      Array.isArray(navigator.languages) ? navigator.languages[0] : '',
      fallback,
    ];
    for (const item of candidates) {
      const text = String(item || '').trim().toLowerCase();
      if (!text) continue;
      if (text.startsWith('zh')) return 'zh';
      if (text.startsWith('en')) return 'en';
      return text.split(/[-_]/)[0].slice(0, 8) || fallback;
    }
    return fallback;
  }

  function normalizeRegion(fallback) {
    const candidates = [
      searchParam('region'),
      searchParam('sys_region'),
      searchParam('app_region'),
      searchParam('country'),
      fallback,
    ];
    for (const item of candidates) {
      const text = String(item || '').trim().toUpperCase();
      if (/^[A-Z]{2}$/.test(text)) return text;
    }
    return fallback;
  }

  function searchParam(name) {
    try { return new URLSearchParams(location.search || '').get(name) || ''; } catch (error) { return ''; }
  }

  function commonQuery() {
    const query = {
      aid: SITE.aid,
      real_aid: SITE.realAid,
      device_platform: 'web',
      language: SITE.language,
      region: SITE.region,
      sys_region: SITE.sysRegion,
      pc_version: SITE.pcVersion,
      version_code: SITE.versionCode,
      pkg_type: 'release_version',
      samantha_web: '1',
      'use-olympus-account': '1',
    };
    if (SITE.zone) query.zone = SITE.zone;
    if (SITE.deviceId) query.device_id = SITE.deviceId;
    if (SITE.webPlatform) query.web_platform = SITE.webPlatform;
    return query;
  }

  function diagnosticProfile() {
    return {
      site: SITE.site,
      origin: SITE.origin,
      aid: SITE.aid,
      realAid: SITE.realAid,
      language: SITE.language,
      region: SITE.region,
      zone: SITE.zone,
      pcVersion: SITE.pcVersion,
      versionCode: SITE.versionCode,
      webPlatform: SITE.webPlatform,
    };
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

  function toNoWatermarkUrl(value) {
    const url = normalizeUrl(value);
    if (!url || isBlockedWatermarkVariantUrl(url)) return '';
    return url.replace(/video_gen_watermark(?:_[a-z0-9]+)*/ig, 'video_gen_no_watermark');
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

  function normalizeNoWatermarkUrl(value) {
    const url = toNoWatermarkUrl(value);
    if (!url || !isNoWatermarkUrl(url) || isExplicitWatermarkUrl(url) || !isLikelyVideoResourceUrl(url)) return '';
    return url;
  }

  function trustedNoWatermarkUrl(value, source) {
    const url = normalizeNoWatermarkUrl(value);
    if (!url) return '';
    if (isNoWatermarkUrl(url)) return url;
    return '';
  }

  function isLikelyHtmlDocumentUrl(value) {
    const url = normalizeUrl(value);
    if (!url) return false;
    if (/\.(?:html?|shtml)(?:$|[?#])/i.test(url)) return true;
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.replace(/^www\./i, '').toLowerCase();
      const path = parsed.pathname.replace(/\/+$/g, '') || '/';
      const hasEncodedMedia = /(?:[?&](?:download|media_url|play_url|main_url|backup_url)=https?%3A)/i.test(parsed.search);
      if ((host === 'doubao.com' || host === 'dola.com') && !hasEncodedMedia) {
        if (path === '/' || /^\/(?:chat|download|login|auth|home|app)(?:\/|$)/i.test(path)) return true;
      }
    } catch (error) {}
    return false;
  }

  function isLikelyVideoResourceUrl(value) {
    const url = normalizeUrl(value);
    if (!url || isLikelyHtmlDocumentUrl(url)) return false;
    try {
      const parsed = new URL(url);
      if (/\/(?:api|samantha|alice|im|service|biz)\//i.test(parsed.pathname)
        && !/\.(?:mp4|mov|webm)(?:$|[?#])/i.test(parsed.pathname)
        && !/(?:[?&](?:download|media_url|play_url|main_url|backup_url)=https?%3A)/i.test(parsed.search)) {
        return false;
      }
    } catch (error) {
      return false;
    }
    if (/\.(?:png|jpe?g|webp|gif|avif|svg|heic|heif)(?:~|$|[?#])/i.test(url)) return false;
    if (/image_generation|tplv-[^/?#]*image|\.image(?:$|[?#~])/i.test(url) && !/video|play|media/i.test(url)) return false;
    return /\.(?:mp4|mov|webm|m4v)(?:$|[?#])|(?:[?&](?:vid|video_id|videoId|item_id|itemId|media_id|play_id)=)|\/video(?:[_/-]|$)|video_generation|play_?url|main_?url|download_?url|backup_?url|media_?url|tos-[^/?#]+\/obj\/[^?#]*(?:video|media|mp4)|byteimg\.com\/[^?#]*(?:video|tos|obj|mp4)|bytevod|voddos|vod-|video\.?tos/i.test(url);
  }

  function apiUrl(path, extra = {}) {
    const url = new URL(path, API_ORIGIN);
    if (SITE.isDola || !/^\/api\/biz\//i.test(path)) {
      for (const [key, value] of Object.entries(commonQuery())) {
        if (!url.searchParams.has(key)) url.searchParams.set(key, value);
      }
    }
    for (const [key, value] of Object.entries(extra || {})) {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value));
    }
    return url.toString();
  }

  function headers(extra = {}, path = '') {
    const base = {
      'Content-Type': 'application/json',
      Accept: 'application/json, text/plain, */*',
    };
    if (!/^\/(?:api\/biz|samantha\/(?:media|video)\/get_play_info|alice\/media\/bigmusic\/share_save|creativity\/share\/get_video_share_info)/i.test(path)) {
      base['x-use-ppe'] = '1';
    }
    return {
      ...base,
      ...extra,
    };
  }

  function trimResponseText(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().slice(0, 180);
  }

  async function postJson(path, body, extraHeaders = {}, extraQuery = {}) {
    const url = apiUrl(path, extraQuery);
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: headers(extraHeaders, path),
      body: JSON.stringify(body || {}),
    });
    const text = await response.text().catch(() => '');
    if (!response.ok) {
      const detail = trimResponseText(text);
      throw new Error(`${path}: HTTP ${response.status}${detail ? `; ${detail}` : ''}`);
    }
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch (error) {
      throw new Error(`${path}: invalid JSON${text ? `; ${trimResponseText(text)}` : ''}`);
    }
    const hasData = Boolean(json && Object.prototype.hasOwnProperty.call(json, 'data'));
    const code = json && (json.code ?? json.err_no ?? json.status_code ?? json.ret);
    const okCode = code === undefined || code === null || code === '' || Number(code) === 0;
    if (!json || !okCode || !hasData) {
      const message = json && (json.message || json.msg || json.status_msg || code);
      throw new Error(`${path}: API returned unexpected result: ${message || 'empty data'}`);
    }
    return json.data;
  }

  function safeTitle() {
    return String(document.title || 'doubao-video').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 80);
  }

  function normalizeId(value) {
    const text = String(value || '').trim();
    if (!text || text === '0') return '';
    const match = text.match(/(?:^|[^\w-])(v0[a-zA-Z0-9_-]{6,}|[a-f0-9]{16,}|[0-9]{8,}|[a-zA-Z0-9][a-zA-Z0-9_-]{7,119})(?:$|[^\w-])/i);
    return match ? match[1].slice(0, 120) : text.slice(0, 120);
  }

  function findVid(value, depth = 0, seen = null) {
    if (!value || depth > 6) return '';
    if (typeof value === 'string' || typeof value === 'number') {
      const id = normalizeId(value);
      return /^v0/i.test(id) ? id : '';
    }
    if (typeof value !== 'object') return '';
    const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
    if (nextSeen) {
      if (nextSeen.has(value)) return '';
      nextSeen.add(value);
    }
    const direct = normalizeId(value.vid || value.video_id || value.videoId || value.item_id || value.itemId || value.media_id || value.mediaId || '');
    if (/^v0/i.test(direct)) return direct;
    const entries = Array.isArray(value) ? value : Object.values(value);
    for (const item of entries.slice ? entries.slice(0, 300) : entries) {
      const nested = findVid(item, depth + 1, nextSeen);
      if (nested) return nested;
    }
    return '';
  }

  function collectRouterRefs(value, depth = 0, seen = null, refs = []) {
    if (!value || typeof value !== 'object' || depth > 8) return refs;
    const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
    if (nextSeen) {
      if (nextSeen.has(value)) return refs;
      nextSeen.add(value);
    }
    const messageId = normalizeId(value.message_id || value.messageId || value.msg_id || value.msgId || '');
    if (messageId) {
      const vid = findVid(value);
      if (vid) refs.push({ vid, messageId, source: 'router-data' });
    }
    const entries = Array.isArray(value) ? value : Object.values(value);
    for (const item of entries.slice ? entries.slice(0, 500) : entries) {
      collectRouterRefs(item, depth + 1, nextSeen, refs);
    }
    return refs;
  }

  function pushGlobalSource(sources, key) {
    try {
      if (window[key]) sources.push({ name: key, value: window[key] });
    } catch (error) {}
  }

  function pushParsedStorageSources(sources, storage, label) {
    try {
      if (!storage) return;
      for (let index = 0; index < Math.min(storage.length, 200); index += 1) {
        const key = storage.key(index);
        const value = storage.getItem(key) || '';
        if (!/(message|video|samantha|doubao|dola|chat|media|conversation|history|state)/i.test(`${key || ''} ${value.slice(0, 220)}`)) continue;
        if (!/^\s*[\[{]/.test(value) || value.length > 3000000) continue;
        try {
          sources.push({ name: `${label}:${key}`, value: JSON.parse(value) });
        } catch (error) {}
      }
    } catch (error) {}
  }

  function hasLikelyDoubaoVideoSignal(value, depth = 0, seen = null) {
    if (!value || depth > 4) return false;
    if (typeof value === 'string') return /message_?id|video_?id|media_?id|main_?url|play_?url|backup_?url|v0[a-zA-Z0-9_-]{6,}/i.test(value);
    if (typeof value !== 'object') return false;
    const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
    if (nextSeen) {
      if (nextSeen.has(value)) return false;
      nextSeen.add(value);
    }
    for (const key of Object.keys(value).slice(0, 80)) {
      if (/message_?id|video_?id|media_?id|main_?url|play_?url|backup_?url|vid|media_info|play_info/i.test(key)) return true;
      try {
        if (hasLikelyDoubaoVideoSignal(value[key], depth + 1, nextSeen)) return true;
      } catch (error) {}
    }
    return false;
  }

  function pushReactSource(sources, name, value) {
    if (!value || sources.length >= 360) return;
    if (hasLikelyDoubaoVideoSignal(value)) sources.push({ name, value });
  }

  function collectReactSources() {
    const sources = [];
    let nodes = [];
    try {
      nodes = Array.from(document.querySelectorAll('body, body *')).slice(-2500);
    } catch (error) {
      return sources;
    }
    for (const element of nodes) {
      let keys = [];
      try {
        keys = Object.keys(element).filter((key) => key.startsWith('__reactProps$') || key.startsWith('__reactFiber$')).slice(0, 8);
      } catch (error) {
        continue;
      }
      for (const key of keys) {
        try {
          const value = element[key];
          if (key.startsWith('__reactProps$') && value) pushReactSource(sources, `react:${key}`, value);
          if (key.startsWith('__reactFiber$') && value) {
            pushReactSource(sources, `react:${key}:memoizedProps`, value.memoizedProps);
            pushReactSource(sources, `react:${key}:pendingProps`, value.pendingProps);
            pushReactSource(sources, `react:${key}:memoizedState`, value.memoizedState);
            if (value.return) {
              pushReactSource(sources, `react:${key}:returnProps`, value.return.memoizedProps);
              pushReactSource(sources, `react:${key}:returnState`, value.return.memoizedState);
            }
          }
        } catch (error) {}
      }
    }
    return sources.filter((item) => item && item.value);
  }

  function collectPageDataSources() {
    const sources = [];
    [
      '__MODERN_ROUTER_DATA',
      '_ROUTER_DATA',
      '__INITIAL_STATE__',
      '__SSR_DATA__',
      '__NEXT_DATA__',
      '__NUXT__',
    ].forEach((key) => pushGlobalSource(sources, key));
    pushParsedStorageSources(sources, window.localStorage, 'localStorage');
    pushParsedStorageSources(sources, window.sessionStorage, 'sessionStorage');
    sources.push(...collectReactSources());
    return sources;
  }

  function collectSourceRefs(sources) {
    const refs = [];
    for (const source of sources) {
      const before = refs.length;
      collectRouterRefs(source && source.value, 0, null, refs);
      for (let index = before; index < refs.length; index += 1) {
        refs[index].source = refs[index].source || source.name || 'page-state';
      }
    }
    return refs;
  }

  function collectSourceMediaCandidates(sources, target) {
    const videos = [];
    for (const source of sources) {
      collectRouterMediaCandidates(source && source.value, target, source && source.name || 'page-state', 0, null, videos);
      if (videos.length > 80) break;
    }
    return videos;
  }

  function targetRefs(target) {
    if (!target || typeof target !== 'object') return [];
    const vid = normalizeId(target.vid || target.doubaoInternalVideoId || '');
    const messageId = normalizeId(target.messageId || target.doubaoInternalMessageId || '');
    const networkAnchorTaskId = normalizeId(target.networkAnchorTaskId || '');
    const hasAnchor = vid || messageId || networkAnchorTaskId;
    return hasAnchor ? [{
      vid,
      messageId,
      source: 'request-target',
      doubaoInternalTaskId: normalizeId(target.doubaoInternalTaskId || ''),
      networkAnchorTaskId,
      networkAnchorAccountId: String(target.networkAnchorAccountId || ''),
      networkAnchorSubmittedAt: Number(target.networkAnchorSubmittedAt || 0),
      doubaoResultKey: String(target.doubaoResultKey || ''),
    }] : [];
  }

  function uniqueRefs(refs) {
    const map = new Map();
    for (const ref of refs.filter(Boolean)) {
      const key = `${ref.vid || ''}:${ref.messageId || ''}:${ref.networkAnchorTaskId || ''}:${ref.doubaoInternalTaskId || ''}`;
      if (!key.replace(/:/g, '')) continue;
      if (!map.has(key)) map.set(key, ref);
      else map.set(key, { ...map.get(key), ...ref });
    }
    return [...map.values()];
  }

  function refScore(ref, target) {
    if (!target || typeof target !== 'object') return 0;
    let score = 0;
    const targetVid = normalizeId(target.vid || target.doubaoInternalVideoId || '');
    const targetMessage = normalizeId(target.messageId || target.doubaoInternalMessageId || '');
    const targetNetworkTask = normalizeId(target.networkAnchorTaskId || '');
    const targetInternalTask = normalizeId(target.doubaoInternalTaskId || '');
    if (targetVid && ref.vid === targetVid) score += 100;
    if (targetMessage && ref.messageId === targetMessage) score += 100;
    if (targetNetworkTask && ref.networkAnchorTaskId === targetNetworkTask) score += 120;
    if (targetInternalTask && ref.doubaoInternalTaskId === targetInternalTask) score += 80;
    const text = [
      target.doubaoResultKey,
      target.assetUrl,
      target.backupUrl,
      target.noWatermarkUrl,
      ...(target.doubaoResultMeta && Array.isArray(target.doubaoResultMeta.resultKeys) ? target.doubaoResultMeta.resultKeys : []),
    ].filter(Boolean).join(' ');
    if (ref.vid && text.includes(ref.vid)) score += 30;
    if (ref.messageId && text.includes(ref.messageId)) score += 30;
    return score;
  }

  function chooseRefs(allRefs, target) {
    const refs = uniqueRefs(allRefs);
    const hasTarget = target && (target.vid || target.doubaoInternalVideoId || target.messageId || target.doubaoInternalMessageId || target.doubaoResultKey || target.networkAnchorTaskId);
    if (!hasTarget) return refs.slice(-8);
    const scored = refs
      .map((ref) => ({ ref, score: refScore(ref, target) }))
      .filter((entry) => entry.score > 0 || entry.ref.source === 'request-target')
      .sort((a, b) => b.score - a.score)
      .map((entry) => entry.ref);
    return scored.length ? scored.slice(0, 4) : refs.slice(-4);
  }

  function candidateMatchesTarget(candidate, target) {
    if (!target || typeof target !== 'object') return true;
    const hasTarget = target.vid || target.doubaoInternalVideoId || target.messageId || target.doubaoInternalMessageId || target.doubaoResultKey || target.networkAnchorTaskId;
    if (!hasTarget) return true;
    const ref = {
      vid: candidate.vid || candidate.doubaoInternalVideoId || '',
      messageId: candidate.messageId || candidate.doubaoInternalMessageId || '',
      doubaoInternalTaskId: candidate.doubaoInternalTaskId || '',
      networkAnchorTaskId: candidate.networkAnchorTaskId || '',
      doubaoResultKey: candidate.doubaoResultKey || '',
    };
    return refScore(ref, target) > 0
      || shareTextIncludesTarget([
        candidate.noWatermarkUrl,
        candidate.assetUrl,
        candidate.backupUrl,
        candidate.doubaoResultKey,
        ...(candidate.doubaoResultMeta && Array.isArray(candidate.doubaoResultMeta.resultKeys) ? candidate.doubaoResultMeta.resultKeys : []),
      ], target);
  }

  function shareTextIncludesTarget(values, target) {
    const text = values.filter(Boolean).join(' ');
    const needles = [
      target.vid,
      target.doubaoInternalVideoId,
      target.messageId,
      target.doubaoInternalMessageId,
      target.doubaoInternalTaskId,
      target.networkAnchorTaskId,
      target.doubaoResultKey,
    ].map((item) => String(item || '').trim()).filter((item) => item.length >= 6);
    return needles.some((needle) => text.includes(needle));
  }

  function refFromObject(value, fallback = {}) {
    const directVid = normalizeId(value && (value.vid || value.video_id || value.videoId || value.item_id || value.itemId || value.media_id || value.mediaId) || '');
    const ref = {
      vid: (/^v0/i.test(directVid) ? directVid : '') || normalizeId(fallback.vid || fallback.doubaoInternalVideoId || ''),
      messageId: normalizeId(value && (value.message_id || value.messageId || value.msg_id || value.msgId) || fallback.messageId || fallback.doubaoInternalMessageId || ''),
      source: fallback.source || 'page-state',
      doubaoInternalTaskId: normalizeId(value && (value.task_id || value.taskId || value.job_id || value.jobId) || fallback.doubaoInternalTaskId || ''),
      networkAnchorTaskId: normalizeId(value && value.networkAnchorTaskId || fallback.networkAnchorTaskId || ''),
      networkAnchorAccountId: String(value && value.networkAnchorAccountId || fallback.networkAnchorAccountId || ''),
      networkAnchorSubmittedAt: Number(value && value.networkAnchorSubmittedAt || fallback.networkAnchorSubmittedAt || 0),
      doubaoResultKey: String(fallback.doubaoResultKey || ''),
    };
    return ref;
  }

  function buildTrustedUrlCandidate(ref, url, backupUrl, source, seed = {}) {
    const cleanUrl = trustedNoWatermarkUrl(url, source);
    if (!cleanUrl) return null;
    const sourceName = isNoWatermarkUrl(cleanUrl) ? 'no-watermark-api:explicit-url' : `no-watermark-api:${String(source || 'page-state').slice(0, 80)}`;
    return buildCandidate(ref, {
      main_url: cleanUrl,
      backup_url: normalizeUrl(backupUrl || seed.backupUrl || seed.backup_url || ''),
      width: seed.width || 0,
      height: seed.height || 0,
      definition: seed.definition || '',
      meta: seed.meta || {},
    }, sourceName, [{ name: 'page-state', error: '', source }]);
  }

  function networkStateCandidates(target) {
    const list = Array.isArray(window.__vmoDoubaoNetworkVideoCandidates) ? window.__vmoDoubaoNetworkVideoCandidates.slice(-120) : [];
    const videos = [];
    for (const item of list) {
      if (!item || typeof item !== 'object') continue;
      if (!candidateMatchesTarget(item, target)) continue;
      const source = item.doubaoWatermarkSource || item.watermarkSource || item.source || 'network-no-watermark-api';
      const url = trustedNoWatermarkUrl(item.noWatermarkUrl || item.no_watermark_url || item.assetUrl || '', source);
      if (!url) continue;
      const ref = refFromObject(item, target || {});
      const video = buildTrustedUrlCandidate(ref, url, item.backupUrl || '', source, item);
      if (video) videos.push(video);
    }
    return videos;
  }

  function collectRouterMediaCandidates(value, target, path = 'router-data', depth = 0, seen = null, videos = []) {
    if (!value || typeof value !== 'object' || depth > 8 || videos.length > 60) return videos;
    const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
    if (nextSeen) {
      if (nextSeen.has(value)) return videos;
      nextSeen.add(value);
    }
    const fallback = targetRefs(target)[0] || {};
    const ref = refFromObject(value, fallback);
    if (Array.isArray(value)) {
      value.slice(-300).forEach((item, index) => collectRouterMediaCandidates(item, target, `${path}[${index}]`, depth + 1, nextSeen, videos));
      return videos;
    }
    for (const [key, item] of Object.entries(value).slice(-900)) {
      const source = `${path}.${key}`;
      if (typeof item === 'string') {
        const url = trustedNoWatermarkUrl(item, source);
        if (url) {
          const backup = value.backup_url || value.backupUrl || value.backup || '';
          const video = buildTrustedUrlCandidate(ref, url, backup, source, value);
          if (video && candidateMatchesTarget(video, target)) videos.push(video);
        }
        if ((item.trim().startsWith('{') || item.trim().startsWith('[')) && /video|media|play|main|backup|download|origin|watermark|url/i.test(item)) {
          try {
            collectRouterMediaCandidates(JSON.parse(item), target, `${source}:json`, depth + 1, nextSeen, videos);
          } catch (error) {}
        }
      } else if (item && typeof item === 'object') {
        collectRouterMediaCandidates(item, target, source, depth + 1, nextSeen, videos);
      }
    }
    return videos;
  }

  function mediaUrls(media) {
    return {
      main: firstString(
        media && media.main_url,
        media && media.mainUrl,
        media && media.play_url,
        media && media.playUrl,
        media && media.download_url,
        media && media.downloadUrl,
        media && media.url,
      ),
      backup: firstString(
        media && media.backup_url,
        media && media.backupUrl,
        media && media.backup_url_1,
        media && media.backupUrl1,
        media && media.backup,
        firstArrayString(media && media.backup_urls),
        firstArrayString(media && media.backupUrls),
      ),
    };
  }

  function isPlainObject(value) {
    return Boolean(value && typeof value === 'object' && !Array.isArray(value));
  }

  function firstString(...values) {
    for (const value of values) {
      if (typeof value === 'string' && value.trim()) return value;
      if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    }
    return '';
  }

  function firstArrayString(value) {
    if (!Array.isArray(value)) return '';
    for (const item of value) {
      if (typeof item === 'string' && item.trim()) return item;
      if (isPlainObject(item)) {
        const nested = firstString(item.main_url, item.mainUrl, item.url, item.play_url, item.playUrl, item.backup_url, item.backupUrl);
        if (nested) return nested;
      }
    }
    return '';
  }

  function hasMediaUrl(value) {
    const urls = mediaUrls(value);
    return Boolean(normalizeNoWatermarkUrl(urls.main) || normalizeNoWatermarkUrl(urls.backup));
  }

  function mediaFromPlayInfoNode(node) {
    if (!isPlainObject(node)) return null;
    const direct = [
      node.original_video_info,
      node.originalVideoInfo,
      node.original_media_info,
      node.originalMediaInfo,
      node.source_video_info,
      node.sourceVideoInfo,
      node.video_info,
      node.videoInfo,
      node.media_info,
      node.mediaInfo,
      node.play_info,
      node.playInfo,
      node,
    ];
    for (const item of direct) {
      if (isPlainObject(item) && hasMediaUrl(item)) return item;
    }
    return null;
  }

  function findMediaInValue(value, vid = '', depth = 0, seen = null) {
    if (!value || depth > 7) return null;
    if (typeof value !== 'object') return null;
    const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
    if (nextSeen) {
      if (nextSeen.has(value)) return null;
      nextSeen.add(value);
    }
    if (vid && isPlainObject(value)) {
      const keyed = value[vid] || value[String(vid)] || null;
      const keyedMedia = mediaFromPlayInfoNode(keyed);
      if (keyedMedia) return keyedMedia;
    }
    const media = mediaFromPlayInfoNode(value);
    if (media) return media;
    const entries = Array.isArray(value) ? value : Object.values(value);
    for (const item of entries.slice ? entries.slice(0, 500) : entries) {
      const nested = findMediaInValue(item, vid, depth + 1, nextSeen);
      if (nested) return nested;
    }
    return null;
  }

  function parseMgetPlayInfo(data, vid) {
    const roots = [
      data && data.play_infos,
      data && data.playInfos,
      data && data.play_info,
      data && data.playInfo,
      data && data.infos,
      data && data.list,
      data,
    ];
    for (const root of roots) {
      const media = findMediaInValue(root, vid);
      if (media) return media;
    }
    return null;
  }

  function buildCandidate(ref, media, source, attempts = []) {
    const urls = mediaUrls(media);
    const noWatermarkUrl = normalizeNoWatermarkUrl(urls.main);
    if (!noWatermarkUrl) throw new Error(`${source} returned empty or explicit-watermark URL`);
    const backup = normalizeUrl(urls.backup);
    const backupUrl = isLikelyVideoResourceUrl(backup) ? backup : '';
    const meta = media && media.meta || {};
    return {
      vid: ref.vid || '',
      messageId: ref.messageId || '',
      doubaoInternalTaskId: ref.doubaoInternalTaskId || '',
      doubaoInternalMessageId: ref.messageId || '',
      doubaoInternalVideoId: ref.vid || '',
      doubaoResultKey: ref.doubaoResultKey || '',
      networkAnchorTaskId: ref.networkAnchorTaskId || '',
      networkAnchorAccountId: ref.networkAnchorAccountId || '',
      networkAnchorSubmittedAt: Number(ref.networkAnchorSubmittedAt || 0),
      noWatermarkUrl,
      assetUrl: noWatermarkUrl,
      backupUrl,
      title: safeTitle(),
      width: Number(meta.width || media && media.width || 0) || 0,
      height: Number(meta.height || media && media.height || 0) || 0,
      definition: String(meta.definition || media && media.definition || ''),
      source: `watermark-extractor:${source}`,
      doubaoWatermarkSource: source,
      doubaoWatermarkResolved: true,
      doubaoWatermarkDiagnostic: {
        reason: 'resolved',
        source,
        attempts,
        at: Date.now(),
        ...diagnosticProfile(),
      },
      doubaoResultMeta: {
        source: `watermark-extractor:${source}`,
        watermarkSource: source,
        resultKeys: [
          ref.doubaoInternalTaskId ? `task:${ref.doubaoInternalTaskId}` : '',
          ref.networkAnchorTaskId ? `task:${ref.networkAnchorTaskId}` : '',
          ref.vid ? `video:${ref.vid}` : '',
          ref.messageId ? `message:${ref.messageId}` : '',
        ].filter(Boolean),
      },
      attempts,
    };
  }

  async function attempt(name, fn) {
    try {
      return { name, result: await fn(), error: '' };
    } catch (error) {
      return { name, result: null, error: String(error && error.message || error) };
    }
  }

  async function mgetPlayInfo(ref) {
    if (!ref.vid) throw new Error('missing vid');
    const requestBodies = [
      { vids: [ref.vid] },
      { vid_list: [ref.vid] },
      { video_ids: [ref.vid] },
      { vid: ref.vid },
    ];
    const errors = [];
    for (const body of requestBodies) {
      try {
        const data = await postJson('/api/biz/v1/common/mget_play_info', body);
        const media = parseMgetPlayInfo(data, ref.vid);
        if (media) return media;
        errors.push(`${Object.keys(body)[0]}: missing original_video_info/main_url`);
      } catch (error) {
        errors.push(`${Object.keys(body)[0]}: ${String(error && error.message || error)}`);
      }
    }
    throw new Error(errors.join('; '));
  }

  function parseSamanthaPlayInfo(data) {
    const roots = [
      data && data.original_media_info,
      data && data.originalMediaInfo,
      data && data.media_info,
      data && data.mediaInfo,
      data && data.original_video_info,
      data && data.originalVideoInfo,
      data && data.video_info,
      data && data.videoInfo,
      data,
    ];
    for (const item of roots) {
      if (isPlainObject(item) && hasMediaUrl(item)) return item;
    }
    return findMediaInValue(data);
  }

  async function samanthaMediaGetPlayInfo(ref) {
    if (!ref.vid) throw new Error('missing vid');
    const endpoint = SITE.isDola ? '/samantha/video/get_play_info' : '/samantha/media/get_play_info';
    const data = await postJson(
      endpoint,
      { key: ref.vid, type: 'video' },
      { 'agw-js-conv': 'str' },
      {
        aid: SITE.aid,
        real_aid: SITE.realAid,
        device_platform: 'web',
        language: SITE.language,
        region: SITE.region,
        sys_region: SITE.sysRegion,
        ...(SITE.zone ? { zone: SITE.zone } : {}),
        ...(SITE.webPlatform ? { web_platform: SITE.webPlatform } : {}),
        samantha_web: '1',
        'use-olympus-account': '1',
        version_code: SITE.versionCode,
        pc_version: SITE.pcVersion,
        pkg_type: 'release_version',
      },
    );
    const media = parseSamanthaPlayInfo(data);
    if (!media) throw new Error('missing media_info');
    return media;
  }

  async function aliceShareSave(ref) {
    if (!ref.messageId) throw new Error('missing messageId');
    const data = await postJson(
      '/alice/media/bigmusic/share_save',
      { message_id: ref.messageId },
      { 'agw-js-conv': 'str', 'x-tt-logid': '' },
    );
    const shareId = data && (data.share_id || data.shareId);
    if (!shareId) throw new Error('missing share_id');
    return shareId;
  }

  async function creativityShareInfo(ref, shareId) {
    if (!ref.vid) throw new Error('missing vid');
    if (!shareId) throw new Error('missing share_id');
    const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
    const data = await postJson(
      '/creativity/share/get_video_share_info',
      { share_id: shareId, vid: ref.vid, creation_id: '' },
      { 'agw-js-conv': 'str', 'x-tt-logid': '' },
      { web_tab_id: requestId },
    );
    const media = (Array.isArray(data.play_infos) && data.play_infos[0])
      || (Array.isArray(data.playInfos) && data.playInfos[0])
      || data.play_info
      || data.playInfo
      || data;
    if (!media) throw new Error('missing play_info');
    return {
      main_url: media.main_url || media.mainUrl || media.main || media.url || '',
      backup_url: media.backup_url || media.backupUrl || media.backup_url_1 || media.backupUrl1 || media.backup || '',
      width: media.width || media.meta && media.meta.width || 0,
      height: media.height || media.meta && media.meta.height || 0,
      definition: media.definition || media.meta && media.meta.definition || '',
      meta: media.meta || {},
    };
  }

  function generationTaskBody() {
    return {
      limit: 20,
      cursor: '0',
      with_meta: true,
      task_status_list: [1, 2, 3, 4, 6, 7],
      task_type_list: [1, 2, 3, 7],
    };
  }

  function isAuthEndpointError(error) {
    return /HTTP\s*(401|403)|login|unauthorized|forbidden|auth/i.test(String(error && error.message || error || ''));
  }

  function taskListFromData(data) {
    const candidates = [
      data && data.task_results,
      data && data.taskResults,
      data && data.tasks,
      data && data.list,
      data && data.items,
      data && data.results,
      Array.isArray(data) ? data : null,
    ];
    for (const item of candidates) {
      if (Array.isArray(item)) return item;
    }
    return [];
  }

  function generationRefFromTask(task) {
    if (!isPlainObject(task)) return null;
    const meta = isPlainObject(task.meta) ? task.meta : {};
    const commonMeta = isPlainObject(meta.common_meta) ? meta.common_meta : (isPlainObject(meta.commonMeta) ? meta.commonMeta : {});
    const extra = isPlainObject(commonMeta.extra) ? commonMeta.extra : (isPlainObject(meta.extra) ? meta.extra : {});
    const vid = firstString(
      commonMeta.vid,
      commonMeta.video_id,
      commonMeta.videoId,
      extra.vid,
      extra.video_id,
      extra.videoId,
      task.vid,
      task.video_id,
      task.videoId,
      findVid(task),
    );
    if (!/^v0/i.test(vid)) return null;
    const taskId = normalizeId(firstString(task.task_id, task.taskId, task.id, commonMeta.task_id, commonMeta.taskId));
    const messageId = normalizeId(firstString(task.message_id, task.messageId, task.msg_id, task.msgId, commonMeta.message_id, commonMeta.messageId));
    const threadId = normalizeId(firstString(task.thread_id, task.threadId, commonMeta.thread_id, commonMeta.threadId));
    const createdAt = Number(firstString(task.create_time, task.createTime, task.created_at, task.createdAt, meta.create_time, meta.createdAt));
    return {
      vid,
      messageId,
      source: 'generation-task-list',
      doubaoInternalTaskId: taskId,
      networkAnchorTaskId: taskId || threadId,
      networkAnchorSubmittedAt: Number.isFinite(createdAt) ? createdAt * (createdAt < 20000000000 ? 1000 : 1) : 0,
      doubaoResultKey: [taskId ? `task:${taskId}` : '', threadId ? `thread:${threadId}` : '', `video:${vid}`].filter(Boolean).join('|'),
      rawStatus: firstString(task.status, task.task_status, task.taskStatus),
    };
  }

  async function generationTaskRefs(target) {
    const endpoints = [
      '/api/biz/v2/common/generation_task_list',
      '/api/biz/v1/common/generation_task_list',
    ];
    const errors = [];
    for (const endpoint of endpoints) {
      try {
        const data = await postJson(endpoint, generationTaskBody());
        const refs = taskListFromData(data)
          .map((task) => generationRefFromTask(task))
          .filter(Boolean)
          .filter((ref) => candidateMatchesTarget(ref, target) || !target || !(
            target.vid || target.doubaoInternalVideoId || target.messageId || target.doubaoInternalMessageId || target.doubaoResultKey || target.networkAnchorTaskId
          ));
        if (refs.length) return uniqueRefs(refs).slice(0, 12);
        errors.push(`${endpoint}: no matching task refs`);
      } catch (error) {
        if (isAuthEndpointError(error)) {
          continue;
        }
        errors.push(`${endpoint}: ${String(error && error.message || error)}`);
      }
    }
    throw new Error(errors.join('; '));
  }

  async function getMediaInfo(ref) {
    if (!ref.vid) throw new Error('missing vid');
    const data = await postJson('/samantha/media/get_media_info', { key: ref.vid, type: 'vid' });
    const media = data.original_media_info || data.originalMediaInfo || data.media_info || data.mediaInfo;
    if (!media) throw new Error('missing media_info');
    return media;
  }

  async function bindShareId(ref) {
    if (!ref.messageId) throw new Error('missing messageId');
    const data = await postJson('/samantha/media/bind_share_id', { message_id: ref.messageId }, { 'x-tt-logid': '' });
    const shareId = data.share_id || data.shareId;
    if (!shareId) throw new Error('missing share_id');
    return shareId;
  }

  async function generateShareInfo(ref, shareId) {
    if (!ref.vid) throw new Error('missing vid');
    const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
    const data = await postJson(
      '/samantha/share/generate_video_share_info',
      { share_id: shareId, vid: ref.vid, need_watermark: '' },
      { 'x-tt-logid': '' },
      { request_id: requestId },
    );
    const media = (Array.isArray(data.play_infos) && data.play_infos[0]) || data.play_info || data;
    if (!media) throw new Error('missing play_info');
    return {
      main_url: media.main_url || media.mainUrl || media.mainUrl || media.url || '',
      backup_url: media.backup_url || media.backupUrl || media.backup || '',
      width: media.width || 0,
      height: media.height || 0,
      definition: media.definition || '',
    };
  }

  async function resolveRef(ref) {
    const attempts = [];
    const samanthaPlaySource = SITE.isDola ? 'samantha-video-get-play-info' : 'samantha-media-get-play-info';
    const samanthaPlayAttempt = await attempt(samanthaPlaySource, () => samanthaMediaGetPlayInfo(ref));
    attempts.push({ name: samanthaPlayAttempt.name, error: samanthaPlayAttempt.error });
    if (samanthaPlayAttempt.result) return buildCandidate(ref, samanthaPlayAttempt.result, samanthaPlaySource, attempts);

    const aliceAttempt = await attempt('alice-share-save', () => aliceShareSave(ref));
    attempts.push({ name: aliceAttempt.name, error: aliceAttempt.error });
    if (aliceAttempt.result) {
      const creativityAttempt = await attempt('creativity-share-info', () => creativityShareInfo(ref, aliceAttempt.result));
      attempts.push({ name: creativityAttempt.name, error: creativityAttempt.error });
      if (creativityAttempt.result) return buildCandidate(ref, creativityAttempt.result, 'creativity-share-info', attempts);
    }

    const mgetAttempt = await attempt('mget-play-info', () => mgetPlayInfo(ref));
    attempts.push({ name: mgetAttempt.name, error: mgetAttempt.error });
    if (mgetAttempt.result) return buildCandidate(ref, mgetAttempt.result, 'mget-play-info', attempts);

    const mediaAttempt = await attempt('get-media-info', () => getMediaInfo(ref));
    attempts.push({ name: mediaAttempt.name, error: mediaAttempt.error });
    if (mediaAttempt.result) return buildCandidate(ref, mediaAttempt.result, 'get-media-info', attempts);

    const bindAttempt = await attempt('bind-share-id', () => bindShareId(ref));
    attempts.push({ name: bindAttempt.name, error: bindAttempt.error });
    if (!bindAttempt.result) throw new Error(attempts.map((item) => `${item.name}: ${item.error}`).join('; '));

    const shareAttempt = await attempt('share-info', () => generateShareInfo(ref, bindAttempt.result));
    attempts.push({ name: shareAttempt.name, error: shareAttempt.error });
    if (shareAttempt.result) return buildCandidate(ref, shareAttempt.result, 'share-info', attempts);
    throw new Error(attempts.map((item) => `${item.name}: ${item.error}`).join('; '));
  }

  async function extractVideos(target) {
    const dataSources = collectPageDataSources();
    const stateVideos = [
      ...networkStateCandidates(target),
      ...collectSourceMediaCandidates(dataSources, target),
    ];
    if (stateVideos.length) return mergeVideos(stateVideos).slice(-12);

    const baseRefs = chooseRefs([
      ...targetRefs(target),
      ...collectSourceRefs(dataSources),
    ], target);
    let taskRefs = [];
    let taskListError = '';
    if (!baseRefs.some((ref) => ref.vid) || target) {
      try {
        taskRefs = await generationTaskRefs(target);
      } catch (error) {
        taskListError = String(error && error.message || error);
      }
    }
    const refs = chooseRefs([...baseRefs, ...taskRefs], target);
    if (!dataSources.length && !refs.length && !taskRefs.length) return [];
    if (!refs.length) return [];

    const videos = [];
    const errors = [];
    for (const ref of refs) {
      try {
        videos.push(await resolveRef(ref));
      } catch (error) {
        errors.push(`${ref.vid || ref.messageId || 'unknown'}: ${String(error && error.message || error)}`);
      }
    }
    if (!videos.length) throw new Error([errors.join('; '), taskListError ? `generation-task-list: ${taskListError}` : ''].filter(Boolean).join('; ') || 'no no-watermark candidates');
    return mergeVideos(videos);
  }

  function mergeVideos(videos) {
    const map = new Map();
    for (const video of videos.filter(Boolean)) {
      const key = [
        video.doubaoInternalTaskId,
        video.doubaoInternalVideoId || video.vid,
        video.doubaoInternalMessageId || video.messageId,
        video.noWatermarkUrl,
      ].filter(Boolean).join('|') || video.noWatermarkUrl || Math.random().toString(36);
      if (!map.has(key)) map.set(key, video);
      else map.set(key, { ...map.get(key), ...video });
    }
    return [...map.values()];
  }

  function reply(requestId, payload) {
    window.postMessage({
      source: CHANNEL,
      type: 'extract-result',
      requestId,
      ...payload,
    }, '*');
  }

  window.addEventListener('message', async (event) => {
    const data = event.data;
    if (event.source !== window || !data || data.source !== CHANNEL || data.type !== 'extract') return;
    try {
      const videos = await extractVideos(data.target || null);
      reply(data.requestId, {
        ok: true,
        videos,
        diagnostic: { reason: 'resolved', count: videos.length, at: Date.now(), ...diagnosticProfile() },
      });
    } catch (error) {
      reply(data.requestId, {
        ok: false,
        error: String(error && error.message || error),
        diagnostic: { reason: 'extract-error', at: Date.now(), ...diagnosticProfile() },
      });
    }
  });
}());
