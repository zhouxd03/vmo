const DBVD_VIDEO_EXT = '.mp4';
let dbvdAuthCache = { ok: false, checkedAt: 0, expiresAt: 0 };

async function readLaunchLock() {
  const url = chrome.runtime.getURL('launch-lock.json');
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error('missing launch lock');
  const data = await response.json();
  if (!data || !data.token || !data.port || !data.expiresAt) throw new Error('invalid launch lock');
  if (Date.now() > Number(data.expiresAt)) throw new Error('launch lock expired');
  return data;
}

async function checkAuthorized() {
  const now = Date.now();
  if (dbvdAuthCache.ok && now - dbvdAuthCache.checkedAt < 15000 && now < dbvdAuthCache.expiresAt) return true;
  try {
    const lock = await readLaunchLock();
    const authUrl = 'http://127.0.0.1:' + Number(lock.port) + '/__dbvd_auth?token=' + encodeURIComponent(lock.token) + '&rid=' + encodeURIComponent(chrome.runtime.id);
    const response = await fetch(authUrl, { cache: 'no-store' });
    const result = await response.json().catch(() => null);
    const ok = Boolean(response.ok && result && result.ok);
    dbvdAuthCache = { ok, checkedAt: now, expiresAt: Number(result && result.expiresAt || lock.expiresAt || 0) };
    return ok;
  } catch (error) {
    dbvdAuthCache = { ok: false, checkedAt: now, expiresAt: 0 };
    return false;
  }
}

async function requestTask(method, body, taskId, accountId) {
  const lock = await readLaunchLock();
  const url = 'http://127.0.0.1:' + Number(lock.port)
    + '/__dbvd_task?token=' + encodeURIComponent(lock.token)
    + '&rid=' + encodeURIComponent(chrome.runtime.id)
    + (taskId ? '&taskId=' + encodeURIComponent(taskId) : '')
    + (accountId ? '&accountId=' + encodeURIComponent(accountId) : '');
  const response = await fetch(url, {
    method,
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || !result || !result.ok) throw new Error(result && result.error || 'task request failed');
  return result.task || null;
}

async function sendHeartbeat(payload) {
  const lock = await readLaunchLock();
  const url = 'http://127.0.0.1:' + Number(lock.port)
    + '/__dbvd_heartbeat?token=' + encodeURIComponent(lock.token)
    + '&rid=' + encodeURIComponent(chrome.runtime.id)
    + '&version=' + encodeURIComponent(chrome.runtime.getManifest().version || '');
  const response = await fetch(url, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      extensionId: chrome.runtime.id,
      version: chrome.runtime.getManifest().version || '',
      ...(payload || {}),
    }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || !result || !result.ok) throw new Error(result && result.error || 'heartbeat failed');
  return result;
}

async function getGenerationImageUrl(taskId) {
  if (!await checkAuthorized()) throw new Error('Runtime authorization failed');
  const lock = await readLaunchLock();
  if (!taskId) throw new Error('missing task id');
  return 'http://127.0.0.1:' + Number(lock.port)
    + '/__dbvd_file?token=' + encodeURIComponent(lock.token)
    + '&rid=' + encodeURIComponent(chrome.runtime.id)
    + '&taskId=' + encodeURIComponent(taskId);
}

async function claimGenerationTask(taskId, accountId) {
  if (!await checkAuthorized()) throw new Error('Runtime authorization failed');
  return requestTask('GET', null, taskId, accountId);
}

async function updateGenerationTask(input) {
  if (!await checkAuthorized()) throw new Error('Runtime authorization failed');
  if (input && input.id && !input.automationRunnerId) input.automationRunnerId = chrome.runtime.id;
  return requestTask('POST', input || {}, input && input.id || '', input && input.accountId || '');
}

