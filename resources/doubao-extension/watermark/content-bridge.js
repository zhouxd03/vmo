(function () {
  'use strict';

  const FLAG = '__vmoDoubaoWatermarkBridgeInstalled';
  const CHANNEL = 'doubao-video-download-extractor';
  const TOAST_ID = 'vmo-doubao-watermark-bridge-toast';
  if (window[FLAG]) return;
  window[FLAG] = true;

  function textOf(element) {
    if (!element) return '';
    return String(
      element.innerText
      || element.textContent
      || element.getAttribute && (element.getAttribute('aria-label') || element.getAttribute('title'))
      || ''
    ).replace(/\s+/g, ' ').trim();
  }

  function controlHintOf(element, depth = 4) {
    const parts = [];
    let current = element instanceof Element ? element : null;
    for (let index = 0; current && index < depth; index += 1, current = current.parentElement) {
      parts.push(
        textOf(current),
        current.getAttribute && current.getAttribute('aria-label'),
        current.getAttribute && current.getAttribute('title'),
        current.getAttribute && current.getAttribute('download'),
        current.getAttribute && current.getAttribute('href'),
        current.getAttribute && current.getAttribute('role'),
        current.getAttribute && current.getAttribute('data-testid'),
        current.getAttribute && current.getAttribute('data-test-id'),
        current.id,
        current.className,
      );
    }
    return parts.filter(Boolean).map((item) => String(item)).join(' ').replace(/\s+/g, ' ').trim();
  }

  function isDoubaoAuthOrNavigationControl(element) {
    let current = element instanceof Element ? element : null;
    for (let depth = 0; current && depth < 4; depth += 1, current = current.parentElement) {
      if (isLargeNonInteractiveContainer(current)) continue;
      const directHint = directControlHintOf(current);
      if (/(?:登录|登陆|注册|验证码|手机号|邮箱|账号|密码|扫码|下载豆包|客户端下载|下载电脑版|电脑版|桌面版|客户端|打开客户端|下载安装|Microsoft\s*Store|Windows|macOS|App\s*Store|应用商店|会员|充值|个人中心)/i.test(directHint)) return true;
      if (/(?:设置|新对话|更多)/i.test(directHint)) return true;
    }
    return false;
  }

  function isLargeNonInteractiveContainer(element) {
    if (!(element instanceof Element)) return false;
    const role = String(element.getAttribute && element.getAttribute('role') || '');
    const tag = String(element.tagName || '').toUpperCase();
    const isInteractive = /^(A|BUTTON|INPUT|TEXTAREA|SELECT|LABEL|SUMMARY)$/.test(tag) || /button|link|menuitem|tab|textbox|combobox|switch|checkbox/i.test(role);
    if (isInteractive) return false;
    const rect = element.getBoundingClientRect && element.getBoundingClientRect();
    return Boolean(rect && rect.width >= 240 && rect.height >= 120);
  }

  function directControlHintOf(element) {
    if (!(element instanceof Element)) return '';
    return [
      textOf(element),
      element.getAttribute && element.getAttribute('aria-label'),
      element.getAttribute && element.getAttribute('title'),
      element.getAttribute && element.getAttribute('download'),
      element.getAttribute && element.getAttribute('href'),
      element.getAttribute && element.getAttribute('role'),
      element.id,
      element.className,
    ].filter(Boolean).map((item) => String(item)).join(' ').replace(/\s+/g, ' ').trim();
  }

  function hasCompletedStatusSignal(text) {
    return /(?:视频生成好(?:啦|了)?|生成好(?:啦|了)?|生成完成|视频已生成|已生成视频|创作完成|下载视频|保存视频)/i.test(String(text || ''));
  }

  function isLikelyDirectVideoUrl(value) {
    const url = normalizeUrl(value);
    if (!url) return false;
    return isLikelyVideoResourceUrl(url);
  }

  function resultCardHasVideoSignal(card) {
    if (!card || !card.querySelector) return false;
    return Boolean(card.querySelector('video, canvas, [data-video-url], [data-download-url], a[href*=".mp4"], a[href*=".webm"], a[href*=".mov"], a[href*="video"], [src*=".mp4"], [src*=".webm"], [src*="video"]'));
  }

  function isLikelyVideoResultCard(card, trigger) {
    if (!(card instanceof Element) || card === document.body || card === document.documentElement) return false;
    if (isDoubaoAuthOrNavigationControl(card)) return false;
    const href = trigger && trigger.tagName === 'A'
      ? String(trigger.href || trigger.getAttribute('href') || '')
      : String(trigger && trigger.getAttribute && trigger.getAttribute('href') || '');
    const cardText = textOf(card);
    const hasDirectVideoUrl = isLikelyDirectVideoUrl(href);
    const hasVideoInCard = resultCardHasVideoSignal(card);
    const hasResultText = hasCompletedStatusSignal(cardText) || /下载视频|保存视频|播放|Seedance|无水印/i.test(cardText);
    return Boolean(hasDirectVideoUrl || (hasVideoInCard && hasResultText));
  }

  function isDownloadTrigger(element) {
    let current = element instanceof Element ? element : null;
    for (let depth = 0; current && depth < 5; depth += 1, current = current.parentElement) {
      if (current.id === TOAST_ID || current.closest && current.closest('#' + TOAST_ID)) return false;
      if (current.closest && current.closest('#page-service-root')) return false;
      if (isDoubaoAuthOrNavigationControl(current)) return false;
      const label = [
        textOf(current),
        current.getAttribute && current.getAttribute('aria-label'),
        current.getAttribute && current.getAttribute('title'),
        current.getAttribute && current.getAttribute('download'),
      ].filter(Boolean).join(' ');
      if (/无水印/i.test(label)) return current;
      if (/下載|下载|保存|导出|download|save/i.test(label) || (current.tagName === 'A' && current.hasAttribute('download'))) {
        const card = closestResultCard(current);
        return isLikelyVideoResultCard(card, current) ? current : false;
      }
    }
    return false;
  }

  function closestResultCard(element) {
    let current = element instanceof Element ? element : null;
    for (let depth = 0; current && depth < 10; depth += 1, current = current.parentElement) {
      const rect = current.getBoundingClientRect && current.getBoundingClientRect();
      const text = textOf(current);
      const hasResultText = hasCompletedStatusSignal(text) || /下载视频|保存视频|播放|Seedance|无水印|download video|save video|play/i.test(text);
      const hasVideoSignal = resultCardHasVideoSignal(current);
      if ((hasResultText || hasVideoSignal) && rect && rect.width >= 120 && rect.height >= 60) return current;
      if (hasVideoSignal && rect && rect.width >= 180 && rect.height >= 120) return current;
    }
    return element instanceof Element ? element : document.body;
  }

  function normalizeUrl(value) {
    let raw = String(value || '').trim();
    if (!raw || raw.startsWith('blob:') || raw.startsWith('data:')) return '';
    raw = raw.replace(/\\u0026/g, '&').replace(/&amp;/g, '&').replace(/\\\//g, '/');
    if (/^https%3A%2F%2F/i.test(raw)) {
      try { raw = decodeURIComponent(raw); } catch (error) {}
    }
    try {
      return new URL(raw, location.href).toString();
    } catch (error) {
      return '';
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
    if (isExplicitWatermarkUrl(url)) return false;
    if (/\.(?:png|jpe?g|webp|gif|avif|svg|heic|heif)(?:~|$|[?#])/i.test(url)) return false;
    if (/image_generation|tplv-[^/?#]*image|\.image(?:$|[?#~])/i.test(url) && !/video|play|media/i.test(url)) return false;
    return /\.(?:mp4|mov|webm|m4v)(?:$|[?#])|(?:[?&](?:vid|video_id|videoId|item_id|itemId|media_id|play_id)=)|\/video(?:[_/-]|$)|video_generation|play_?url|main_?url|download_?url|backup_?url|media_?url|tos-[^/?#]+\/obj\/[^?#]*(?:video|media|mp4)|byteimg\.com\/[^?#]*(?:video|tos|obj|mp4)|bytevod|voddos|vod-|video\.?tos/i.test(url);
  }

  function isExplicitWatermarkUrl(value) {
    const text = String(value || '');
    return /video_gen_watermark(?:_[a-z0-9]+)*/i.test(text)
      || /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|logo_type=(?!video_gen_no_watermark)[^&#]*watermark)/i.test(text);
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

  function extractVid(value) {
    const text = String(value || '');
    const match = text.match(/(?:^|[^\w-])(v0[a-zA-Z0-9_-]{8,})(?:$|[^\w-])/i);
    return match ? match[1] : '';
  }

  function extractMessageId(element) {
    let current = element instanceof Element ? element : null;
    for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
      for (const name of current.getAttributeNames ? current.getAttributeNames() : []) {
        const value = current.getAttribute(name) || '';
        if (/message|conversation|chat|item|data-id|id/i.test(name) && /\d{8,}/.test(value)) {
          return value.match(/\d{8,}/)[0].slice(0, 120);
        }
      }
    }
    return '';
  }

  function buildTargetFromTrigger(trigger) {
    const card = closestResultCard(trigger);
    const url = pickBestTargetUrl(card, trigger);
    const cardText = textOf(card).slice(0, 800);
    return {
      vid: extractVid(url) || extractVid(cardText),
      messageId: extractMessageId(card),
      assetUrl: url,
      backupUrl: url,
    };
  }

  function pickBestTargetUrl(card, trigger) {
    const candidates = [];
    const add = (value, source = '') => {
      const url = normalizeUrl(value);
      if (!url) return;
      candidates.push({ url, source });
    };
    if (trigger) {
      add(trigger.currentSrc || trigger.src || trigger.href || trigger.getAttribute && (
        trigger.getAttribute('src')
        || trigger.getAttribute('href')
        || trigger.getAttribute('data-src')
        || trigger.getAttribute('data-url')
        || trigger.getAttribute('data-video-url')
        || trigger.getAttribute('data-download-url')
      ), 'trigger');
    }
    if (card && card.querySelectorAll) {
      for (const video of card.querySelectorAll('video')) add(video.currentSrc || video.src || video.getAttribute('src'), 'video');
      for (const element of card.querySelectorAll('[data-video-url], [data-download-url], [data-url], [data-src], [src], a[href]')) {
        add(
          element.currentSrc
          || element.src
          || element.href
          || element.getAttribute('data-video-url')
          || element.getAttribute('data-download-url')
          || element.getAttribute('data-url')
          || element.getAttribute('data-src')
          || element.getAttribute('src')
          || element.getAttribute('href'),
          'card',
        );
      }
    }
    const ranked = candidates
      .filter((item, index, list) => item.url && list.findIndex((other) => other.url === item.url) === index)
      .map((item) => ({ ...item, score: scoreTargetUrl(item.url, item.source) }))
      .filter((item) => item.score > -1000)
      .sort((a, b) => b.score - a.score);
    return ranked[0] ? ranked[0].url : '';
  }

  function scoreTargetUrl(url, source = '') {
    let score = 0;
    if (isNoWatermarkVideoUrl(url)) score += 4000;
    if (isLikelyVideoResourceUrl(url)) score += 1200;
    if (/^video$/i.test(source)) score += 600;
    if (/currentSrc|video/i.test(source)) score += 240;
    if (isLikelyHtmlDocumentUrl(url)) score -= 5000;
    if (/\.(?:png|jpe?g|webp|gif|avif|svg|heic|heif)(?:~|$|[?#])/i.test(url)) score -= 2000;
    return score;
  }

  function extractCandidates(target = null, timeoutMs = 24000) {
    const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        window.removeEventListener('message', onMessage);
        reject(new Error('无水印解析超时'));
      }, timeoutMs);
      function onMessage(event) {
        const data = event.data;
        if (!data || data.source !== CHANNEL || data.type !== 'extract-result' || data.requestId !== requestId) return;
        window.removeEventListener('message', onMessage);
        clearTimeout(timer);
        if (!data.ok) {
          reject(new Error(data.error || '无水印解析失败'));
          return;
        }
        resolve(Array.isArray(data.videos) ? data.videos : []);
      }
      window.addEventListener('message', onMessage);
      window.postMessage({ source: CHANNEL, type: 'extract', requestId, target }, '*');
    });
  }

  async function extractCandidatesQuiet(target = null, timeoutMs = 24000) {
    try {
      return await extractCandidates(target, timeoutMs);
    } catch (error) {
      console.debug('[vmo-watermark-bridge] extractor pending', error);
      return [];
    }
  }

  function hasNoWatermarkUrl(video) {
    const url = normalizeUrl(video && (video.noWatermarkUrl || video.no_watermark_url || video.assetUrl));
    if (!url) return false;
    if (isExplicitWatermarkUrl(url)) return false;
    if (!isNoWatermarkVideoUrl(url)) return false;
    if (!isLikelyVideoResourceUrl(url)) return false;
    return true;
  }

  function shareTarget(video, target) {
    if (!video || !target) return false;
    const haystack = [
      video.vid,
      video.doubaoInternalVideoId,
      video.messageId,
      video.doubaoInternalMessageId,
      video.doubaoInternalTaskId,
      video.doubaoResultKey,
      video.noWatermarkUrl,
      video.assetUrl,
      video.backupUrl,
    ].filter(Boolean).join(' ');
    return [target.vid, target.messageId, target.assetUrl].filter((item) => String(item || '').length >= 6).some((item) => haystack.includes(item));
  }

  function pickCandidate(videos, target) {
    const resolved = (Array.isArray(videos) ? videos : []).filter(hasNoWatermarkUrl);
    if (!resolved.length) return null;
    return resolved.find((video) => shareTarget(video, target)) || (resolved.length === 1 ? resolved[0] : resolved[resolved.length - 1]);
  }

  function safeDownloadPayload(video) {
    const noWatermarkUrl = normalizeUrl(video.noWatermarkUrl || video.no_watermark_url || video.assetUrl);
    if (!isNoWatermarkVideoUrl(noWatermarkUrl)) throw new Error('no-watermark URL was not resolved');
    if (!isLikelyVideoResourceUrl(noWatermarkUrl)) throw new Error('resolved URL is not a video');
    return {
      ...video,
      noWatermarkUrl,
      assetUrl: noWatermarkUrl,
      requireNoWatermark: true,
      title: video.title || document.title || 'doubao-video',
    };
  }

  function showToast(message, tone = 'info') {
    let toast = document.getElementById(TOAST_ID);
    if (!toast) {
      toast = document.createElement('div');
      toast.id = TOAST_ID;
      toast.style.cssText = [
        'position:fixed',
        'right:18px',
        'bottom:18px',
        'z-index:2147483647',
        'max-width:360px',
        'padding:10px 12px',
        'border-radius:8px',
        'font:13px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
        'box-shadow:0 12px 36px rgba(15,23,42,.18)',
        'background:#111827',
        'color:#fff',
        'pointer-events:none',
      ].join(';');
      document.documentElement.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.background = tone === 'error' ? '#b91c1c' : tone === 'ok' ? '#047857' : '#111827';
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.remove(), 4200);
  }

  async function downloadNoWatermarkFromTrigger(trigger) {
    const target = buildTargetFromTrigger(trigger);
    showToast('正在解析无水印视频...');
    let videos = await extractCandidatesQuiet(target);
    let candidate = pickCandidate(videos, target);
    if (!candidate) {
      videos = await extractCandidatesQuiet(null);
      candidate = pickCandidate(videos, target);
    }
    if (!candidate) {
      showToast('暂未解析到无水印地址，正在等待页面结果刷新...');
      await new Promise((resolve) => setTimeout(resolve, 1800));
      videos = await extractCandidatesQuiet(null, 28000);
      candidate = pickCandidate(videos, target);
    }
    if (!candidate) throw new Error('暂未解析到无水印地址，请等视频卡片完全加载后重试');
    const result = await chrome.runtime.sendMessage({ type: 'download-video', video: safeDownloadPayload(candidate) });
    if (!result || !result.ok) throw new Error(result && result.error || '下载创建失败');
    showToast('无水印下载已开始', 'ok');
  }

  document.addEventListener('click', (event) => {
    const trigger = isDownloadTrigger(event.target);
    if (!trigger) return;
    if (event.defaultPrevented) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    downloadNoWatermarkFromTrigger(trigger).catch((error) => {
      showToast(error && error.message ? error.message : String(error), 'error');
      console.warn('[vmo-watermark-bridge]', error);
    });
  }, true);
}());
