const VIDEO_EXT = '.mp4';
const DOWNLOAD_DIR = 'doubao-videos';

function normalizeUrl(value) {
  let raw = String(value || '').trim();
  if (!raw || raw.startsWith('blob:') || raw.startsWith('data:')) return '';
  raw = raw.replace(/\\u0026/g, '&').replace(/&amp;/g, '&').replace(/\\\//g, '/');
  if (/^https%3A%2F%2F/i.test(raw)) {
    try { raw = decodeURIComponent(raw); } catch (error) {}
  }
  try { return new URL(raw).toString(); } catch (error) { return ''; }
}

function safeName(value) {
  return String(value || '')
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '_')
    .replace(/\s+/g, ' ')
    .slice(0, 80)
    || 'doubao-video';
}

function isExplicitWatermarkUrl(value) {
  const text = String(value || '');
  return /video_gen_watermark(?:_[a-z0-9]+)*/i.test(text)
    || /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|logo_type=(?!video_gen_no_watermark)[^&#]*watermark)/i.test(text);
}

function isBlockedWatermarkVariantUrl(value) {
  return /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|(?:[?&]logo_type=(?!video_gen_no_watermark)[^&#]*watermark))/i.test(String(value || ''));
}

function toNoWatermarkUrl(value) {
  const url = normalizeUrl(value);
  if (!url || isBlockedWatermarkVariantUrl(url)) return '';
  return url.replace(/video_gen_watermark(?:_[a-z0-9]+)*/ig, 'video_gen_no_watermark');
}

function isNoWatermarkVideoUrl(value) {
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
  if (!url || isExplicitWatermarkUrl(url) || isLikelyHtmlDocumentUrl(url)) return false;
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

function downloadUrlFromVideo(video) {
  return toNoWatermarkUrl(video && (video.noWatermarkUrl || video.no_watermark_url || video.assetUrl || video.url || ''));
}

function filenameFor(video, url) {
  const title = safeName(video && video.title || 'doubao-video');
  const id = safeName(video && (video.doubaoResultKey || video.doubaoInternalTaskId || video.doubaoInternalVideoId || video.vid || video.messageId) || Date.now().toString());
  return `${DOWNLOAD_DIR}/${title}-${id}${VIDEO_EXT}`;
}

function assertVideo(video) {
  const url = downloadUrlFromVideo(video);
  if (!url) throw new Error('missing video URL');
  if (!isNoWatermarkVideoUrl(url)) throw new Error('no-watermark URL was not resolved');
  if (!isLikelyVideoResourceUrl(url)) throw new Error('resolved URL is not a video');
  return url;
}

async function waitForDownloadComplete(downloadId, timeoutMs = 30 * 60 * 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const items = await chrome.downloads.search({ id: downloadId });
    const item = items && items[0];
    if (item && item.state === 'complete') return item;
    if (item && item.state === 'interrupted') throw new Error(item.error || 'download interrupted');
    await new Promise((resolve) => setTimeout(resolve, 1000));
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
  const url = assertVideo(video);
  const downloadId = await chrome.downloads.download({
    url,
    filename: filenameFor(video, url),
    saveAs: false,
  });
  const item = await waitForDownloadComplete(downloadId);
  if (!isDownloadedVideoItem(item, url)) {
    await discardInvalidDownload(downloadId);
    throw new Error('downloaded response is not a video');
  }
  return {
    downloadId,
    filename: item && item.filename || '',
    url,
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== 'download-video') return false;
  downloadVideo(message.video)
    .then((result) => sendResponse({ ok: true, ...(result || {}) }))
    .catch((error) => sendResponse({ ok: false, error: error && error.message || String(error) }));
  return true;
});