function assertVideo(video) {
  const noWatermark = trustedNoWatermarkUrl(video);
  if (video && noWatermark) video.assetUrl = noWatermark;
  if (video && !video.assetUrl && video.backupUrl) video.assetUrl = video.backupUrl;
  const urls = videoDownloadUrls(video);
  if (!urls.length && video && video.requireNoWatermark) throw new Error('No trusted no-watermark URL; blocked watermarked download');
  if (!urls.length) throw new Error('No usable item URL');
  if (video && video.requireNoWatermark && !urls.every((url) => isNoWatermarkVideoUrl(url))) {
    throw new Error('No-watermark download requires a trusted clean URL');
  }
}

function videoDownloadUrls(video) {
  const urls = [];
  const noWatermark = trustedNoWatermarkUrl(video);
  if (noWatermark) urls.push(noWatermark);
  if (video && video.requireNoWatermark) return urls;
  for (const value of [video && video.assetUrl, video && video.backupUrl]) {
    const raw = normalizeDoubaoVideoUrl(value);
    if (!raw || urls.includes(raw) || !isLikelyVideoResourceUrl(raw)) continue;
    try {
      const parsed = new URL(raw);
      if (['http:', 'https:'].includes(parsed.protocol)) urls.push(raw);
    } catch (error) {}
  }
  return urls.sort((a, b) => Number(isNoWatermarkVideoUrl(b)) - Number(isNoWatermarkVideoUrl(a)));
}

function normalizeDoubaoVideoUrl(value) {
  let raw = String(value || '').trim();
  if (!raw || raw.startsWith('blob:') || raw.startsWith('data:')) return '';
  raw = raw.replace(/\\u0026/g, '&').replace(/&amp;/g, '&').replace(/\\\//g, '/');
  if (/^https%3A%2F%2F/i.test(raw)) {
    try { raw = decodeURIComponent(raw); } catch (error) {}
  }
  return raw;
}

function normalizeDoubaoNoWatermarkUrl(value) {
  const raw = toDoubaoNoWatermarkUrl(value);
  if (!raw || isExplicitDoubaoWatermarkUrl(raw)) return '';
  if (!isNoWatermarkVideoUrl(raw)) return '';
  if (!isLikelyVideoResourceUrl(raw)) return '';
  return raw;
}

function isNoWatermarkVideoUrl(value) {
  const text = String(value || '');
  return /(?:[?&]lr=video_gen_no_watermark|video_gen_no_watermark)/i.test(text)
    || isDolaCleanVideoUrl(text);
}

function isDolaCleanVideoUrl(value) {
  const url = normalizeDoubaoVideoUrl(value);
  if (!url || isExplicitDoubaoWatermarkUrl(url)) return false;
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

function isExplicitDoubaoWatermarkUrl(value) {
  const text = String(value || '');
  return /video_gen_watermark(?:_[a-z0-9]+)*/i.test(text)
    || /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|logo_type=(?!video_gen_no_watermark)[^&#]*watermark)/i.test(text);
}

function isBlockedWatermarkVariantUrl(value) {
  return /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|(?:[?&]logo_type=(?!video_gen_no_watermark)[^&#]*watermark))/i.test(String(value || ''));
}

function toDoubaoNoWatermarkUrl(value) {
  const raw = normalizeDoubaoVideoUrl(value);
  if (!raw || isBlockedWatermarkVariantUrl(raw)) return '';
  return raw.replace(/video_gen_watermark(?:_[a-z0-9]+)*/ig, 'video_gen_no_watermark');
}

function isLikelyHtmlDocumentUrl(value) {
  const url = normalizeDoubaoVideoUrl(value);
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
  const url = normalizeDoubaoVideoUrl(value);
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

function isTrustedWatermarkSource(video) {
  if (!video || typeof video !== 'object') return false;
  const marker = String(video.doubaoWatermarkSource || video.watermarkSource || video.source || '');
  if (/derived|dom-|performance|network-capture|native/i.test(marker)) return false;
  if (/(?:samantha-(?:media|video)-get-play-info|samantha[-_/]?(?:media|video)[-_/]?get[-_]?play[-_]?info|alice-share-save|creativity-share-info|get-play-info|mget-play-info|generation-task-list|get-media-info|share-info|watermark-extractor|no-watermark-api|network-no-watermark|doubao-api)/i.test(marker)) return true;
  const meta = video.doubaoResultMeta || {};
  const metaSource = String(meta.watermarkSource || meta.source || '');
  return /(?:samantha-(?:media|video)-get-play-info|samantha[-_/]?(?:media|video)[-_/]?get[-_]?play[-_]?info|alice-share-save|creativity-share-info|get-play-info|mget-play-info|generation-task-list|get-media-info|share-info|watermark-extractor|no-watermark-api|network-no-watermark|doubao-api)/i.test(metaSource);
}

function trustedNoWatermarkUrl(video) {
  const url = normalizeDoubaoNoWatermarkUrl(video && (video.noWatermarkUrl || video.no_watermark_url));
  if (!url || !isTrustedWatermarkSource(video)) return '';
  return url;
}

function safeName(value) {
  return String(value || '').trim().replace(/[\\/:*?"<>|]+/g, '_').replace(/\s+/g, ' ').slice(0, 80) || 'page-item';
}

function buildFilename(video) {
  const title = safeName(video.title || 'page-item');
  const vid = safeName(video.doubaoResultKey || video.doubaoInternalTaskId || video.doubaoInternalVideoId || video.doubaoInternalMessageId || video.vid || video.messageId || Date.now().toString());
  return title + '-' + vid + DBVD_VIDEO_EXT;
}

function selectedVideoPatch(video) {
  const keys = [
    video && video.doubaoResultKey ? String(video.doubaoResultKey) : '',
    video && video.doubaoInternalTaskId ? `task:${video.doubaoInternalTaskId}` : '',
    video && video.doubaoInternalVideoId ? `video:${video.doubaoInternalVideoId}` : '',
    video && video.doubaoInternalMessageId ? `message:${video.doubaoInternalMessageId}` : '',
    video && video.vid ? `vid:${video.vid}` : '',
    video && video.messageId ? `message:${video.messageId}` : '',
    video && video.noWatermarkUrl ? `url:${video.noWatermarkUrl}` : '',
    video && video.assetUrl ? `url:${video.assetUrl}` : '',
    video && video.backupUrl ? `url:${video.backupUrl}` : '',
  ].filter(Boolean);
  return {
    doubaoInternalTaskId: video && video.doubaoInternalTaskId || '',
    doubaoInternalMessageId: video && (video.doubaoInternalMessageId || video.messageId) || '',
    doubaoInternalVideoId: video && (video.doubaoInternalVideoId || video.vid) || '',
    doubaoResultKey: video && (video.doubaoResultKey || keys[0]) || '',
    networkAnchorTaskId: video && video.networkAnchorTaskId || '',
    networkAnchorAccountId: video && video.networkAnchorAccountId || '',
    networkAnchorSubmittedAt: Number(video && video.networkAnchorSubmittedAt || 0),
    networkAnchorTrusted: Boolean(video && video.networkAnchorTrusted),
    networkPromptMatched: Boolean(video && video.networkPromptMatched),
    noWatermarkUrl: video && video.noWatermarkUrl || '',
    doubaoWatermarkSource: video && video.doubaoWatermarkSource || '',
    doubaoWatermarkResolved: Boolean(trustedNoWatermarkUrl(video)),
    doubaoResultMeta: {
      ...(video && video.doubaoResultMeta || {}),
      watermarkSource: video && video.doubaoWatermarkSource || video && video.doubaoResultMeta && video.doubaoResultMeta.watermarkSource || '',
      resultKeys: [...new Set([...(video && video.doubaoResultMeta && video.doubaoResultMeta.resultKeys || []), ...keys])].slice(-40),
      source: video && video.source || '',
    },
  };
}

async function waitForDownloadComplete(downloadId, timeoutMs = 30 * 60 * 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const items = await chrome.downloads.search({ id: downloadId });
    const item = items && items[0];
    if (item && item.state === 'complete') return item;
    if (item && item.state === 'interrupted') throw new Error(item.error || 'download interrupted');
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('download completion timeout');
}

function isDownloadedVideoItem(item, sourceUrl) {
  const filename = String(item && item.filename || '');
  const mime = String(item && item.mime || '').toLowerCase();
  const finalUrl = String(item && (item.finalUrl || item.url) || sourceUrl || '');
  if (/\.(?:html?|shtml)(?:$|[?#])/i.test(filename)) return false;
  if (/text\/html|application\/xhtml\+xml|application\/json|text\/plain/i.test(mime)) return false;
  if (isLikelyHtmlDocumentUrl(finalUrl) || isLikelyHtmlDocumentUrl(sourceUrl)) return false;
  if (/^video\//i.test(mime)) return true;
  if (/\.(?:mp4|mov|webm|m4v)$/i.test(filename)) return true;
  if (isLikelyVideoResourceUrl(finalUrl) || isLikelyVideoResourceUrl(sourceUrl)) {
    return !mime || /octet-stream|binary|mp4|quicktime|webm|video/i.test(mime);
  }
  return false;
}

async function discardInvalidDownload(downloadId) {
  try { await chrome.downloads.removeFile(downloadId); } catch (error) {}
  try { await chrome.downloads.erase({ id: downloadId }); } catch (error) {}
}

async function downloadVideo(video) {
  if (!await checkAuthorized()) throw new Error('Runtime authorization failed');
  assertVideo(video);
  let lastError = null;
  for (const url of videoDownloadUrls(video)) {
    try {
      const downloadId = await chrome.downloads.download({ url, filename: buildFilename(video), saveAs: false });
      const item = await waitForDownloadComplete(downloadId);
      if (!isDownloadedVideoItem(item, url)) {
        await discardInvalidDownload(downloadId);
        throw new Error('downloaded response is not a video');
      }
      return {
        downloadId,
        filename: item && item.filename || '',
        url,
        noWatermark: trustedNoWatermarkUrl(video) === url,
      };
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error('video download failed');
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message) return false;
  if (message.type === 'dbvd-auth-check') {
    checkAuthorized().then((ok) => sendResponse({ ok })).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === 'download-video') {
    downloadVideo(message.video)
      .then(async (result) => {
        if (message.taskId) {
          await updateGenerationTask({
            id: message.taskId,
            status: 'downloaded',
            downloadId: result && result.downloadId || '',
            downloadFilename: result && result.filename || '',
            assetUrl: result && result.url || message.video && (message.video.assetUrl || message.video.backupUrl) || '',
            backupUrl: message.video && message.video.backupUrl || '',
            ...selectedVideoPatch(message.video || {}),
            doubaoWatermarkResolved: Boolean(result && result.noWatermark),
            doubaoWatermarkDiagnostic: {
              reason: result && result.noWatermark ? 'downloaded-no-watermark' : 'downloaded-fallback',
              at: Date.now(),
              url: result && result.url || '',
            },
          }).catch(() => {});
        }
        sendResponse({ ok: true, ...(result || {}) });
      })
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === 'claim-generation-task') {
    claimGenerationTask(message.taskId, message.accountId).then((task) => sendResponse({ ok: true, task })).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === 'update-generation-task') {
    updateGenerationTask(message.task).then((task) => sendResponse({ ok: true, task })).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === 'dbvd-heartbeat') {
    sendHeartbeat(message.payload).then((result) => sendResponse({ ok: true, result })).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === 'get-generation-image-url') {
    getGenerationImageUrl(message.taskId).then((url) => sendResponse({ ok: true, url })).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  return false;
});
