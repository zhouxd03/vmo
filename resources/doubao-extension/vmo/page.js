(async () => {
  const __vmoDoubaoSource = true;
  const initialAuthOk = await globalThis.dbvdCheckAuthorized?.();

  const READY = '__pageServiceContentReady';
  const CHANNEL = 'page-service-channel';
  const WATERMARK_EXTRACTOR_CHANNEL = 'doubao-video-download-extractor';
  const STYLE_ID = 'page-service-style';
  const ROOT_ID = 'page-service-root';
  const CORE_SCRIPT_ID = 'page-service-core-script';
  const NETWORK_SCRIPT_ID = 'page-service-network-script';
  const WATERMARK_EXTRACTOR_SCRIPT_ID = 'page-service-watermark-extractor-script';
  const VIDEO_DOWNLOAD_OVERLAY_CLASS = 'dbvd-video-download-overlay';
  const HEARTBEAT_INTERVAL_MS = 5000;
  const TASK_STALE_MS = 60 * 1000;
  const DOUBAO_FIXED_VIDEO_DURATION_SECONDS = 15;
  const DOUBAO_VIDEO_DURATION_OPTIONS = [5, 10, 15];
  const DOUBAO_REQUIRE_NO_WATERMARK_DOWNLOAD = true;
  const DOUBAO_STATUS_SYNC_INTERVAL_MS = 2500;
  const VMO_IDLE_TASK_MESSAGE = '等待 VMO 豆包任务';
  const VMO_LOCAL_BRIDGE_WAIT_MESSAGE = '等待 VMO 本地服务重连，任务会自动恢复';
  const VMO_TASK_COMPLETED_MESSAGE = '豆包任务已完成';
  const DOUBAO_HARD_FAILURE_PATTERN = /(?:出于肖像保护考虑|肖像保护[^。；;\n]*(?:考虑|真实人脸|人脸素材|参考)|(?:暂不支持|不支持)[^。；;\n]*(?:真实人脸|人脸素材|人物肖像)[^。；;\n]*(?:参考|上传)?|(?:换(?:一)?张参考图|更换参考图)[^。；;\n]*(?:文生视频)?)[^。；;\n]*/i;
  const DOUBAO_STATUS_KEYWORDS = {
    creditCost: ['将消耗', '预计消耗', '本次消耗', '消耗', '扣除', '使用额度', '视频生成额度', 'credits', 'credit'],
    creditRemaining: ['今日剩余', '当前剩余', '剩余额度', '剩余积分', '账户余额', '余额', '可用额度'],
    progress: ['进度', '完成度', '已完成', '生成进度', '当前进度', '%'],
    queued: ['排队中', '队列中', '等待生成', '预计等待', '任务已创建', '已提交'],
    generating: ['正在为您生成', '正在生成', '生成中', '创作中', '视频生成中'],
    completed: ['视频生成好啦', '视频生成好了', '生成好啦', '生成好了', '生成完成', '视频已生成', '已生成视频', '创作完成', '下载视频', '保存视频'],
    failed: ['视频生成失败', '生成失败', '生成异常', '无法生成', '任务失败', '请求失败', '额度不足', '积分不足', '余额不足', '审核未通过', '内容不符合', '肖像保护', '真实人脸素材', '人脸素材作为参考', '换张参考图', '网络异常', '服务异常', '超时', '繁忙', '稍后重试', '登录已过期'],
  };
  const DOUBAO_STATUS_PATTERNS = {
    progress: [
      /(?:生成进度|完成进度|当前进度|进度|完成度|已完成)\s*[:：]?\s*(\d{1,3})\s*%/i,
      /(\d{1,3})\s*%\s*(?:完成|生成|进度)/i,
    ],
    wait: [
      /(?:预计等待|预计用时|预计耗时|预计还需|还需等待|剩余时间|排队预计|等待|预计)\s*[:：]?\s*([0-9.]+\s*(?:小时|时|分钟|分|秒|min|mins?|s|h|hrs?))/i,
      /(?:约|大约)\s*([0-9.]+\s*(?:小时|时|分钟|分|秒|min|mins?|s|h|hrs?))/i,
    ],
    creditCost: [
      /(?:将消耗|预计消耗|本次消耗|消耗|扣除|将扣除|使用|花费)\s*[:：]?\s*([0-9.]+)\s*(?:个|点|次)?\s*(?:视频生成)?(?:额度|积分|点数|credits?|credit)/i,
      /([0-9.]+)\s*(?:个|点|次)?\s*(?:视频生成)?(?:额度|积分|点数|credits?)\s*(?:将被消耗|已消耗|消耗|扣除)/i,
    ],
    creditRemaining: [
      /(?:今日剩余|当前剩余|剩余额度|剩余积分|账户余额|余额|剩余|可用)\s*[:：]?\s*([0-9.]+)\s*(?:个|点|次)?\s*(?:视频生成)?(?:额度|积分|点数|credits?|credit)/i,
      /([0-9.]+)\s*(?:个|点|次)?\s*(?:视频生成)?(?:额度|积分|点数|credits?)\s*(?:剩余|可用)/i,
    ],
    model: [
      /(?:使用|本次使用|模型)\s*[:：]?\s*([^，。,.；;]{2,50}?(?:模型|Seedance[^，。,.；;]{0,30}))\s*(?:生成|，|,|。|$)/i,
      /(Seedance\s*[^，。,.；;]{0,40}(?:模型)?)/i,
    ],
    failed: [
      DOUBAO_HARD_FAILURE_PATTERN,
      /(?:视频生成失败|生成失败|生成异常|无法生成|任务失败|请求失败|提交失败|下载失败|生成额度未扣除|额度未扣除|未扣除|额度不足|剩余额度不足|积分不足|余额不足|内容不符合|审核未通过|违规|敏感|风控|网络异常|服务异常|服务器异常|超时|繁忙|请稍后重试|稍后再试|出了点问题|遇到问题|登录已过期|请先登录|重新登录)[^。；;\n]*/i,
    ],
    completed: [
      /(?:你的视频生成好(?:啦|了)?(?!后|之后|以后)|视频生成好(?:啦|了)?(?!后|之后|以后)|生成好(?:啦|了)(?!后|之后|以后)|生成完成(?!后|之后|以后)|视频已生成(?!后|之后|以后)|已生成视频(?!后|之后|以后)|创作完成(?!后|之后|以后)|下载视频|保存视频)/i,
    ],
    generating: [
      /(?:正在为您生成|正在生成|生成中|创作中|视频生成中|排队中|队列中|等待生成|预计等待)/i,
    ],
  };
  let generationLoopBusy = false;
  let activeGenerationTaskId = '';
  let lastDoubaoStatusSyncAt = 0;
  let lastLlmDecisionAt = 0;
  let lastLlmDecisionKey = '';
  let lastLlmDecision = null;
  let lastWatermarkDiagnostic = null;
  let manualNoWatermarkDownloadBusy = false;
  let automationPanelState = null;
  const css = `
  #${ROOT_ID} {
    position: fixed;
    right: 18px;
    bottom: 18px;
    z-index: 2147483647;
    font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  #${ROOT_ID} .dbvd-fab {
    width: 56px;
    height: 48px;
    border: 0;
    border-radius: 999px;
    background: #1677ff;
    color: #fff;
    cursor: pointer;
    font-weight: 700;
    box-shadow: 0 10px 28px rgba(22, 119, 255, 0.36);
  }
  #${ROOT_ID} .dbvd-panel {
    position: absolute;
    right: 0;
    bottom: 60px;
    display: none;
    width: 380px;
    max-height: min(520px, calc(100vh - 96px));
    overflow: hidden;
    border: 1px solid rgba(15, 23, 42, 0.14);
    border-radius: 10px;
    background: #fff;
    color: #172033;
    box-shadow: 0 18px 48px rgba(15, 23, 42, 0.24);
  }
  #${ROOT_ID}[data-open="true"] .dbvd-panel { display: grid; grid-template-rows: auto 1fr; }
  #${ROOT_ID} .dbvd-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px; border-bottom: 1px solid #e5e8ef; }
  #${ROOT_ID} .dbvd-title { margin: 0; font-size: 15px; font-weight: 700; }
  #${ROOT_ID} .dbvd-action { height: 30px; padding: 0 10px; border: 1px solid #ccd3df; border-radius: 7px; background: #fff; color: #172033; cursor: pointer; }
  #${ROOT_ID} .dbvd-action:hover { border-color: #1677ff; color: #1677ff; }
  #${ROOT_ID} .dbvd-action:disabled { cursor: not-allowed; opacity: .62; }
  #${ROOT_ID} .dbvd-action-primary { border-color: #1677ff; background: #1677ff; color: #fff; }
  #${ROOT_ID} .dbvd-action-primary:hover { color: #fff; filter: brightness(.97); }
  #${ROOT_ID} .dbvd-body { display: grid; gap: 8px; min-height: 96px; max-height: 440px; overflow: auto; padding: 12px; }
  #${ROOT_ID} .dbvd-message { align-self: center; color: #667085; text-align: center; }
  #${ROOT_ID} .dbvd-error { color: #c24135; }
  #${ROOT_ID} .dbvd-item { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 10px; padding: 10px; border: 1px solid #e5e8ef; border-radius: 8px; background: #f8fafc; }
  #${ROOT_ID} .dbvd-name { overflow: hidden; font-size: 13px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
  #${ROOT_ID} .dbvd-meta { margin-top: 3px; color: #667085; font-size: 12px; }
  #${ROOT_ID} .dbvd-status-card { display: grid; gap: 10px; padding: 12px; border: 1px solid #dbe3f0; border-radius: 8px; background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%); }
  #${ROOT_ID} .dbvd-status-card[data-tone="ok"] { border-color: #9fd7b4; background: #f5fff8; }
  #${ROOT_ID} .dbvd-status-card[data-tone="warn"] { border-color: #f2ce8b; background: #fffaf0; }
  #${ROOT_ID} .dbvd-status-card[data-tone="bad"] { border-color: #f2aaa2; background: #fff7f6; }
  #${ROOT_ID} .dbvd-status-top { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 9px; min-width: 0; }
  #${ROOT_ID} .dbvd-status-dot { width: 9px; height: 9px; border-radius: 999px; background: #1677ff; box-shadow: 0 0 0 4px rgba(22, 119, 255, .12); }
  #${ROOT_ID} .dbvd-status-card[data-tone="ok"] .dbvd-status-dot { background: #18a058; box-shadow: 0 0 0 4px rgba(24, 160, 88, .12); }
  #${ROOT_ID} .dbvd-status-card[data-tone="warn"] .dbvd-status-dot { background: #d97706; box-shadow: 0 0 0 4px rgba(217, 119, 6, .13); }
  #${ROOT_ID} .dbvd-status-card[data-tone="bad"] .dbvd-status-dot { background: #d92d20; box-shadow: 0 0 0 4px rgba(217, 45, 32, .12); }
  #${ROOT_ID} .dbvd-task-name { overflow: hidden; color: #172033; font-size: 13px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
  #${ROOT_ID} .dbvd-task-stage { margin-top: 2px; color: #667085; font-size: 12px; }
  #${ROOT_ID} .dbvd-progress-number { color: #334155; font-size: 12px; font-weight: 700; }
  #${ROOT_ID} .dbvd-progress { height: 7px; overflow: hidden; border-radius: 999px; background: #e8eef7; }
  #${ROOT_ID} .dbvd-progress > span { display: block; height: 100%; width: 0%; border-radius: inherit; background: linear-gradient(90deg, #1677ff, #21b7ff); transition: width .25s ease; }
  #${ROOT_ID} .dbvd-status-card[data-tone="ok"] .dbvd-progress > span { background: linear-gradient(90deg, #18a058, #46c37b); }
  #${ROOT_ID} .dbvd-status-card[data-tone="warn"] .dbvd-progress > span { background: linear-gradient(90deg, #d97706, #f59e0b); }
  #${ROOT_ID} .dbvd-status-card[data-tone="bad"] .dbvd-progress > span { background: linear-gradient(90deg, #d92d20, #f04438); }
  #${ROOT_ID} .dbvd-status-message { color: #334155; font-size: 12px; line-height: 1.55; word-break: break-word; }
  #${ROOT_ID} .dbvd-meta-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
  #${ROOT_ID} .dbvd-meta-chip { min-width: 0; padding: 6px 8px; border-radius: 7px; background: rgba(15, 23, 42, .045); color: #475569; font-size: 11px; }
  #${ROOT_ID} .dbvd-meta-chip b { display: block; overflow: hidden; margin-top: 1px; color: #172033; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  #${ROOT_ID} .dbvd-step-list { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 4px; }
  #${ROOT_ID} .dbvd-step { height: 5px; border-radius: 999px; background: #dbe3f0; }
  #${ROOT_ID} .dbvd-step.done { background: #1677ff; }
  .${VIDEO_DOWNLOAD_OVERLAY_CLASS} {
    position: fixed;
    z-index: 2147483646;
    height: 32px;
    padding: 0 12px;
    border: 1px solid rgba(22, 119, 255, .45);
    border-radius: 7px;
    background: rgba(22, 119, 255, .96);
    color: #fff;
    cursor: pointer;
    font: 13px/30px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-weight: 700;
    box-shadow: 0 8px 22px rgba(15, 23, 42, .22);
    backdrop-filter: blur(4px);
  }
  .${VIDEO_DOWNLOAD_OVERLAY_CLASS}:hover { filter: brightness(.97); }
  .${VIDEO_DOWNLOAD_OVERLAY_CLASS}:disabled {
    cursor: wait;
    opacity: .72;
  }
  .dbvd-video-download-toast {
    position: fixed;
    z-index: 2147483646;
    max-width: min(360px, calc(100vw - 24px));
    padding: 8px 10px;
    border: 1px solid rgba(15, 23, 42, .12);
    border-radius: 7px;
    background: rgba(15, 23, 42, .92);
    color: #fff;
    font: 12px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    box-shadow: 0 8px 24px rgba(15, 23, 42, .22);
    pointer-events: none;
  }
  .dbvd-video-download-toast[data-tone="bad"] {
    background: rgba(185, 28, 28, .94);
  }
`;

  async function sendHeartbeat() {
    const payload = {
      url: location.href,
      title: document.title,
      userAgent: navigator.userAgent,
      accountId: getAssignedAccountId(),
      taskId: getAssignedTaskId(),
      durationCapability: inspectDoubaoDurationCapability(),
    };
    try {
      await directHeartbeat(payload);
      return;
    } catch (error) {
      // Fall back to the extension worker when direct local fetch is blocked.
    }
    try {
      const result = chrome.runtime.sendMessage({ type: 'dbvd-heartbeat', payload });
      if (result && typeof result.catch === 'function') result.catch(() => {});
    } catch (error) {
      // Keep the content script alive even if the worker is asleep or unavailable.
    }
  }

  async function directHeartbeat(payload) {
    const lockResponse = await fetch(chrome.runtime.getURL('launch-lock.json'), { cache: 'no-store' });
    const lock = await lockResponse.json();
    if (!lock || !lock.token || !lock.port || Date.now() > Number(lock.expiresAt || 0)) throw new Error('invalid launch lock');
    const response = await fetch(`http://127.0.0.1:${Number(lock.port)}/__dbvd_heartbeat?token=${encodeURIComponent(lock.token)}&rid=${encodeURIComponent(chrome.runtime.id)}&version=${encodeURIComponent(chrome.runtime.getManifest().version || '')}`, {
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


  async function readLaunchLock() {
    const lockResponse = await fetch(chrome.runtime.getURL('launch-lock.json'), { cache: 'no-store' });
    const lock = await lockResponse.json();
    if (!lock || !lock.token || !lock.port || Date.now() > Number(lock.expiresAt || 0)) throw new Error('invalid launch lock');
    return lock;
  }

  async function localTaskRequest(method, body, taskId) {
    const lock = await readLaunchLock();
    const accountId = getAssignedAccountId();
    const url = `http://127.0.0.1:${Number(lock.port)}/__dbvd_task?token=${encodeURIComponent(lock.token)}&rid=${encodeURIComponent(chrome.runtime.id)}${taskId ? `&taskId=${encodeURIComponent(taskId)}` : ''}${accountId ? `&accountId=${encodeURIComponent(accountId)}` : ''}`;
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

  async function getTaskStatus(taskId) {
    if (!taskId) return null;
    const lock = await readLaunchLock();
    const url = `http://127.0.0.1:${Number(lock.port)}/__dbvd_task_status?token=${encodeURIComponent(lock.token)}&rid=${encodeURIComponent(chrome.runtime.id)}&taskId=${encodeURIComponent(taskId)}`;
    const response = await fetch(url, { cache: 'no-store' });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result || !result.ok) throw new Error(result && result.error || 'task status failed');
    return result.task || null;
  }

  async function devtoolsClick(element, taskId = '') {
    if (!(element instanceof HTMLElement)) return false;
    if (typeof isLikelyConversationTitleOrHistoryTarget === 'function' && isLikelyConversationTitleOrHistoryTarget(element)) return false;
    try {
      const lock = await readLaunchLock();
      const rect = element.getBoundingClientRect();
      const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
      const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
      const hit = document.elementFromPoint(x, y);
      const target = typeof clickableTargetFor === 'function' ? clickableTargetFor(hit || element) : (hit || element);
      if (!target) return false;
      if (typeof isLikelyConversationTitleOrHistoryTarget === 'function' && isLikelyConversationTitleOrHistoryTarget(target)) return false;
      const response = await fetch(`http://127.0.0.1:${Number(lock.port)}/__dbvd_click?token=${encodeURIComponent(lock.token)}&rid=${encodeURIComponent(chrome.runtime.id)}`, {
        method: 'POST',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ taskId: taskId || getAssignedTaskId(), accountId: getAssignedAccountId(), x, y }),
      });
      const result = await response.json().catch(() => null);
      return Boolean(response.ok && result && result.ok);
    } catch (error) {
      return false;
    }
  }

  async function devtoolsRefreshTaskPage(taskId = '', reason = '') {
    try {
      const lock = await readLaunchLock();
      const response = await fetch(`http://127.0.0.1:${Number(lock.port)}/__dbvd_refresh?token=${encodeURIComponent(lock.token)}&rid=${encodeURIComponent(chrome.runtime.id)}`, {
        method: 'POST',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ taskId: taskId || getAssignedTaskId(), accountId: getAssignedAccountId(), reason }),
      });
      const result = await response.json().catch(() => null);
      return Boolean(response.ok && result && result.ok);
    } catch (error) {
      return false;
    }
  }

  function llmRecoveryEnabled(task = null) {
    return !(task && (task.doubaoLlmRecovery === false || task.decisionLlm === false));
  }

  function compactDecisionText(value, limit = 4200) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (text.length <= limit) return text;
    const head = Math.max(800, Math.floor(limit / 4));
    const tail = Math.max(1200, limit - head - 32);
    return `${text.slice(0, head)} ... ${text.slice(-tail)}`;
  }

  function decisionHash(value) {
    let hash = 2166136261;
    const text = String(value || '');
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16);
  }

  function collectAutomationSurfaceState() {
    let anyInput = null;
    let videoInput = null;
    let videoSurface = false;
    let imageMode = false;
    let titleModal = false;
    let inputContext = '';
    try {
      anyInput = findAnyPromptInput() || findPromptInput();
    } catch {}
    try {
      videoInput = findVideoPromptInput();
    } catch {}
    try {
      videoSurface = Boolean(isVideoCreationSurface());
    } catch {}
    try {
      imageMode = Boolean(anyInput && isImageModePromptInput(anyInput));
    } catch {}
    try {
      titleModal = Boolean(isConversationTitleEditModalOpen());
    } catch {}
    try {
      inputContext = anyInput ? elementTextContext(anyInput).slice(0, 500) : '';
    } catch {}
    return {
      href: location.href,
      videoSurface,
      hasVideoInput: Boolean(videoInput),
      hasAnyInput: Boolean(anyInput),
      imageMode,
      titleModal,
      inputContext,
    };
  }

  async function requestLlmAutomationDecision(task, stage = '', pageStatus = null, extra = {}) {
    if (!llmRecoveryEnabled(task)) return null;
    const text = compactDecisionText(statusSourceTextForTask(task, 4200), 4200);
    const surface = collectAutomationSurfaceState();
    const mergedPageStatus = { ...(pageStatus || {}), surface };
    const key = decisionHash(JSON.stringify({
      taskId: task && task.id || getAssignedTaskId() || '',
      stage,
      status: mergedPageStatus.status || '',
      text: text.slice(-1800),
      targetSeconds: taskTargetDurationSeconds(task || {}),
      decisionKey: extra && extra.decisionKey || '',
      surface: {
        videoSurface: surface.videoSurface,
        hasVideoInput: surface.hasVideoInput,
        imageMode: surface.imageMode,
        href: surface.href,
      },
    }));
    const now = Date.now();
    const hasCandidateContext = Array.isArray(extra && extra.candidateVideos) && extra.candidateVideos.length > 0;
    const minDecisionInterval = hasCandidateContext ? 2500 : 18000;
    if (lastLlmDecision && key === lastLlmDecisionKey && now - lastLlmDecisionAt < 45000) return lastLlmDecision;
    if (now - lastLlmDecisionAt < minDecisionInterval && key !== lastLlmDecisionKey) return null;
    lastLlmDecisionAt = now;
    lastLlmDecisionKey = key;
    try {
      const lock = await readLaunchLock();
      const response = await fetch(`http://127.0.0.1:${Number(lock.port)}/__dbvd_llm_decide?token=${encodeURIComponent(lock.token)}&rid=${encodeURIComponent(chrome.runtime.id)}`, {
        method: 'POST',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          taskId: task && task.id || getAssignedTaskId() || '',
          accountId: task && (task.accountId || task.currentAccountId) || getAssignedAccountId() || '',
          stage,
          taskStatus: task && task.status || '',
          targetSeconds: taskTargetDurationSeconds(task || {}),
          prompt: task && task.prompt || '',
          pageStatus: mergedPageStatus,
          candidateVideos: Array.isArray(extra && extra.candidateVideos) ? extra.candidateVideos : [],
          deterministicCandidate: extra && extra.deterministicCandidate || {},
          task: task ? {
            id: task.id,
            title: task.title || '',
            status: task.status || '',
            stage: task.stage || '',
            accountId: task.accountId || task.currentAccountId || '',
            prompt: task.prompt || '',
            doubaoTargetDuration: task.doubaoTargetDuration || task.targetDuration || task.duration || '',
            resultGuard: task.resultGuard || {},
            beforeVideoKeys: task.beforeVideoKeys || [],
            doubaoResultKey: task.doubaoResultKey || '',
            assetUrl: task.assetUrl || '',
            backupUrl: task.backupUrl || '',
          } : {},
          text,
          ...extra,
        }),
      });
      const result = await response.json().catch(() => null);
      const decision = result && result.ok ? result.decision || null : null;
      lastLlmDecision = decision;
      if (decision && task && task.id) {
        await updateTask({
          id: task.id,
          doubaoLlmDecision: decision,
          doubaoLlmDecisionAt: Date.now(),
          doubaoLlmAction: decision.action || '',
          doubaoAutomationDiagnostic: {
            reason: 'llm-recovery-decision',
            at: Date.now(),
            action: decision.action || '',
            source: decision.source || '',
            usedLlm: Boolean(decision.usedLlm),
            confidence: decision.confidence || 0,
            message: decision.reason || '',
          },
        }).catch(() => {});
      }
      return decision;
    } catch (error) {
      return null;
    }
  }

  async function sendAutomationReply(task, reply, stage = 'llm-reply-continue') {
    const text = String(reply || durationConfirmReply(task)).trim();
    if (!text) return false;
    const input = findAnyPromptInput() || findPromptInput();
    if (!input) throw new Error('豆包页面需要回复，但未找到可写输入框');
    showTaskToast(`自动回复：${text}`, task || automationPanelState?.task || null, {
      stage,
      progress: 78,
      tone: 'warn',
    });
    await setPromptValue(input, text, { allowChatReply: true });
    await waitForPromptValue(input, text, { allowChatReply: true });
    await submitDurationConfirmReply(input);
    await sleep(1200);
    return true;
  }

  async function applyLlmAutomationDecision(task, decision, context = {}) {
    if (!decision || !decision.action || decision.action === 'no_action') return '';
    const action = String(decision.action || '');
    const reason = String(decision.reason || '');
    const pageStatus = context.pageStatus || null;
    if (action === 'wait_generation') {
      setAutomationProgress(task, 'llm-wait-generation', reason || 'LLM 判断豆包仍在生成，继续等待', {
        status: task && task.status || 'submitted',
        progress: pageStatus && pageStatus.progress || progressForStage('waiting-result', task && task.status || 'submitted'),
        tone: 'warn',
      });
      return '';
    }
    if (action === 'reply_continue') {
      await sendAutomationReply(task, decision.reply || durationConfirmReply(task), 'llm-reply-continue');
      return 'reply';
    }
    if (action === 'reply_correction') {
      const reply = decision.reply || `请直接生成${taskTargetDurationSeconds(task || {})}秒视频，不要输出提示词或文案。`;
      await sendAutomationReply(task, reply, 'llm-reply-correction');
      return 'reply';
    }
    if (action === 'redirect_video_surface') {
      setAutomationProgress(task, 'llm-redirect-video-surface', reason || 'LLM 判断当前不在视频生成流程，正在切回视频生成页', {
        status: task && task.status || 'submitted',
        progress: 42,
        tone: 'warn',
      });
      await ensureVideoCreationSurface();
      await assertVideoSurfaceForSubmit(task || { id: getAssignedTaskId() }, 'llm-redirect-video-surface');
      return 'redirect';
    }
    if (action === 'activate_result') {
      await updateTask({ id: task.id, status: 'submitted', stage: 'llm-result-detected', doubaoPageMessage: reason || 'LLM 判断页面已有视频结果，正在抓取' }).catch(() => {});
      setAutomationProgress(task, 'llm-result-detected', reason || 'LLM 判断页面已有视频结果，正在抓取', {
        status: 'submitted',
        progress: 94,
      });
      await activateLatestResultCard();
      return 'activate';
    }
    if (action === 'refresh_result') {
      const delay = Number(decision.retryDelayMs || 0);
      await updateTask({ id: task.id, status: 'submitted', stage: 'llm-refresh-result-page', doubaoRefreshReason: 'llm-recovery', doubaoPageMessage: reason || 'LLM 建议刷新页面恢复抓取' }).catch(() => {});
      setAutomationProgress(task, 'llm-refresh-result-page', reason || 'LLM 建议刷新页面恢复抓取', {
        status: 'submitted',
        progress: 88,
        tone: 'warn',
      });
      if (delay) await sleep(Math.min(delay, 120000));
      if (!(await devtoolsRefreshTaskPage(task.id, 'llm-recovery'))) location.reload();
      await sleep(8000);
      return 'refresh';
    }
    if (action === 'resubmit_prompt') {
      if (hasActiveDoubaoGenerationSignal(pageStatus)) return '';
      await updateTask({ id: task.id, status: 'submitted', stage: 'llm-resubmit-prompt', doubaoPageMessage: reason || 'LLM 建议重新提交提示词' }).catch(() => {});
      await resubmitGenerationTask(task, Number(context.beforeFailureCount || 0));
      return 'resubmit';
    }
    if (action === 'fail_hard') {
      throw new Error(reason || 'LLM 判断豆包页面当前状态不可自动恢复');
    }
    return '';
  }

  async function reportAutomationDiagnostic(taskId, reason, extra = {}) {
    const id = taskId || getAssignedTaskId();
    if (!id) return null;
    try {
      return await updateTask({
        id,
        stage: extra.stage || 'automation-diagnostic',
        doubaoAutomationDiagnostic: {
          reason: String(reason || ''),
          at: Date.now(),
          ...extra,
        },
      });
    } catch (error) {
      return null;
    }
  }

  async function claimTask(taskId) {
    try { return await localTaskRequest('GET', null, taskId); }
    catch (error) {
      const response = await chrome.runtime.sendMessage({ type: 'claim-generation-task', taskId, accountId: getAssignedAccountId() });
      if (!response || !response.ok) throw new Error(response && response.error || error.message || 'claim task failed');
      return response.task || null;
    }
  }

  async function updateTask(task) {
    if (task && task.id && !task.automationRunnerId) task.automationRunnerId = chrome.runtime.id;
    if (task && task.id && !task.accountId) task.accountId = getAssignedAccountId();
    try {
      const updated = await localTaskRequest('POST', task || {});
      if (updated && updated.id) updateAutomationPanel({
        task: updated,
        status: updated.status,
        stage: updated.stage,
        progress: updated.doubaoProgress,
        message: updated.doubaoPageMessage || updated.error || stageLabel(updated.stage, updated.status),
      });
      return updated;
    }
    catch (error) {
      const response = await chrome.runtime.sendMessage({ type: 'update-generation-task', task });
      if (!response || !response.ok) throw new Error(response && response.error || error.message || 'update task failed');
      const updated = response.task || null;
      if (updated && updated.id) updateAutomationPanel({
        task: updated,
        status: updated.status,
        stage: updated.stage,
        progress: updated.doubaoProgress,
        message: updated.doubaoPageMessage || updated.error || stageLabel(updated.stage, updated.status),
      });
      return updated;
    }
  }

  async function getTaskImageUrl(taskId, index = 0) {
    const lock = await readLaunchLock();
    return `http://127.0.0.1:${Number(lock.port)}/__dbvd_file?token=${encodeURIComponent(lock.token)}&rid=${encodeURIComponent(chrome.runtime.id)}&taskId=${encodeURIComponent(taskId)}&index=${encodeURIComponent(index)}`;
  }

  ensureNetworkCaptureScript();
  ensureWatermarkExtractorScript();
  window.addEventListener('message', (event) => {
    const data = event.data;
    if (data && data.source === CHANNEL && data.type === 'network-candidates-updated') {
      const taskId = getAssignedTaskId();
      if (taskId) {
        updateTask({ id: taskId, stage: 'network-result-captured', doubaoAutomationDiagnostic: { reason: 'network-result-captured', at: Date.now(), count: Array.isArray(data.videos) ? data.videos.length : 0 } }).catch(() => {});
      }
    }
  });

  sendHeartbeat();
  setInterval(sendHeartbeat, HEARTBEAT_INTERVAL_MS);

  function showMessage(body, text, isError = false) {
    const item = document.createElement('div');
    item.className = isError ? 'dbvd-message dbvd-error' : 'dbvd-message';
    item.textContent = text;
    body.replaceChildren(item);
  }

  function clampProgress(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 0;
    return Math.max(0, Math.min(100, Math.round(number)));
  }

  function readDurationNumber(value) {
    if (value === null || value === undefined || value === '') return 0;
    if (typeof value === 'number') return Number.isFinite(value) && value > 0 ? value : 0;
    const match = String(value).match(/\d+(?:\.\d+)?/);
    const number = match ? Number(match[0]) : 0;
    return Number.isFinite(number) && number > 0 ? number : 0;
  }

  function selectDoubaoDuration(seconds) {
    const value = readDurationNumber(seconds);
    if (!value) return DOUBAO_FIXED_VIDEO_DURATION_SECONDS;
    if (value > 10) return 15;
    if (value > 5) return 10;
    return 5;
  }

  function promptDurationSeconds(prompt = '') {
    const text = normalizePromptText(prompt);
    const labelled = text.match(/(?:总时长|视频时长|生成时长|时长|duration)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:秒|s|sec|seconds?)?/i);
    if (labelled) return readDurationNumber(labelled[1]);
    return 0;
  }

  function taskSourceDurationSeconds(task = {}) {
    for (const key of ['sourceDuration', 'requestedDuration', 'storyboardDuration', 'shotDuration', 'originalDuration', 'duration']) {
      const value = readDurationNumber(task && task[key]);
      if (value) return value;
    }
    return promptDurationSeconds(task && task.prompt || '');
  }

  function taskTargetDurationSeconds(task = {}) {
    for (const key of ['doubaoTargetDuration', 'targetDuration', 'selectedDuration']) {
      const value = readDurationNumber(task && task[key]);
      if (DOUBAO_VIDEO_DURATION_OPTIONS.includes(Math.round(value))) return Math.round(value);
    }
    return selectDoubaoDuration(taskSourceDurationSeconds(task));
  }

  function durationConfirmReply(task = null) {
    return `是，生成${taskTargetDurationSeconds(task || automationPanelState?.task || {})}秒视频`;
  }

  function progressForStage(stage = '', status = '') {
    const key = String(stage || status || '').trim();
    const map = {
      queued: 5,
      claimed: 10,
      'surface-ready': 18,
      uploading: 28,
      'after-upload': 38,
      prompting: 46,
      'before-prompt': 48,
      prepared: 58,
      'before-submit': 62,
      submitted: 68,
      'waiting-result': 72,
      'doubao-queued': 74,
      'doubao-generating': 80,
      'doubao-duration-confirm': 78,
      'doubao-failure-signal': 100,
      'completed-card-detected': 92,
      'doubao-completed': 94,
      'result-candidate-confirming': 95,
      'refreshing-result-page': 88,
      'download-retry-waiting': 90,
      downloading: 97,
      downloaded: 100,
      completed: 100,
      manual: 100,
      'semi-auto-ready': 100,
      failed: 100,
      cancelled: 100,
      idle: 0,
    };
    return map[key] ?? map[String(status || '').trim()] ?? 0;
  }

  function stageLabel(stage = '', status = '') {
    const key = String(stage || status || '').trim();
    const map = {
      queued: '等待领取任务',
      claimed: '已领取任务',
      'surface-ready': '确认视频生成页面',
      uploading: '上传参考图',
      'after-upload': '参考图已就绪',
      prompting: '写入视频提示词',
      'before-prompt': '准备写入提示词',
      prepared: '提示词与图片已准备',
      'before-submit': '准备提交生成',
      submitted: '已提交，等待豆包生成',
      'waiting-result': '轮询生成结果',
      'doubao-queued': '豆包排队中',
      'doubao-generating': '豆包生成中',
      'doubao-duration-confirm': '确认生成时长',
      'doubao-failure-signal': '检测到失败提示',
      'completed-card-detected': '检测到完成卡片',
      'doubao-completed': '正在绑定结果',
      'result-candidate-confirming': '确认视频归属',
      'refreshing-result-page': '刷新页面读取结果',
      'download-retry-waiting': '下载重试等待',
      downloading: '下载视频',
      downloaded: '下载完成',
      completed: '任务完成',
      manual: '等待手动操作',
      'semi-auto-ready': '半自动已填入',
      failed: '任务失败',
      cancelled: '任务已停止',
      idle: '等待任务',
    };
    return map[key] || map[String(status || '').trim()] || key || '等待任务';
  }

  function taskTitle(task = {}) {
    return String(
      task.shotNo || task.shot_no || task.filenamePrefix || task.traceKey || task.title || task.id || '豆包视频任务',
    );
  }

  function isSemiAutoTask(task = {}) {
    const mode = String(task?.doubaoAutomationMode || task?.automationMode || task?.automation_mode || '').trim().toLowerCase();
    return ['semi', 'semi-auto', 'semiauto', 'manual', 'assist'].includes(mode);
  }

  function taskMeta(task = {}) {
    const imageCount = Number(task.imageCount || (Array.isArray(task.imagePaths) ? task.imagePaths.length : 0) || (task.imagePath ? 1 : 0));
    const sourceDuration = taskSourceDurationSeconds(task);
    const targetDuration = taskTargetDurationSeconds(task);
    const durationText = sourceDuration && sourceDuration !== targetDuration ? `${sourceDuration}→${targetDuration}秒` : `${targetDuration}秒`;
    const rows = [
      ['镜头', task.shotNo || task.shot_no || task.filenamePrefix || '-'],
      ['账号', task.accountId || task.currentAccountId || '-'],
      ['参考图', imageCount ? `${imageCount} 张` : '无'],
      ['规格', `${durationText} / ${task.ratio || '-'}`],
    ];
    const decision = task.doubaoLlmDecision || {};
    const action = task.doubaoLlmAction || decision.action || '';
    if (action) rows.push(['介入', `${decision.usedLlm ? 'LLM' : '规则'} · ${action}`]);
    if (task.doubaoWatermarkResolved || task.noWatermarkUrl) rows.push(['水印', '无水印候选']);
    else if (task.doubaoWatermarkDiagnostic && task.doubaoWatermarkDiagnostic.reason) rows.push(['水印', `解析中：${task.doubaoWatermarkDiagnostic.reason}`]);
    return rows;
  }

  function panelTone(status = '', stage = '') {
    const value = `${status} ${stage}`.toLowerCase();
    if (/failed|cancelled|error|failure/.test(value)) return 'bad';
    if (/downloaded|completed/.test(value)) return 'ok';
    if (/refresh|retry|waiting|queued|duration/.test(value)) return 'warn';
    return 'active';
  }

  function renderAutomationPanel(body, state = {}) {
    const task = state.task || {};
    const status = String(state.status || task.status || (state.idle ? 'idle' : '') || '');
    const stage = String(state.stage || task.stage || status || '');
    const progress = clampProgress(
      state.progress ?? task.doubaoProgress ?? progressForStage(stage, status),
    );
    const tone = state.tone || panelTone(status, stage);
    const message = String(state.message || task.doubaoPageMessage || task.error || stageLabel(stage, status));
    const card = document.createElement('div');
    card.className = 'dbvd-status-card';
    card.dataset.tone = tone;

    const top = document.createElement('div');
    top.className = 'dbvd-status-top';
    const dot = document.createElement('span');
    dot.className = 'dbvd-status-dot';
    const main = document.createElement('div');
    main.style.minWidth = '0';
    const name = document.createElement('div');
    name.className = 'dbvd-task-name';
    name.textContent = state.idle ? 'VMO 豆包任务助手' : taskTitle(task);
    const stageText = document.createElement('div');
    stageText.className = 'dbvd-task-stage';
    stageText.textContent = stageLabel(stage, status);
    main.append(name, stageText);
    const percent = document.createElement('div');
    percent.className = 'dbvd-progress-number';
    percent.textContent = `${progress}%`;
    top.append(dot, main, percent);

    const bar = document.createElement('div');
    bar.className = 'dbvd-progress';
    const fill = document.createElement('span');
    fill.style.width = `${progress}%`;
    bar.append(fill);

    const steps = document.createElement('div');
    steps.className = 'dbvd-step-list';
    [15, 35, 55, 70, 95].forEach((limit) => {
      const step = document.createElement('span');
      step.className = `dbvd-step${progress >= limit ? ' done' : ''}`;
      steps.append(step);
    });

    const msg = document.createElement('div');
    msg.className = 'dbvd-status-message';
    msg.textContent = message;

    card.append(top, bar, steps, msg);
    if (!state.idle) {
      const meta = document.createElement('div');
      meta.className = 'dbvd-meta-grid';
      taskMeta(task).forEach(([label, value]) => {
        const chip = document.createElement('div');
        chip.className = 'dbvd-meta-chip';
        chip.textContent = label;
        const bold = document.createElement('b');
        bold.textContent = String(value || '-');
        chip.append(bold);
        meta.append(chip);
      });
      card.append(meta);
    }
    body.replaceChildren(card);
  }

  function updateAutomationPanel(next = {}) {
    const root = document.getElementById(ROOT_ID);
    if (!root) return;
    const patch = typeof next === 'string' ? { message: next } : { ...(next || {}) };
    const previous = automationPanelState || {};
    automationPanelState = {
      ...previous,
      ...patch,
      task: Object.prototype.hasOwnProperty.call(patch, 'task') ? patch.task : previous.task || null,
      updatedAt: Date.now(),
    };
    if (patch.open) root.dataset.open = 'true';
    const body = root.querySelector('.dbvd-body');
    if (body) renderAutomationPanel(body, automationPanelState);
  }

  function setAutomationProgress(task, stage, message = '', patch = {}) {
    const status = patch.status || (task && task.status) || stage || '';
    updateAutomationPanel({
      task: task || automationPanelState?.task || null,
      status,
      stage,
      progress: progressForStage(stage, status),
      message: message || stageLabel(stage, status),
      open: true,
      ...(patch || {}),
    });
  }

  function showIdleTaskMessage() {
    updateAutomationPanel({
      idle: true,
      task: null,
      status: 'idle',
      stage: 'idle',
      progress: 0,
      tone: 'active',
      message: VMO_IDLE_TASK_MESSAGE,
    });
  }

  async function requestExtract(task = null) {
    const requestId = crypto.randomUUID();
    const networkVideos = await requestNetworkCandidates(900, task).catch(() => []);
    const domVideos = extractVisibleResultVideos();
    await waitForCoreScriptReady();
    await waitForWatermarkExtractorReady().catch(() => {});
    return new Promise((resolve, reject) => {
      let fallbackTimer = null;
      let lastExtractError = '';
      const cleanup = () => {
        window.removeEventListener('message', listener);
        clearTimeout(timer);
        if (fallbackTimer) clearTimeout(fallbackTimer);
      };
      const finishWithFallback = () => {
        const fallbackVideos = extractVisibleResultVideos();
        const mergedVideos = mergeExtractedVideos(networkVideos, domVideos, fallbackVideos);
        if (mergedVideos.length) {
          cleanup();
          resolve(mergedVideos);
          return;
        }
        cleanup();
        fallbackVideos.length ? resolve(fallbackVideos) : reject(new Error(lastExtractError || 'extract timeout'));
      };
      const scheduleFallback = (message = '') => {
        lastExtractError = message || lastExtractError;
        if (!fallbackTimer) fallbackTimer = setTimeout(finishWithFallback, 1800);
      };
      const timer = setTimeout(() => {
        finishWithFallback();
        return;
        window.removeEventListener('message', listener);
        const fallbackVideos = extractVisibleResultVideos();
        const mergedVideos = mergeExtractedVideos(networkVideos, domVideos, fallbackVideos);
        if (mergedVideos.length) {
          resolve(mergedVideos);
          return;
        }
        fallbackVideos.length ? resolve(fallbackVideos) : reject(new Error('读取豆包结果超时'));
      }, 15000);
      const listener = (event) => {
        const data = event.data;
        if (data && data.source === CHANNEL && data.type === 'extract-result' && data.requestId === requestId) {
          window.removeEventListener('message', listener);
          clearTimeout(timer);
          if (data.ok) {
            const videos = Array.isArray(data.videos) ? data.videos : [];
            resolve(mergeExtractedVideos(networkVideos, domVideos, videos, extractVisibleResultVideos()));
            return;
          } else {
            window.addEventListener('message', listener);
            scheduleFallback(data.error || 'Page parse failed');
            return;
            const fallbackVideos = extractVisibleResultVideos();
            const mergedVideos = mergeExtractedVideos(networkVideos, domVideos, fallbackVideos);
            if (mergedVideos.length) {
              resolve(mergedVideos);
              return;
            }
            fallbackVideos.length ? resolve(fallbackVideos) : reject(new Error(data.error || 'Page parse failed'));
          }
        }
      };
      window.addEventListener('message', listener);
      window.postMessage({ source: CHANNEL, type: 'extract', requestId }, '*');
    });
  }

  function watermarkDiagnostic(reason, patch = {}) {
    const diagnostic = {
      reason,
      at: Date.now(),
      scriptLoaded: document.getElementById(WATERMARK_EXTRACTOR_SCRIPT_ID)?.dataset?.loaded || '',
      hasRouterData: Boolean(window.__MODERN_ROUTER_DATA),
      ...patch,
    };
    lastWatermarkDiagnostic = diagnostic;
    return diagnostic;
  }

  async function requestWatermarkCandidates(timeoutMs = 14000, options = {}) {
    const diagnosticOnly = Boolean(options && options.diagnostic);
    const ready = await waitForWatermarkExtractorReady().catch((error) => {
      watermarkDiagnostic('inject-error', { error: String(error && error.message || error) });
      return false;
    });
    if (ready === false && diagnosticOnly) {
      return { videos: [], diagnostic: lastWatermarkDiagnostic || watermarkDiagnostic('inject-error') };
    }
    const requestId = crypto.randomUUID();
    const target = watermarkRequestTarget(options && options.target);
    return await new Promise((resolve) => {
      const timer = setTimeout(() => {
        window.removeEventListener('message', listener);
        const diagnostic = watermarkDiagnostic('timeout', { timeoutMs });
        resolve(diagnosticOnly ? { videos: [], diagnostic } : []);
      }, timeoutMs);
      const listener = (event) => {
        const data = event.data;
        if (data && data.source === WATERMARK_EXTRACTOR_CHANNEL && data.type === 'extract-result' && data.requestId === requestId) {
          window.removeEventListener('message', listener);
          clearTimeout(timer);
          const videos = data.ok && Array.isArray(data.videos) ? data.videos : [];
          const normalizedVideos = videos.map((video) => normalizeExtractedVideoCandidate(video)).filter(Boolean);
          const noWatermarkCount = normalizedVideos.filter((video) => isWatermarkResolvedCandidate(video)).length;
          const diagnostic = watermarkDiagnostic(data.ok ? (noWatermarkCount ? 'resolved' : 'no-candidate') : 'extract-error', {
            ok: Boolean(data.ok),
            count: videos.length,
            noWatermarkCount,
            error: data.ok ? '' : String(data.error || ''),
            firstSource: String(videos[0] && videos[0].source || ''),
            firstWatermarkSource: String(videos[0] && videos[0].doubaoWatermarkSource || ''),
            extractorReason: String(data.reason || data.diagnostic && data.diagnostic.reason || ''),
          });
          resolve(diagnosticOnly ? { videos: normalizedVideos, diagnostic } : normalizedVideos);
        }
      };
      window.addEventListener('message', listener);
      window.postMessage({ source: WATERMARK_EXTRACTOR_CHANNEL, type: 'extract', requestId, target }, '*');
    });
  }

  async function requestCoreExtract(timeoutMs = 15000) {
    await waitForCoreScriptReady();
    const requestId = crypto.randomUUID();
    return await new Promise((resolve) => {
      const timer = setTimeout(() => {
        window.removeEventListener('message', listener);
        resolve([]);
      }, timeoutMs);
      const listener = (event) => {
        const data = event.data;
        if (data && data.source === CHANNEL && data.type === 'extract-result' && data.requestId === requestId) {
          window.removeEventListener('message', listener);
          clearTimeout(timer);
          resolve(data.ok && Array.isArray(data.videos) ? data.videos : []);
        }
      };
      window.addEventListener('message', listener);
      window.postMessage({ source: CHANNEL, type: 'extract', requestId }, '*');
    });
  }

  async function requestExtract(task = null) {
    const networkVideos = await requestNetworkCandidates(900, task).catch(() => []);
    const domVideos = extractVisibleResultVideos();
    const [coreVideos, watermarkVideos] = await Promise.all([
      requestCoreExtract(15000).catch(() => []),
      requestWatermarkCandidates(14000).catch(() => []),
    ]);
    const fallbackVideos = extractVisibleResultVideos();
    const mergedVideos = mergeExtractedVideos(networkVideos, domVideos, coreVideos, watermarkVideos, fallbackVideos);
    if (mergedVideos.length) return mergedVideos;
    if (fallbackVideos.length) return fallbackVideos;
    throw new Error('extract timeout');
  }

  function extractVisibleResultVideos() {
    const doneText = getPageTextWithoutPanel();
    const hasDoneMarker = hasCompletedStatusSignal(doneText);
    const candidates = [];
    const add = (node, url, source, requireUrl = true) => {
      const cleanUrl = normalizeVideoUrl(url);
      if (requireUrl && !cleanUrl) return;
      const root = closestResultCard(node);
      const rect = (root || node).getBoundingClientRect?.() || node.getBoundingClientRect?.();
      const cardText = String((root || node).innerText || (root || node).textContent || '').replace(/\s+/g, ' ').trim();
      const seedVid = cleanUrl ? extractVideoId(cleanUrl) : '';
      const internal = extractDoubaoInternalIds(root || node, { assetUrl: cleanUrl, backupUrl: cleanUrl, vid: seedVid });
      const assetUrl = cleanUrl || internal.assetUrl || internal.backupUrl || '';
      const backupUrl = internal.backupUrl || (internal.assetUrl && internal.assetUrl !== assetUrl ? internal.assetUrl : '') || assetUrl;
      const fallbackVid = assetUrl ? extractVideoId(assetUrl) : seedVid;
      const candidate = {
        vid: internal.doubaoInternalVideoId || fallbackVid,
        messageId: internal.doubaoInternalMessageId || extractMessageId(root || node) || '',
        assetUrl,
        backupUrl,
        title: document.title || 'doubao-video',
        width: Math.round(rect?.width || 0),
        height: Math.round(rect?.height || 0),
        rectLeft: Math.round(rect?.left || 0),
        rectRight: Math.round(rect?.right || 0),
        rectTop: Math.round(rect?.top || 0),
        rectBottom: Math.round(rect?.bottom || 0),
        extractedAt: Date.now(),
        cardText: cardText.slice(0, 500),
        source,
        score: scoreResultCandidate(root || node, hasDoneMarker),
        overlayTargetKey: overlayTargetKey(node, root || node, rect),
        doubaoInternalTaskId: internal.doubaoInternalTaskId || '',
        doubaoInternalMessageId: internal.doubaoInternalMessageId || '',
        doubaoInternalVideoId: internal.doubaoInternalVideoId || '',
        doubaoResultMeta: internal.doubaoResultMeta || {},
      };
      candidate.doubaoResultKey = internal.doubaoResultKey || buildDoubaoResultKey(candidate);
      candidate.doubaoResultMeta = normalizeDoubaoResultMeta({
        ...(candidate.doubaoResultMeta || {}),
        resultKeys: videoIdentityKeys(candidate),
        source,
      });
      candidates.push(candidate);
    };
    const addMarker = (node, source) => add(node, '', source, false);
    for (const video of [...document.querySelectorAll('video')]) {
      if (!isResultMediaVisible(video)) continue;
      addMarker(video, 'dom-video-marker');
      add(video, video.currentSrc || video.src || video.getAttribute('src') || video.getAttribute('data-src') || '', 'dom-video');
      for (const source of [...video.querySelectorAll('source')]) {
        add(video, source.src || source.getAttribute('src') || '', 'dom-video-source');
      }
    }
    for (const link of [...document.querySelectorAll('a[href]')]) {
      if (!isResultMediaVisible(link)) continue;
      const href = link.href || link.getAttribute('href') || '';
      if (isLikelyDirectVideoUrl(href)) add(link, href, 'dom-link');
    }
    for (const item of [...document.querySelectorAll('[src],[data-src],[data-url],[data-video-url],[data-download-url]')]) {
      if (!isResultMediaVisible(item)) continue;
      for (const name of ['src', 'data-src', 'data-url', 'data-video-url', 'data-download-url']) {
        const value = item.getAttribute(name) || '';
        if (isLikelyDirectVideoUrl(value)) add(item, value, `dom-${name}`);
      }
    }
    const performanceAnchor = findLatestCompletedResultCard();
    if (performanceAnchor) {
      for (const url of readPerformanceVideoUrls()) {
        add(performanceAnchor, url, 'performance-resource');
      }
    }
    return [...new Map(candidates
      .filter((item) => (item.assetUrl || item.messageId || item.vid || item.doubaoResultKey) && item.score > -1000)
      .sort((a, b) => b.score - a.score)
      .map((item) => [videoKey(item), item])).values()];
  }

  function readPerformanceVideoUrls() {
    try {
      return [...new Set(performance.getEntriesByType('resource')
        .slice(-1000)
        .map((entry) => String(entry.name || ''))
        .filter((url) => isLikelyVideoResourceUrl(url)))].slice(-60);
    } catch (error) {
      return [];
    }
  }

  function findLatestCompletedResultCard() {
    const candidates = [...document.querySelectorAll('div, section, article, li')]
      .filter((item) => item instanceof HTMLElement && item.offsetParent !== null && !item.closest(`#${ROOT_ID}`))
      .filter((item) => hasCompletedStatusSignal(String(item.innerText || item.textContent || '')))
      .map((item) => {
        const rect = item.getBoundingClientRect();
        return { item, rect, score: rect.top + rect.height + Math.min(rect.width * rect.height / 5000, 800) };
      })
      .filter((entry) => entry.rect.width >= 180 && entry.rect.height >= 80)
      .sort((a, b) => b.score - a.score);
    return candidates[0]?.item || null;
  }

  async function activateLatestResultCard() {
    const card = findLatestCompletedResultCard();
    if (!card) return false;
    card.scrollIntoView?.({ block: 'center', inline: 'center' });
    await sleep(500);
    const target = [...card.querySelectorAll('video, button, [role="button"], [aria-label], [title], img, canvas, div, span')]
      .filter((item) => item instanceof HTMLElement && item.offsetParent !== null && !item.closest(`#${ROOT_ID}`))
      .map((item) => {
        const rect = item.getBoundingClientRect();
        const text = String(item.innerText || item.textContent || item.getAttribute('aria-label') || item.getAttribute('title') || '');
        let score = rect.width * rect.height / 1000;
        if (/播放|play|预览|查看|下载|保存/i.test(text)) score += 2000;
        if (item.tagName === 'VIDEO') score += 1600;
        if (rect.width >= 160 && rect.height >= 90) score += 800;
        return { item, score };
      })
      .sort((a, b) => b.score - a.score)[0]?.item;
    if (target) {
      try {
        if (target.tagName === 'VIDEO') {
          target.muted = true;
          await target.play?.().catch(() => {});
        } else {
          clickElement(target);
        }
      } catch (error) {}
    }
    await sleep(1200);
    return true;
  }

  async function activateTriggeredResultCard(trigger = {}) {
    const rect = trigger && trigger.rect;
    if (!rect || !Number.isFinite(Number(rect.left)) || !Number.isFinite(Number(rect.top))) return false;
    const centerX = Math.max(0, Math.min(window.innerWidth - 1, (Number(rect.left) + Number(rect.right || rect.left)) / 2));
    const centerY = Math.max(0, Math.min(window.innerHeight - 1, (Number(rect.top) + Number(rect.bottom || rect.top)) / 2));
    const hit = document.elementFromPoint(centerX, centerY);
    const card = closestResultCard(hit instanceof HTMLElement ? hit : (trigger.element instanceof HTMLElement ? trigger.element : null));
    const candidates = [];
    const addCandidate = (item, score = 0) => {
      if (!(item instanceof HTMLElement) || item.offsetParent === null || item.closest(`#${ROOT_ID}`)) return;
      if (isLikelyConversationTitleOrHistoryTarget(item)) return;
      const itemRect = item.getBoundingClientRect();
      if (!itemRect || itemRect.width < 8 || itemRect.height < 8) return;
      candidates.push({ item, score: score + scoreElementAgainstRect(itemRect, rect) });
    };
    if (card && card.querySelectorAll) {
      [...card.querySelectorAll('video, canvas, img, button, [role="button"], [aria-label], [title]')].forEach((item) => {
        const text = String(item.innerText || item.textContent || item.getAttribute?.('aria-label') || item.getAttribute?.('title') || '');
        let score = 0;
        if (/播放|play|预览|查看|下载|保存/i.test(text)) score += 1400;
        if (item.tagName === 'VIDEO') score += 1600;
        if (item.tagName === 'CANVAS' || item.tagName === 'IMG') score += 900;
        addCandidate(item, score);
      });
    }
    addCandidate(hit instanceof HTMLElement ? hit : null, 600);
    const target = candidates.sort((a, b) => b.score - a.score)[0]?.item;
    if (!target) return false;
    target.scrollIntoView?.({ block: 'center', inline: 'center' });
    await sleep(150);
    try {
      if (target.tagName === 'VIDEO') {
        target.muted = true;
        await target.play?.().catch(() => {});
      } else {
        clickElement(target);
      }
    } catch (error) {}
    await sleep(1100);
    return true;
  }

  function scoreElementAgainstRect(itemRect, targetRect) {
    const target = {
      left: Number(targetRect.left || 0),
      top: Number(targetRect.top || 0),
      right: Number(targetRect.right || targetRect.left || 0),
      bottom: Number(targetRect.bottom || targetRect.top || 0),
    };
    const overlapX = Math.max(0, Math.min(itemRect.right, target.right) - Math.max(itemRect.left, target.left));
    const overlapY = Math.max(0, Math.min(itemRect.bottom, target.bottom) - Math.max(itemRect.top, target.top));
    const overlapArea = overlapX * overlapY;
    const minArea = Math.max(1, Math.min(itemRect.width * itemRect.height, Math.max(1, (target.right - target.left) * (target.bottom - target.top))));
    const centerDistance = Math.hypot((itemRect.left + itemRect.right - target.left - target.right) / 2, (itemRect.top + itemRect.bottom - target.top - target.bottom) / 2);
    return Math.round((overlapArea / minArea) * 2400 + Math.max(0, 1200 - centerDistance));
  }

  function mergeExtractedVideos(...groups) {
    const merged = [];
    const index = new Map();
    const keysFor = (item) => videoIdentityKeys(item);
    for (const rawItem of groups.flat().filter(Boolean)) {
      const item = normalizeExtractedVideoCandidate(rawItem);
      const keys = keysFor(item);
      let target = keys.map((key) => index.get(key)).find(Boolean);
      if (!target) {
        target = { ...item, extractionIndex: merged.length };
        merged.push(target);
      } else {
        mergeVideoCandidate(target, item);
      }
      keysFor(target).forEach((key) => index.set(key, target));
      keys.forEach((key) => index.set(key, target));
    }
    return merged.filter((item) => hasUsableVideoUrl(item) && videoKey(item));
  }

  function watermarkRequestTarget(video) {
    if (!video || typeof video !== 'object') return null;
    return {
      vid: video.vid || video.doubaoInternalVideoId || '',
      messageId: video.messageId || video.doubaoInternalMessageId || '',
      doubaoInternalTaskId: video.doubaoInternalTaskId || '',
      doubaoInternalMessageId: video.doubaoInternalMessageId || video.messageId || '',
      doubaoInternalVideoId: video.doubaoInternalVideoId || video.vid || '',
      doubaoResultKey: video.doubaoResultKey || '',
      networkAnchorTaskId: video.networkAnchorTaskId || '',
      networkAnchorAccountId: video.networkAnchorAccountId || '',
      networkAnchorSubmittedAt: Number(video.networkAnchorSubmittedAt || 0),
      assetUrl: video.assetUrl || '',
      backupUrl: video.backupUrl || '',
      noWatermarkUrl: video.noWatermarkUrl || '',
      doubaoResultMeta: video.doubaoResultMeta || {},
    };
  }

  function isTrustedWatermarkSource(video) {
    if (!video || typeof video !== 'object') return false;
    const marker = String(video.doubaoWatermarkSource || video.watermarkSource || video.source || '');
    if (/derived|dom-|performance|network-capture|native/i.test(marker)) return false;
    if (/(?:samantha-(?:media|video)-get-play-info|samantha[-_/]?(?:media|video)[-_/]?get[-_]?play[-_]?info|alice-share-save|creativity-share-info|get-play-info|mget-play-info|generation-task-list|get-media-info|share-info|watermark-extractor|no-watermark-api|network-no-watermark|doubao-api)/i.test(marker)) return true;
    const metaSource = String(video.doubaoResultMeta && (video.doubaoResultMeta.watermarkSource || video.doubaoResultMeta.source) || '');
    return /(?:samantha-(?:media|video)-get-play-info|samantha[-_/]?(?:media|video)[-_/]?get[-_]?play[-_]?info|alice-share-save|creativity-share-info|get-play-info|mget-play-info|generation-task-list|get-media-info|share-info|watermark-extractor|no-watermark-api|network-no-watermark|doubao-api)/i.test(metaSource);
  }

  function trustedNoWatermarkUrl(video) {
    if (!video || typeof video !== 'object') return '';
    const explicit = video.noWatermarkUrl || video.no_watermark_url || '';
    const url = normalizeNoWatermarkVideoUrl(explicit);
    if (!url || !isTrustedWatermarkSource(video)) return '';
    return url;
  }

  function isWatermarkResolvedCandidate(video) {
    return Boolean(trustedNoWatermarkUrl(video));
  }

  function normalizeExtractedVideoCandidate(item) {
    if (!item || typeof item !== 'object') return item;
    const next = { ...item };
    const sourceText = String(next.source || '');
    if (!next.doubaoWatermarkSource && /samantha[-_/]?video[-_/]?get[-_]?play[-_]?info/i.test(sourceText)) next.doubaoWatermarkSource = 'samantha-video-get-play-info';
    if (!next.doubaoWatermarkSource && /samantha[-_/]?media[-_/]?get[-_]?play[-_]?info|get[-_]?play[-_]?info/i.test(sourceText)) next.doubaoWatermarkSource = 'samantha-media-get-play-info';
    if (!next.doubaoWatermarkSource && /alice[-_/]?share[-_]?save/i.test(sourceText)) next.doubaoWatermarkSource = 'alice-share-save';
    if (!next.doubaoWatermarkSource && /creativity[-_/]?share[-_]?info/i.test(sourceText)) next.doubaoWatermarkSource = 'creativity-share-info';
    if (!next.doubaoWatermarkSource && /mget-play-info/i.test(sourceText)) next.doubaoWatermarkSource = 'mget-play-info';
    if (!next.doubaoWatermarkSource && /generation-task-list/i.test(sourceText)) next.doubaoWatermarkSource = 'generation-task-list';
    if (!next.doubaoWatermarkSource && /get-media-info/i.test(sourceText)) next.doubaoWatermarkSource = 'get-media-info';
    if (!next.doubaoWatermarkSource && /share-info/i.test(sourceText)) next.doubaoWatermarkSource = 'share-info';
    if (!next.doubaoWatermarkSource && /watermark.?extract|no.?watermark/i.test(sourceText)) next.doubaoWatermarkSource = 'watermark-extractor';
    const trustedWatermarkSource = isTrustedWatermarkSource(next);
    const explicitNoWatermarkUrl = next.noWatermarkUrl || next.no_watermark_url || '';
    const noWatermarkUrl = normalizeNoWatermarkVideoUrl(explicitNoWatermarkUrl || (trustedWatermarkSource ? next.playUrl || next.mainUrl || '' : ''));
    if (noWatermarkUrl) {
      next.noWatermarkUrl = noWatermarkUrl;
      const currentAssetUrl = normalizeVideoUrl(next.assetUrl || '');
      if (!currentAssetUrl || currentAssetUrl !== noWatermarkUrl) {
        if (next.assetUrl && next.assetUrl !== noWatermarkUrl && !next.backupUrl) next.backupUrl = next.assetUrl;
        next.assetUrl = noWatermarkUrl;
      }
    }
    if (next.assetUrl) {
      const assetUrl = normalizeVideoUrl(next.assetUrl);
      next.assetUrl = isLikelyVideoResourceUrl(assetUrl) ? assetUrl : '';
    }
    if (next.backupUrl) {
      const backupUrl = normalizeVideoUrl(next.backupUrl);
      next.backupUrl = isLikelyVideoResourceUrl(backupUrl) ? backupUrl : '';
    }
    if (isWatermarkResolvedCandidate(next)) {
      next.doubaoWatermarkResolved = true;
      next.doubaoWatermarkSource = next.doubaoWatermarkSource || 'watermark-extractor';
      next.source = String(next.source || 'watermark-extractor');
    }
    return next;
  }

  function mergeVideoCandidate(target, item) {
    for (const key of [
      'vid', 'messageId', 'assetUrl', 'backupUrl', 'noWatermarkUrl', 'title', 'cardText',
      'doubaoInternalTaskId', 'doubaoInternalMessageId', 'doubaoInternalVideoId', 'doubaoResultKey', 'overlayTargetKey',
      'doubaoWatermarkSource', 'doubaoWatermarkDiagnostic',
      'networkAnchorTaskId', 'networkAnchorAccountId',
    ]) {
      if (!target[key] && item[key]) target[key] = item[key];
    }
    if (item.doubaoWatermarkResolved) target.doubaoWatermarkResolved = true;
    if (item.networkAnchorTrusted) target.networkAnchorTrusted = true;
    if (item.networkPromptMatched) target.networkPromptMatched = true;
    for (const key of ['width', 'height', 'rectLeft', 'rectRight', 'rectTop', 'rectBottom', 'extractedAt', 'networkAnchorSubmittedAt']) {
      if (!Number.isFinite(Number(target[key])) || Number(target[key]) === 0) {
        if (Number.isFinite(Number(item[key])) && Number(item[key]) !== 0) target[key] = item[key];
      }
    }
    target.score = Math.max(Number(target.score || 0), Number(item.score || 0));
    if (item.source && !String(target.source || '').includes(item.source)) {
      target.source = [target.source, item.source].filter(Boolean).join('+');
    }
    preferVideoUrl(target, item.assetUrl || '');
    preferVideoUrl(target, item.backupUrl || '');
    preferVideoUrl(target, item.noWatermarkUrl || '');
    target.doubaoResultMeta = normalizeDoubaoResultMeta(target.doubaoResultMeta, item.doubaoResultMeta, {
      resultKeys: videoIdentityKeys(target),
      source: target.source || item.source || '',
      watermarkSource: target.doubaoWatermarkSource || item.doubaoWatermarkSource || '',
    });
  }

  function hasUsableVideoUrl(video) {
    return Boolean(video && (
      isLikelyVideoResourceUrl(video.assetUrl)
      || isLikelyVideoResourceUrl(video.backupUrl)
      || Boolean(normalizeNoWatermarkVideoUrl(video.noWatermarkUrl || video.no_watermark_url || ''))
    ));
  }

  function normalizeVideoUrl(value) {
    let url = String(value || '').trim();
    if (!url || url.startsWith('blob:') || url.startsWith('data:')) return '';
    url = url.replace(/\\u0026/g, '&').replace(/&amp;/g, '&').replace(/\\\//g, '/');
    if (/^https%3A%2F%2F/i.test(url)) {
      try { url = decodeURIComponent(url); } catch (error) {}
    }
    try {
      return new URL(url, location.href).toString();
    } catch (error) {
      return '';
    }
  }

  function normalizeNoWatermarkVideoUrl(value) {
    const url = toNoWatermarkVideoUrl(value);
    if (!url || !isNoWatermarkVideoUrl(url) || isExplicitDoubaoWatermarkUrl(url) || !isLikelyVideoResourceUrl(url)) return '';
    return url;
  }

  function normalizeDoubaoNoWatermarkUrl(value) {
    return normalizeNoWatermarkVideoUrl(value);
  }

  function isExplicitDoubaoWatermarkUrl(value) {
    const text = String(value || '');
    return /video_gen_watermark(?:_[a-z0-9]+)*/i.test(text)
      || /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|logo_type=(?!video_gen_no_watermark)[^&#]*watermark)/i.test(text);
  }

  function isBlockedWatermarkVariantUrl(value) {
    return /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|(?:[?&]logo_type=(?!video_gen_no_watermark)[^&#]*watermark))/i.test(String(value || ''));
  }

  function isNoWatermarkVideoUrl(value) {
    const text = String(value || '');
    return /(?:[?&]lr=video_gen_no_watermark|video_gen_no_watermark)/i.test(text)
      || isDolaCleanVideoUrl(text);
  }

  function isDolaCleanVideoUrl(value) {
    const url = normalizeVideoUrl(value);
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

  function toNoWatermarkVideoUrl(value) {
    const url = normalizeVideoUrl(value);
    if (!url || isBlockedWatermarkVariantUrl(url)) return '';
    return url.replace(/video_gen_watermark(?:_[a-z0-9]+)*/ig, 'video_gen_no_watermark');
  }

  function preferVideoUrl(target, value) {
    const url = normalizeVideoUrl(value);
    if (!url || !target || !isLikelyVideoResourceUrl(url)) return;
    if (!target.assetUrl) {
      target.assetUrl = url;
      return;
    }
    if (target.assetUrl === url || target.backupUrl === url) return;
    if (isNoWatermarkVideoUrl(url) && !isNoWatermarkVideoUrl(target.assetUrl)) {
      if (target.assetUrl && !target.backupUrl) target.backupUrl = target.assetUrl;
      target.assetUrl = url;
      return;
    }
    if (!target.backupUrl || (isNoWatermarkVideoUrl(url) && !isNoWatermarkVideoUrl(target.backupUrl))) {
      target.backupUrl = url;
    }
  }

  function isLikelyDirectVideoUrl(value) {
    const url = normalizeVideoUrl(value);
    if (!url) return false;
    if (/\.(?:png|jpe?g|webp|gif|avif|heic|heif)(?:~|$|[?#])/i.test(url)) return false;
    if (isExplicitDoubaoWatermarkUrl(url)) return false;
    if (/image_generation|tplv-[^/?#]*image|\.image(?:$|[?#~])/i.test(url) && !/video/i.test(url)) return false;
    return isLikelyVideoResourceUrl(url);
  }

  function isLikelyHtmlDocumentUrl(value) {
    const url = normalizeVideoUrl(value);
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
    const url = normalizeVideoUrl(value);
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
    if (isExplicitDoubaoWatermarkUrl(url)) return false;
    if (/\.(?:png|jpe?g|webp|gif|avif|svg|heic|heif)(?:~|$|[?#])/i.test(url)) return false;
    if (/image_generation|tplv-[^/?#]*image|\.image(?:$|[?#~])/i.test(url) && !/video|play|media/i.test(url)) return false;
    return /\.(?:mp4|mov|webm|m4v)(?:$|[?#])|(?:[?&](?:vid|video_id|videoId|item_id|itemId|media_id|play_id)=)|\/video(?:[_/-]|$)|video_generation|play_?url|main_?url|download_?url|backup_?url|media_?url|tos-[^/?#]+\/obj\/[^?#]*(?:video|media|mp4)|byteimg\.com\/[^?#]*(?:video|tos|obj|mp4)|bytevod|voddos|vod-|video\.?tos/i.test(url);
  }

  function extractVideoId(url) {
    const match = String(url || '').match(/(?:vid=|video_id=|videoId=|item_id=|itemId=|media_id=|mediaId=|play_id=|playId=|\/)(v0[a-zA-Z0-9_-]{6,}|[a-f0-9]{16,}|[0-9]{8,})/i);
    return match ? match[1] : hashString(url);
  }

  function hashString(value) {
    let hash = 2166136261;
    const text = String(value || '');
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return `dom-${(hash >>> 0).toString(16)}`;
  }

  function extractDoubaoInternalIds(node, seed = {}) {
    const result = {
      doubaoInternalTaskId: '',
      doubaoInternalMessageId: '',
      doubaoInternalVideoId: '',
      doubaoResultKey: '',
      assetUrl: normalizeVideoUrl(seed.assetUrl || ''),
      backupUrl: normalizeVideoUrl(seed.backupUrl || ''),
      doubaoResultMeta: { sourceKeys: [], resultKeys: [] },
    };
    const add = (kind, value, sourceKey = '', source = '') => addInternalId(result, kind, value, sourceKey, source);
    add('video', seed.vid || extractVideoIdFromAnyUrl(seed.assetUrl || seed.backupUrl || ''), 'seed.vid', 'seed');
    add('message', seed.messageId || '', 'seed.messageId', 'seed');
    add('task', seed.taskId || '', 'seed.taskId', 'seed');
    let current = node;
    for (let i = 0; current && i < 9; i++, current = current.parentElement) {
      const attrs = current.getAttributeNames ? current.getAttributeNames() : [];
      for (const name of attrs) {
        const kind = classifyDoubaoIdKey(name);
        if (!kind) continue;
        add(kind, current.getAttribute(name) || '', name, 'dom');
      }
      collectReactInternalIds(current, result);
    }
    result.doubaoResultKey = buildDoubaoResultKey(result);
    result.doubaoResultMeta = normalizeDoubaoResultMeta({
      ...(result.doubaoResultMeta || {}),
      resultKeys: videoIdentityKeys(result),
    });
    return result;
  }

  function collectReactInternalIds(node, result) {
    if (!node || !Object.getOwnPropertyNames) return;
    let names = [];
    try {
      names = Object.getOwnPropertyNames(node).filter((name) => /__react|__fiber|__vue|__svelte/i.test(name)).slice(0, 8);
    } catch (error) {
      return;
    }
    for (const name of names) {
      try {
        collectInternalIdsFromObject(node[name], result, 0, `prop:${name}`);
      } catch (error) {}
    }
  }

  function collectInternalIdsFromObject(value, result, depth = 0, source = 'object', seen = null, budget = null) {
    if (!value || typeof value !== 'object' || depth > 5) return;
    const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
    const nextBudget = budget || { count: 0 };
    if (++nextBudget.count > 2500) return;
    if (nextSeen) {
      if (nextSeen.has(value)) return;
      nextSeen.add(value);
    }
    if (Array.isArray(value)) {
      const list = value.length > 80 ? value.slice(-80) : value;
      list.forEach((item, index) => collectInternalIdsFromObject(item, result, depth + 1, `${source}[${index}]`, nextSeen, nextBudget));
      return;
    }
    for (const [key, item] of Object.entries(value).slice(-500)) {
      if (isSensitiveIdKey(key)) continue;
      const kind = classifyDoubaoIdKey(key);
      if (kind && (typeof item === 'string' || typeof item === 'number')) {
        addInternalId(result, kind, item, key, source);
      }
      if (typeof item === 'string' && isMediaUrlKey(key) && isLikelyVideoResourceUrl(item)) {
        addInternalMediaUrl(result, item, key, source);
      }
      if (item && typeof item === 'object' && depth < 4) {
        collectInternalIdsFromObject(item, result, depth + 1, `${source}.${key}`, nextSeen, nextBudget);
      }
    }
  }

  function addInternalId(target, kind, value, sourceKey = '', source = '') {
    const id = normalizeDoubaoInternalId(value, kind);
    if (!id) return false;
    const field = kind === 'task'
      ? 'doubaoInternalTaskId'
      : kind === 'message'
        ? 'doubaoInternalMessageId'
        : 'doubaoInternalVideoId';
    if (!target[field]) target[field] = id;
    const key = normalizeIdentityKey(kind, id);
    target.doubaoResultMeta = normalizeDoubaoResultMeta(target.doubaoResultMeta, {
      sourceKeys: [`${key}@${String(source || sourceKey || 'unknown').slice(0, 80)}`],
      resultKeys: [key],
    });
    return true;
  }

  function addInternalMediaUrl(target, value, sourceKey = '', source = '') {
    const url = normalizeVideoUrl(value);
    if (!url) return false;
    if (!target.assetUrl) target.assetUrl = url;
    else if (!target.backupUrl && target.assetUrl !== url) target.backupUrl = url;
    target.doubaoResultMeta = normalizeDoubaoResultMeta(target.doubaoResultMeta, {
      sourceKeys: [`url:${url}@${String(source || sourceKey || 'unknown').slice(0, 80)}`],
      resultKeys: [normalizeIdentityKey('url', url)],
    });
    return true;
  }

  function isMediaUrlKey(key) {
    return /url|uri|src|play|main|backup|download|media|video|item/i.test(String(key || ''));
  }

  function classifyDoubaoIdKey(key) {
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

  function isSensitiveIdKey(key) {
    return /token|cookie|secret|auth|csrf|passport|session|credential|password|web_id|device_id|user_id|uid|open_id/i.test(String(key || ''));
  }

  function normalizeDoubaoInternalId(value, kind = '') {
    const text = String(value ?? '').trim();
    if (!text || text.length > 500) return '';
    const fromUrl = extractIdFromUrlText(text, kind);
    if (fromUrl) return fromUrl;
    const safe = text.match(/^(v0[a-zA-Z0-9_-]{6,}|[a-f0-9]{16,}|[0-9]{8,}|[a-zA-Z0-9][a-zA-Z0-9_-]{7,119})$/i);
    if (safe) return safe[1].slice(0, 120);
    const embedded = text.match(/(?:^|[^\w-])(v0[a-zA-Z0-9_-]{6,}|[a-f0-9]{16,}|[0-9]{8,})(?:$|[^\w-])/i);
    return embedded ? embedded[1].slice(0, 120) : '';
  }

  function extractIdFromUrlText(value, kind = '') {
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
          const item = normalizeDoubaoInternalId(url.searchParams.get(name) || '', kind);
          if (item) return item;
        }
      }
    } catch (error) {}
    for (const name of names) {
      const pattern = new RegExp(`(?:[?&#/]|^)${name}=([^&#/?]+)`, 'i');
      const match = text.match(pattern);
      if (match) {
        const item = normalizeDoubaoInternalId(decodeURIComponent(match[1] || ''), kind);
        if (item) return item;
      }
    }
    return '';
  }

  function extractVideoIdFromAnyUrl(value) {
    const url = normalizeVideoUrl(value);
    return url ? extractVideoId(url) : '';
  }

  function normalizeIdentityKey(prefix, value) {
    const text = String(value || '').trim();
    if (!text) return '';
    if (/^(result|task|message|video|vid|url):/i.test(text)) return text;
    return `${prefix}:${text}`;
  }

  function buildDoubaoResultKey(video) {
    const keys = videoIdentityKeys(video).filter((key) => /^(task|video|message|vid|url):/i.test(key));
    return keys[0] || '';
  }

  function videoIdentityKeys(video) {
    if (!video) return [];
    const keys = [];
    const add = (prefix, value) => {
      const key = normalizeIdentityKey(prefix, value);
      if (key && !keys.includes(key)) keys.push(key);
    };
    add('task', video.doubaoInternalTaskId || '');
    add('video', video.doubaoInternalVideoId || '');
    add('message', video.doubaoInternalMessageId || '');
    add('vid', video.vid || '');
    add('message', video.messageId || '');
    add('url', video.assetUrl || '');
    add('url', video.backupUrl || '');
    add('url', video.noWatermarkUrl || '');
    add('result', video.doubaoResultKey || '');
    return keys;
  }

  function legacyVideoKeys(video) {
    return [
      video && video.vid,
      video && video.messageId,
      video && video.assetUrl,
      video && video.backupUrl,
      video && video.noWatermarkUrl,
    ].filter(Boolean).map((item) => String(item));
  }

  function normalizeDoubaoResultMeta(...items) {
    const meta = {};
    const addArray = (key, value, limit = 40) => {
      const list = Array.isArray(value) ? value : (value ? [value] : []);
      const current = Array.isArray(meta[key]) ? meta[key] : [];
      for (const item of list) {
        const text = String(item || '').slice(0, 240);
        if (text && !current.includes(text)) current.push(text);
      }
      meta[key] = current.slice(-limit);
    };
    for (const item of items.filter(Boolean)) {
      if (item.source) meta.source = String(item.source).slice(0, 120);
      if (item.watermarkSource) meta.watermarkSource = String(item.watermarkSource).slice(0, 80);
      if (item.extractionIndex !== undefined) meta.extractionIndex = Number(item.extractionIndex) || 0;
      if (item.score !== undefined) meta.score = Number(item.score) || 0;
      addArray('sourceKeys', item.sourceKeys);
      addArray('resultKeys', item.resultKeys);
    }
    return meta;
  }

  function readKnownDoubaoResultKeys() {
    const keys = new Set();
    const addKnown = (kind, value) => {
      const id = normalizeDoubaoInternalId(value, kind);
      const key = normalizeIdentityKey(kind, id);
      if (key) keys.add(key);
    };
    const scanObject = (value, depth = 0, seen = null, budget = { count: 0 }) => {
      if (!value || typeof value !== 'object' || depth > 6 || ++budget.count > 5000) return;
      const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
      if (nextSeen) {
        if (nextSeen.has(value)) return;
        nextSeen.add(value);
      }
      if (Array.isArray(value)) {
        const list = value.length > 160 ? value.slice(-160) : value;
        list.forEach((item) => scanObject(item, depth + 1, nextSeen, budget));
        return;
      }
      for (const [key, item] of Object.entries(value).slice(-800)) {
        if (isSensitiveIdKey(key)) continue;
        const kind = classifyDoubaoIdKey(key);
        if (kind && (typeof item === 'string' || typeof item === 'number')) addKnown(kind, item);
        if (item && typeof item === 'object' && depth < 4) scanObject(item, depth + 1, nextSeen, budget);
      }
    };
    try { scanObject(globalThis.__MODERN_ROUTER_DATA); } catch (error) {}
    for (const node of Array.from(document.querySelectorAll('*')).slice(-2500)) {
      const attrs = node.getAttributeNames ? node.getAttributeNames() : [];
      for (const name of attrs) {
        const kind = classifyDoubaoIdKey(name);
        if (kind) addKnown(kind, node.getAttribute(name) || '');
      }
    }
    try {
      for (const video of extractVisibleResultVideos()) videoIdentityKeys(video).forEach((key) => keys.add(key));
    } catch (error) {}
    return [...keys].slice(-1000);
  }

  function knownKeySetFromGuard(guard = {}) {
    const keys = new Set();
    const add = (item) => {
      if (!item) return;
      if (Array.isArray(item)) {
        item.forEach(add);
        return;
      }
      if (typeof item === 'object') {
        Object.values(item).forEach(add);
        return;
      }
      const text = String(item || '').trim();
      if (!text) return;
      if (/^(result|task|message|video|vid|url):/i.test(text)) keys.add(text);
      else keys.add(text);
    };
    add(guard.knownResultKeysBefore);
    add(guard.knownInternalIdsBefore);
    add(guard.beforeVideoKeys);
    return keys;
  }

  function exactGuardKeySet(guard = {}) {
    const keys = new Set();
    const add = (prefix, value) => {
      const key = normalizeIdentityKey(prefix, value);
      if (key) keys.add(key);
    };
    if (guard.doubaoResultKey) keys.add(String(guard.doubaoResultKey));
    add('task', guard.doubaoInternalTaskId);
    add('message', guard.doubaoInternalMessageId);
    add('video', guard.doubaoInternalVideoId);
    const meta = guard.doubaoResultMeta || {};
    if (Array.isArray(meta.resultKeys)) meta.resultKeys.forEach((key) => keys.add(String(key)));
    return keys;
  }

  function isKnownVideoCandidate(video, beforeKeys, guard = {}) {
    const identities = videoIdentityKeys(video);
    const legacy = legacyVideoKeys(video);
    const known = knownKeySetFromGuard(guard);
    for (const key of [...identities, ...legacy]) {
      if ((beforeKeys && beforeKeys.has && beforeKeys.has(key)) || known.has(key)) return true;
    }
    return false;
  }

  function buildSelectedVideoPatch(video) {
    const meta = normalizeDoubaoResultMeta(video && video.doubaoResultMeta, {
      resultKeys: videoIdentityKeys(video),
      source: video && video.source || '',
      score: video && video.score,
      extractionIndex: video && video.extractionIndex,
      watermarkSource: video && video.doubaoWatermarkSource || '',
      watermarkDiagnostic: video && video.doubaoWatermarkDiagnostic,
    });
    return {
      doubaoInternalTaskId: video && video.doubaoInternalTaskId || '',
      doubaoInternalMessageId: video && (video.doubaoInternalMessageId || video.messageId) || '',
      doubaoInternalVideoId: video && (video.doubaoInternalVideoId || video.vid) || '',
      doubaoResultKey: videoKey(video),
      networkAnchorTaskId: video && video.networkAnchorTaskId || '',
      networkAnchorAccountId: video && video.networkAnchorAccountId || '',
      networkAnchorSubmittedAt: Number(video && video.networkAnchorSubmittedAt || 0),
      networkAnchorTrusted: Boolean(video && video.networkAnchorTrusted),
      networkPromptMatched: Boolean(video && video.networkPromptMatched),
      noWatermarkUrl: video && video.noWatermarkUrl || '',
      doubaoWatermarkSource: video && video.doubaoWatermarkSource || '',
      doubaoWatermarkResolved: Boolean(video && isWatermarkResolvedCandidate(video)),
      doubaoWatermarkDiagnostic: video && video.doubaoWatermarkDiagnostic || lastWatermarkDiagnostic || null,
      doubaoResultMeta: meta,
    };
  }

  function shareVideoIdentity(a, b) {
    const aKeys = new Set(videoIdentityKeys(a).filter((key) => !/^url:/i.test(key)));
    const bKeys = videoIdentityKeys(b).filter((key) => !/^url:/i.test(key));
    if (bKeys.some((key) => aKeys.has(key))) return true;
    const aLegacy = new Set(legacyVideoKeys(a).filter((key) => !/^https?:/i.test(key)));
    return legacyVideoKeys(b).some((key) => key && !/^https?:/i.test(key) && aLegacy.has(key));
  }

  function hasStrongWatermarkTarget(video) {
    if (!video || typeof video !== 'object') return false;
    return [
      video.vid,
      video.doubaoInternalVideoId,
      video.messageId,
      video.doubaoInternalMessageId,
      video.doubaoInternalTaskId,
      video.networkAnchorTaskId,
      video.doubaoResultKey,
    ].some((value) => String(value || '').trim().length >= 6);
  }

  function pickResolvedWatermarkMatch(selected, videos, allowLooseSingle = false) {
    const resolved = (Array.isArray(videos) ? videos : []).filter((item) => isWatermarkResolvedCandidate(item));
    if (!resolved.length) return null;
    return resolved.find((item) => shareVideoIdentity(selected, item))
      || (resolved.length === 1 && (allowLooseSingle || !hasStrongWatermarkTarget(selected)) ? resolved[0] : null);
  }

  async function resolveNoWatermarkForVideo(video, task = null, attempts = 2) {
    let selected = normalizeExtractedVideoCandidate(video || {});
    if (!selected || isWatermarkResolvedCandidate(selected)) return selected;
    let diagnostic = watermarkDiagnostic('not-started');
    for (let index = 1; index <= attempts; index += 1) {
      if (index > 1) await sleep(1200);
      const plans = [
        { label: 'targeted', target: selected, timeoutMs: index === 1 ? 16000 : 22000, allowLooseSingle: true },
      ];
      if (index > 1 && (!task || !hasStrongWatermarkTarget(selected))) {
        plans.push({ label: 'page-scan', target: null, timeoutMs: 22000, allowLooseSingle: !task });
      }
      let videos = [];
      for (const plan of plans) {
        const result = await requestWatermarkCandidates(plan.timeoutMs, { diagnostic: true, target: plan.target }).catch((error) => ({
          videos: [],
          diagnostic: watermarkDiagnostic('request-error', { error: String(error && error.message || error), plan: plan.label }),
        }));
        const planVideos = mergeExtractedVideos(Array.isArray(result.videos) ? result.videos : []);
        videos = mergeExtractedVideos(videos, planVideos);
        diagnostic = {
          ...(result.diagnostic || lastWatermarkDiagnostic || diagnostic || {}),
          plan: plan.label,
          count: planVideos.length || (result.diagnostic && result.diagnostic.count) || 0,
        };
        const matched = pickResolvedWatermarkMatch(selected, planVideos, plan.allowLooseSingle);
        if (matched) {
          mergeVideoCandidate(selected, matched);
          selected.doubaoWatermarkDiagnostic = watermarkDiagnostic('resolved', {
            attempt: index,
            plan: plan.label,
            count: planVideos.length,
            matchedKey: videoKey(matched),
          });
          return selected;
        }
      }
      if (task && task.id) {
        await updateTask({
          id: task.id,
          status: 'submitted',
          stage: 'watermark-resolving',
          doubaoWatermarkDiagnostic: {
            ...(diagnostic || {}),
            reason: diagnostic && diagnostic.reason || 'no-candidate',
            attempt: index,
            count: videos.length,
          },
        }).catch(() => {});
      }
    }
    selected.doubaoWatermarkDiagnostic = {
      ...(diagnostic || watermarkDiagnostic('no-candidate')),
      reason: diagnostic && diagnostic.reason || 'no-candidate',
      selectedKey: videoKey(selected),
    };
    return selected;
  }

  function extractMessageId(node) {
    let current = node;
    for (let i = 0; current && i < 6; i++, current = current.parentElement) {
      const attrs = current.getAttributeNames ? current.getAttributeNames() : [];
      for (const name of attrs) {
        const value = current.getAttribute(name) || '';
        if (/message|conversation|chat|item/i.test(name) && value) return value.slice(0, 120);
      }
    }
    return '';
  }

  function closestResultCard(node) {
    let current = node;
    for (let i = 0; current && i < 8; i++, current = current.parentElement) {
      if (!(current instanceof HTMLElement)) break;
      const text = String(current.innerText || current.textContent || '');
      if (hasCompletedStatusSignal(text) || /下载|保存|AI生成|播放/i.test(text)) return current;
      const rect = current.getBoundingClientRect();
      if (rect.width >= 160 && rect.height >= 120) return current;
    }
    return node;
  }

  function scoreResultCandidate(node, hasDoneMarker) {
    if (!node || !(node instanceof HTMLElement)) return hasDoneMarker ? 100 : 0;
    const rect = node.getBoundingClientRect();
    const text = String(node.innerText || node.textContent || '');
    let score = 0;
    if (hasDoneMarker) score += 500;
    if (hasCompletedStatusSignal(text) || /下载|保存|AI生成|播放/i.test(text)) score += 600;
    score += Math.min(rect.width * rect.height / 1000, 600);
    score += rect.top > 0 ? 80 : 0;
    score += rect.left > window.innerWidth * 0.25 ? 80 : 0;
    return score;
  }

  function isResultMediaVisible(item) {
    if (!(item instanceof HTMLElement)) return false;
    if (item.closest(`#${ROOT_ID}`)) return false;
    const rect = item.getBoundingClientRect();
    if (!rect || rect.width < 24 || rect.height < 24) return false;
    if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight + 600 || rect.left > window.innerWidth) return false;
    const style = getComputedStyle(item);
    return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.01;
  }

  function ensureCoreScript() {
    const existing = document.getElementById(CORE_SCRIPT_ID);
    if (existing) return existing;
    const script = document.createElement('script');
    script.id = CORE_SCRIPT_ID;
    script.src = chrome.runtime.getURL('core.js');
    script.async = false;
    script.addEventListener('load', () => { script.dataset.loaded = 'true'; }, { once: true });
    script.addEventListener('error', () => { script.dataset.loaded = 'error'; }, { once: true });
    (document.head || document.documentElement).appendChild(script);
    return script;
  }

  function ensureNetworkCaptureScript() {
    const existing = document.getElementById(NETWORK_SCRIPT_ID);
    if (existing) return existing;
    const script = document.createElement('script');
    script.id = NETWORK_SCRIPT_ID;
    script.src = chrome.runtime.getURL('network.js');
    script.async = false;
    script.addEventListener('load', () => { script.dataset.loaded = 'true'; }, { once: true });
    script.addEventListener('error', () => { script.dataset.loaded = 'error'; }, { once: true });
    (document.head || document.documentElement).appendChild(script);
    return script;
  }

  function ensureWatermarkExtractorScript() {
    const existing = document.getElementById(WATERMARK_EXTRACTOR_SCRIPT_ID);
    if (existing) return existing;
    const script = document.createElement('script');
    script.id = WATERMARK_EXTRACTOR_SCRIPT_ID;
    script.src = chrome.runtime.getURL('watermark-extractor.js');
    script.async = false;
    script.addEventListener('load', () => { script.dataset.loaded = 'true'; }, { once: true });
    script.addEventListener('error', () => { script.dataset.loaded = 'error'; }, { once: true });
    (document.head || document.documentElement).appendChild(script);
    return script;
  }

  async function waitForNetworkCaptureReady() {
    const script = ensureNetworkCaptureScript();
    if (!script || script.dataset.loaded) return;
    await new Promise((resolve) => {
      const done = () => resolve();
      script.addEventListener('load', done, { once: true });
      script.addEventListener('error', done, { once: true });
      setTimeout(done, 800);
    });
  }

  async function waitForWatermarkExtractorReady() {
    const script = ensureWatermarkExtractorScript();
    if (!script || script.dataset.loaded) return;
    await new Promise((resolve) => {
      const done = () => resolve();
      script.addEventListener('load', done, { once: true });
      script.addEventListener('error', done, { once: true });
      setTimeout(done, 1200);
    });
  }

  async function requestNetworkCandidates(timeoutMs = 700, task = null) {
    await waitForNetworkCaptureReady();
    const requestId = crypto.randomUUID();
    const guard = task && task.resultGuard || {};
    return await new Promise((resolve) => {
      const timer = setTimeout(() => {
        window.removeEventListener('message', listener);
        resolve([]);
      }, timeoutMs);
      const listener = (event) => {
        const data = event.data;
        if (data && data.source === CHANNEL && data.type === 'network-candidates-result' && data.requestId === requestId) {
          window.removeEventListener('message', listener);
          clearTimeout(timer);
          resolve(Array.isArray(data.videos) ? data.videos : []);
        }
      };
      window.addEventListener('message', listener);
      window.postMessage({
        source: CHANNEL,
        type: 'network-candidates-request',
        requestId,
        taskId: task ? task.id || getAssignedTaskId() || '' : '',
        accountId: task ? (task.accountId || task.currentAccountId) || getAssignedAccountId() || '' : '',
        submittedAt: Number(guard && guard.submittedAt || 0),
      }, '*');
    });
  }

  async function resetNetworkCaptureForTask(task, resultGuard = {}) {
    await waitForNetworkCaptureReady().catch(() => {});
    window.postMessage({
      source: CHANNEL,
      type: 'network-capture-reset',
      taskId: task && task.id || getAssignedTaskId() || '',
      accountId: task && (task.accountId || task.currentAccountId) || getAssignedAccountId() || '',
      submittedAt: Number(resultGuard && resultGuard.submittedAt || Date.now()),
      promptFingerprint: String(resultGuard && resultGuard.promptFingerprint || ''),
      promptProbe: Array.isArray(resultGuard && resultGuard.promptProbe) ? resultGuard.promptProbe : [],
    }, '*');
    await sleep(30);
  }

  async function waitForCoreScriptReady() {
    const script = ensureCoreScript();
    if (!script || script.dataset.loaded) return;
    await new Promise((resolve) => {
      const done = () => resolve();
      script.addEventListener('load', done, { once: true });
      script.addEventListener('error', done, { once: true });
      setTimeout(done, 800);
    });
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = css;
    document.documentElement.appendChild(style);
  }

  function ensurePanel() {
    if (document.getElementById(ROOT_ID)) return;
    const root = document.createElement('div');
    root.id = ROOT_ID;
    root.dataset.open = 'false';
    root.innerHTML = `
    <button class="dbvd-fab" type="button" title="豆包任务助手">助手</button>
    <section class="dbvd-panel" aria-label="豆包任务助手">
      <header class="dbvd-head">
        <h2 class="dbvd-title">豆包任务助手</h2>
      </header>
      <div class="dbvd-body"><div class="dbvd-message">等待软件分配分镜任务</div></div>
    </section>`;
    document.documentElement.appendChild(root);
    const fab = root.querySelector('.dbvd-fab');
    const title = root.querySelector('.dbvd-title');
    if (fab) {
      fab.textContent = '助手';
      fab.title = '豆包任务助手';
    }
    if (title) title.textContent = '豆包任务助手';
    bindPanel(root);
    showIdleTaskMessage();
  }

  function bindPanel(root) {
    root.querySelector('.dbvd-fab').addEventListener('click', () => {
      root.dataset.open = String(root.dataset.open !== 'true');
    });
  }

  function bindNativeDownloadInterceptor() {
    document.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target.closest('a, button, [role="button"], [aria-label], [title], div, span') : null;
      if (!target || target.closest(`#${ROOT_ID}`)) return;
      if (isDoubaoAuthOrNavigationControl(target)) return;
      if (!isLikelyNativeVideoDownloadControl(target)) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      manualDownloadCurrentPageNoWatermark(null, {
        source: 'native-click',
        rect: target.getBoundingClientRect?.(),
        element: target,
        label: nativeDownloadControlText(target),
      }).catch((error) => {
        showVideoDownloadToast(target, error.message || String(error), true);
      });
    }, true);
  }

  function bindVideoDownloadOverlays() {
    const refresh = debounce(updateVideoDownloadOverlays, 350);
    window.addEventListener('scroll', refresh, true);
    window.addEventListener('resize', refresh);
    try {
      const observer = new MutationObserver(refresh);
      observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'style', 'class'] });
    } catch (error) {}
    setInterval(updateVideoDownloadOverlays, 2500);
    setTimeout(updateVideoDownloadOverlays, 800);
  }

  function debounce(fn, delay = 200) {
    let timer = 0;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), delay);
    };
  }

  function updateVideoDownloadOverlays() {
    const targets = findVideoDownloadOverlayTargets();
    const activeKeys = new Set(targets.map((item) => item.key));
    for (const button of [...document.querySelectorAll(`.${VIDEO_DOWNLOAD_OVERLAY_CLASS}`)]) {
      if (!activeKeys.has(button.dataset.targetKey || '')) button.remove();
    }
    for (const target of targets) {
      let button = document.querySelector(`.${VIDEO_DOWNLOAD_OVERLAY_CLASS}[data-target-key="${cssEscape(target.key)}"]`);
      if (!button) {
        button = document.createElement('button');
        button.type = 'button';
        button.className = VIDEO_DOWNLOAD_OVERLAY_CLASS;
        button.dataset.targetKey = target.key;
        button.textContent = '无水印下载';
        button.title = '下载该视频的无水印版本';
        button.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          event.stopImmediatePropagation();
      const trigger = {
        source: 'video-overlay',
        key: button.dataset.targetKey || '',
        rect: rectFromDataset(button.dataset),
        element: button,
      };
          manualDownloadCurrentPageNoWatermark(button, {
            ...trigger,
          }).catch((error) => {
            button.disabled = false;
            button.textContent = '重试';
            showVideoDownloadToast(button, error.message || String(error), true);
          });
        });
        document.documentElement.appendChild(button);
      }
      button.dataset.rectLeft = String(Math.round(target.rect.left || 0));
      button.dataset.rectTop = String(Math.round(target.rect.top || 0));
      button.dataset.rectRight = String(Math.round(target.rect.right || 0));
      button.dataset.rectBottom = String(Math.round(target.rect.bottom || 0));
      positionVideoDownloadOverlay(button, target.rect);
    }
  }

  function findVideoDownloadOverlayTargets() {
    const media = [...document.querySelectorAll('video, canvas, img, [data-video-url], [data-download-url], a[href]')]
      .filter((item) => item instanceof HTMLElement && isResultMediaVisible(item))
      .filter((item) => !item.closest(`#${ROOT_ID}`) && !item.closest(`.${VIDEO_DOWNLOAD_OVERLAY_CLASS}`))
      .map((item) => {
        const card = closestResultCard(item);
        const rect = (item.getBoundingClientRect?.() || card?.getBoundingClientRect?.());
        const key = overlayTargetKey(item, card, rect);
        return { item, card, rect, key };
      })
      .filter((item) => isLikelyGeneratedVideoOverlayTarget(item));
    const unique = new Map();
    for (const item of media) {
      if (!unique.has(item.key)) unique.set(item.key, item);
    }
    return [...unique.values()].slice(-8);
  }

  function isLikelyGeneratedVideoOverlayTarget(target) {
    if (!target || !target.item || !target.rect || !target.key) return false;
    const { item, card, rect } = target;
    if (rect.width < 120 || rect.height < 80) return false;
    if (isDoubaoAuthOrNavigationControl(item) || isDoubaoAuthOrNavigationControl(card)) return false;
    const tag = String(item.tagName || '').toUpperCase();
    if (tag === 'VIDEO' || tag === 'CANVAS') return true;
    const ownUrl = item.currentSrc || item.src || item.href || item.getAttribute?.('href') || item.getAttribute?.('src') || item.getAttribute?.('data-src') || item.getAttribute?.('data-video-url') || item.getAttribute?.('data-download-url') || '';
    if (isLikelyDirectVideoUrl(ownUrl)) return true;
    const cardText = String(card && (card.innerText || card.textContent) || '');
    if (!hasCompletedStatusSignal(cardText) && !/下载视频|保存视频|播放|Seedance|视频/i.test(cardText)) return false;
    const hasVideoSibling = Boolean(card && card.querySelector && card.querySelector('video, canvas, [data-video-url], [data-download-url], a[href*=".mp4"], a[href*="video"]'));
    if (hasVideoSibling) return true;
    if (tag === 'IMG') {
      const altText = String(item.getAttribute('alt') || item.getAttribute('aria-label') || item.getAttribute('title') || '');
      const imageUrl = String(item.currentSrc || item.src || item.getAttribute('src') || item.getAttribute('data-src') || '');
      const looksLikeReference = /参考图|首帧|上传|素材|avatar|user|image_generation|tplv-[^/?#]*image/i.test(`${altText} ${imageUrl} ${cardText}`);
      return !looksLikeReference && rect.width >= 180 && rect.height >= 100;
    }
    return false;
  }

  function overlayTargetKey(item, card, rect) {
    const src = item.currentSrc || item.src || item.getAttribute?.('src') || item.getAttribute?.('data-src') || '';
    const id = item.id || card?.id || '';
    const text = String(card && (card.innerText || card.textContent) || '').replace(/\s+/g, ' ').slice(0, 160);
    const base = src || id || `${Math.round(rect?.left || 0)}:${Math.round(rect?.top || 0)}:${Math.round(rect?.width || 0)}:${Math.round(rect?.height || 0)}:${text}`;
    return `vmo-${hashString(base)}`;
  }

  function positionVideoDownloadOverlay(button, rect) {
    const width = Math.min(120, Math.max(96, rect.width - 12));
    const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width - 8));
    const top = Math.max(8, Math.min(window.innerHeight - 40, rect.top + 8));
    button.style.width = `${width}px`;
    button.style.left = `${Math.round(left)}px`;
    button.style.top = `${Math.round(top)}px`;
  }

  function rectFromDataset(dataset) {
    const rect = {
      left: Number(dataset.rectLeft || 0),
      top: Number(dataset.rectTop || 0),
      right: Number(dataset.rectRight || 0),
      bottom: Number(dataset.rectBottom || 0),
    };
    rect.width = Math.max(0, rect.right - rect.left);
    rect.height = Math.max(0, rect.bottom - rect.top);
    return rect;
  }

  function showVideoDownloadToast(anchor, text, bad = false) {
    if (!anchor) return;
    const toast = document.createElement('div');
    toast.className = 'dbvd-video-download-toast';
    toast.dataset.tone = bad ? 'bad' : 'ok';
    toast.textContent = text;
    document.documentElement.appendChild(toast);
    const anchorRect = anchor.getBoundingClientRect();
    const toastRect = toast.getBoundingClientRect();
    const left = Math.max(8, Math.min(window.innerWidth - toastRect.width - 8, anchorRect.right - toastRect.width));
    const top = Math.max(8, Math.min(window.innerHeight - toastRect.height - 8, anchorRect.bottom + 8));
    toast.style.left = `${Math.round(left)}px`;
    toast.style.top = `${Math.round(top)}px`;
    setTimeout(() => toast.remove(), bad ? 5200 : 2200);
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(String(value || ''));
    return String(value || '').replace(/["\\]/g, '\\$&');
  }

  function nativeDownloadControlText(node) {
    const parts = [];
    let current = node;
    for (let i = 0; current && i < 3; i += 1, current = current.parentElement) {
      parts.push(
        current.innerText,
        current.textContent,
        current.getAttribute && current.getAttribute('aria-label'),
        current.getAttribute && current.getAttribute('title'),
        current.getAttribute && current.getAttribute('download'),
      );
    }
    return parts.filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
  }

  function nativeControlHint(node, depth = 4) {
    const parts = [];
    let current = node instanceof Element ? node : null;
    for (let i = 0; current && i < depth; i += 1, current = current.parentElement) {
      parts.push(
        current.innerText,
        current.textContent,
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

  function isDoubaoAuthOrNavigationControl(node) {
    if (!(node instanceof Element)) return false;
    let current = node;
    for (let depth = 0; current && depth < 4; depth += 1, current = current.parentElement) {
      if (isLargeNonInteractiveContainer(current)) continue;
      const directHint = nativeControlDirectHint(current);
      if (/(?:登录|登陆|注册|验证码|手机号|邮箱|账号|密码|扫码|下载豆包|客户端下载|下载电脑版|电脑版|桌面版|客户端|打开客户端|下载安装|Microsoft\s*Store|Windows|macOS|App\s*Store|应用商店|会员|充值|个人中心)/i.test(directHint)) return true;
      if (/(?:设置|新对话|更多)/i.test(directHint)) return true;
    }
    return false;
  }

  function isLargeNonInteractiveContainer(node) {
    if (!(node instanceof Element)) return false;
    const role = String(node.getAttribute && node.getAttribute('role') || '');
    const tag = String(node.tagName || '').toUpperCase();
    const isInteractive = /^(A|BUTTON|INPUT|TEXTAREA|SELECT|LABEL|SUMMARY)$/.test(tag) || /button|link|menuitem|tab|textbox|combobox|switch|checkbox/i.test(role);
    if (isInteractive) return false;
    const rect = node.getBoundingClientRect?.();
    return Boolean(rect && rect.width >= 240 && rect.height >= 120);
  }

  function nativeControlDirectHint(node) {
    if (!(node instanceof Element)) return '';
    return [
      node.innerText,
      node.textContent,
      node.getAttribute && node.getAttribute('aria-label'),
      node.getAttribute && node.getAttribute('title'),
      node.getAttribute && node.getAttribute('download'),
      node.getAttribute && node.getAttribute('href'),
      node.getAttribute && node.getAttribute('role'),
      node.id,
      node.className,
    ].filter(Boolean).map((item) => String(item)).join(' ').replace(/\s+/g, ' ').trim();
  }

  function resultCardHasVideoSignal(card) {
    if (!card || !card.querySelector) return false;
    return Boolean(card.querySelector('video, canvas, [data-video-url], [data-download-url], a[href*=".mp4"], a[href*=".webm"], a[href*=".mov"], a[href*="video"], [src*=".mp4"], [src*=".webm"], [src*="video"]'));
  }

  function isLikelyNativeVideoDownloadControl(node) {
    if (!(node instanceof HTMLElement)) return false;
    if (isDoubaoAuthOrNavigationControl(node)) return false;
    const text = nativeDownloadControlText(node);
    const href = node instanceof HTMLAnchorElement ? String(node.href || node.getAttribute('href') || '') : '';
    const className = String(node.className || '');
    const id = String(node.id || '');
    const controlHint = `${text} ${href} ${className} ${id}`;
    if (!/(?:下载|保存|download|save)/i.test(controlHint)) return false;
    if (/(?:下载豆包|客户端下载|桌面版|电脑版|插件|扩展|图片|image|screenshot|复制|copy|分享|share|设置|setting)/i.test(controlHint)) return false;
    const card = closestResultCard(node);
    const cardText = String(card && (card.innerText || card.textContent) || '');
    if (!card || card === document.body || card === document.documentElement || isDoubaoAuthOrNavigationControl(card)) return false;
    const hasDirectVideoUrl = isLikelyDirectVideoUrl(href);
    const hasVideoInCard = resultCardHasVideoSignal(card);
    const hasResultText = hasCompletedStatusSignal(cardText) || /下载视频|保存视频|播放|Seedance|无水印/i.test(cardText);
    const hasVideoContext = Boolean(hasDirectVideoUrl || (hasVideoInCard && hasResultText));
    if (!hasVideoContext) return false;
    const rect = node.getBoundingClientRect?.();
    if (rect && (rect.width < 8 || rect.height < 8)) return false;
    return true;
  }

  async function refreshPanel(root) {
    const body = root.querySelector('.dbvd-body');
    showMessage(body, 'Reading current page...');
    try {
      renderItems(body, await requestExtract());
    } catch (error) {
      showMessage(body, error.message, true);
    }
  }

  function renderItems(body, videos) {
    if (!Array.isArray(videos) || videos.length === 0) {
      showMessage(body, 'No usable items found');
      return;
    }
    body.replaceChildren(...videos.map((video, index) => renderItem(video, index)));
  }

  function renderItem(video, index) {
    const item = document.createElement('article');
    item.className = 'dbvd-item';
    const meta = document.createElement('div');
    const name = document.createElement('div');
    const detail = document.createElement('div');
    name.className = 'dbvd-name';
    detail.className = 'dbvd-meta';
    name.textContent = video.title || `Item ${index + 1}`;
    detail.textContent = `${video.width && video.height ? `${video.width}x${video.height}` : 'Unknown size'} / ${video.source || 'unknown'}`;
    meta.append(name, detail);
    item.append(meta, renderButton(video));
    return item;
  }

  function renderButton(video) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'dbvd-action';
    button.textContent = 'Save';
    button.addEventListener('click', () => saveItem(button, video));
    return button;
  }

  async function saveItem(button, video) {
    button.disabled = true;
    button.textContent = '解析中';
    try {
      const resolved = await resolveNoWatermarkForVideo(video, null, 2);
      if (!isWatermarkResolvedCandidate(resolved)) throw new Error('无水印解析未成功，已阻止下载带水印版本');
      const result = await chrome.runtime.sendMessage({ type: 'download-video', video: { ...resolved, requireNoWatermark: true } });
      if (!result || !result.ok) throw new Error(result && result.error || 'Save failed');
      button.textContent = '已开始';
    } catch (error) {
      button.disabled = false;
      button.textContent = '重试';
      const body = document.getElementById(ROOT_ID)?.querySelector('.dbvd-body');
      const message = error && error.message ? error.message : String(error);
      if (body) showMessage(body, message, true);
      showVideoDownloadToast(button, message, true);
      console.error('[page-service-channel]', error);
    }
  }

  async function manualDownloadCurrentPageNoWatermark(button = null, trigger = {}) {
    if (manualNoWatermarkDownloadBusy) return false;
    manualNoWatermarkDownloadBusy = true;
    const root = document.getElementById(ROOT_ID);
    const body = root && root.querySelector('.dbvd-body');
    const setButton = (text, disabled = true) => {
      if (!button) return;
      button.disabled = disabled;
      button.textContent = text;
    };
    try {
      setButton('解析中', true);
      if (body) showMessage(body, '正在解析当前页视频的无水印地址...');
      if (trigger && trigger.rect) await activateTriggeredResultCard(trigger).catch(() => {});
      else await activateLatestResultCard().catch(() => {});
      await sleep(350);
      const videos = await requestExtract().catch(() => extractVisibleResultVideos());
      const candidate = pickManualDownloadCandidate(videos, trigger);
      if (!candidate) throw new Error('当前页没有识别到可下载的视频结果');
      const resolved = await resolveNoWatermarkForVideo(candidate, null, 3);
      if (!isWatermarkResolvedCandidate(resolved)) {
        throw new Error(formatWatermarkResolveError(resolved && resolved.doubaoWatermarkDiagnostic));
      }
      if (body) showMessage(body, '已拿到无水印地址，正在创建下载...');
      const result = await chrome.runtime.sendMessage({
        type: 'download-video',
        video: { ...resolved, requireNoWatermark: true },
      });
      if (!result || !result.ok) throw new Error(result && result.error || '无水印下载失败');
      if (body) showMessage(body, '无水印下载已开始');
      showVideoDownloadToast(button || trigger.element, '无水印下载已开始');
      setButton('已开始', false);
      return true;
    } finally {
      manualNoWatermarkDownloadBusy = false;
      setTimeout(() => {
        if (button) {
          button.disabled = false;
          button.textContent = '无水印下载';
        }
      }, 1800);
    }
  }

  function formatWatermarkResolveError(diagnostic) {
    const reason = String(diagnostic && diagnostic.reason || '');
    const detail = String(diagnostic && (diagnostic.error || diagnostic.firstSource || '') || '');
    if (reason === 'timeout') return '无水印解析超时：请播放一次视频后重试';
    if (reason === 'inject-error') return `无水印解析器注入失败${detail ? `：${detail}` : ''}`;
    if (reason === 'extract-error') return `无水印解析接口失败${detail ? `：${detail}` : ''}`;
    if (reason === 'derived-url-unplayable') return `无水印候选不可播放${detail ? `：${detail}` : ''}`;
    if (reason === 'no-candidate') return '没有拿到无水印候选：请先点视频播放/展开后重试';
    return detail || '无水印解析未成功，已阻止下载带水印版本';
  }

  function pickManualDownloadCandidate(videos, trigger = {}) {
    const list = Array.isArray(videos) ? videos.filter(Boolean) : [];
    if (!list.length) return null;
    const triggerRect = trigger && trigger.rect;
    const triggerKey = String(trigger && trigger.key || '');
    const scored = list.map((video) => {
      let score = Number(video.score || 0);
      if (triggerKey && String(video.overlayTargetKey || '') === triggerKey) score += 8000;
      if (isWatermarkResolvedCandidate(video)) score += 4000;
      if (hasCompletedStatusSignal(String(video.cardText || ''))) score += 1200;
      if (Number(video.rectBottom || 0) > 0) score += Number(video.rectBottom || 0) / 10;
      if (triggerRect) {
        score += scoreRectAffinity(video, triggerRect);
      }
      if (/dom-video|dom-link|network|watermark/i.test(String(video.source || ''))) score += 300;
      return { video, score };
    }).sort((a, b) => b.score - a.score);
    return scored[0]?.video || null;
  }

  function scoreRectAffinity(video, triggerRect) {
    const rect = {
      left: Number(video.rectLeft || 0),
      right: Number(video.rectRight || 0),
      top: Number(video.rectTop || 0),
      bottom: Number(video.rectBottom || 0),
    };
    rect.width = Math.max(0, rect.right - rect.left);
    rect.height = Math.max(0, rect.bottom - rect.top);
    const target = {
      left: Number(triggerRect.left || 0),
      right: Number(triggerRect.right || 0),
      top: Number(triggerRect.top || 0),
      bottom: Number(triggerRect.bottom || 0),
    };
    target.width = Math.max(0, target.right - target.left);
    target.height = Math.max(0, target.bottom - target.top);
    const hasRect = rect.width > 0 && rect.height > 0 && target.width > 0 && target.height > 0;
    if (!hasRect) {
      const dy = Math.abs(Number(video.rectTop || 0) - Number(triggerRect.top || 0));
      return Math.max(0, 1200 - dy);
    }
    const overlapX = Math.max(0, Math.min(rect.right, target.right) - Math.max(rect.left, target.left));
    const overlapY = Math.max(0, Math.min(rect.bottom, target.bottom) - Math.max(rect.top, target.top));
    const overlapArea = overlapX * overlapY;
    const targetArea = Math.max(1, target.width * target.height);
    const rectArea = Math.max(1, rect.width * rect.height);
    const overlapRatio = overlapArea / Math.min(targetArea, rectArea);
    const rectCenterX = (rect.left + rect.right) / 2;
    const rectCenterY = (rect.top + rect.bottom) / 2;
    const targetCenterX = (target.left + target.right) / 2;
    const targetCenterY = (target.top + target.bottom) / 2;
    const distance = Math.hypot(rectCenterX - targetCenterX, rectCenterY - targetCenterY);
    return Math.round(overlapRatio * 3000 + Math.max(0, 1800 - distance));
  }

  if (!globalThis[READY]) {
    globalThis[READY] = true;
    ensureStyle();
    ensurePanel();
    bindNativeDownloadInterceptor();
    bindVideoDownloadOverlays();
    if (!initialAuthOk) {
      const root = document.getElementById(ROOT_ID);
      const body = root && root.querySelector('.dbvd-body');
      if (body) showMessage(body, '扩展已加载，但授权服务暂不可用；请关闭当前豆包窗口后重新生成。', true);
    }
    setTimeout(() => runGenerationTaskLoop(), 800);
    setInterval(() => runGenerationTaskLoop(), 1500);
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (!message || message.type !== 'extract-videos') return false;
      requestExtract().then((videos) => sendResponse({ ok: true, videos })).catch((error) => sendResponse({ ok: false, error: error.message }));
      return true;
    });
  }

  async function runGenerationTaskLoop() {
    if (generationLoopBusy) return;
    if (activeGenerationTaskId) {
      const latest = await getTaskStatus(activeGenerationTaskId).catch(() => null);
      if (latest && !['downloaded', 'completed', 'failed', 'cancelled', 'manual'].includes(String(latest.status || ''))) return;
      activeGenerationTaskId = '';
    }
    generationLoopBusy = true;
    try {
      const assignedTaskId = getAssignedTaskId();
      const assignedAccountId = getAssignedAccountId();
      if (!assignedTaskId || !assignedAccountId) {
        showIdleTaskMessage();
        return;
      }
      const task = await claimTask(assignedTaskId);
      if (!task) return;
      setAutomationProgress(task, 'claimed', `已领取：${task.title || '分镜视频'}`, {
        status: task.status || 'claimed',
        progress: 10,
      });
      await submitGenerationTask(task);
    } catch (error) {
      console.warn('[page-service-channel] generation task skipped:', error);
      const message = error && error.message ? error.message : String(error);
      if (isLocalBridgeDisconnect(error)) {
        const id = activeGenerationTaskId || getAssignedTaskId();
        if (!id) {
          activeGenerationTaskId = '';
          showIdleTaskMessage();
          return;
        }
        const latest = id ? await getTaskStatus(id).catch(() => null) : null;
        if (!latest || ['downloaded', 'completed', 'failed', 'cancelled', 'manual'].includes(String(latest.status || ''))) {
          activeGenerationTaskId = '';
          setAutomationProgress(latest || automationPanelState?.task || null, latest?.stage || latest?.status || 'waiting-result', latest && ['downloaded', 'completed'].includes(String(latest.status || '')) ? VMO_TASK_COMPLETED_MESSAGE : VMO_LOCAL_BRIDGE_WAIT_MESSAGE, {
            status: latest?.status || 'waiting',
            tone: latest && ['downloaded', 'completed'].includes(String(latest.status || '')) ? 'ok' : 'warn',
            open: true,
          });
          return;
        }
      }
      if (!/任务已切换到其它豆包账号/.test(message)) {
        setAutomationProgress(automationPanelState?.task || null, 'failed', message, {
          status: 'failed',
          progress: 100,
          tone: 'bad',
          open: true,
        });
      }
    } finally {
      generationLoopBusy = false;
    }
  }

  async function assertTaskActive(task, stage = '') {
    if (!task || !task.id) return task;
    const latest = await getTaskStatus(task.id);
    if (latest && latest.status === 'cancelled') throw new Error(latest.error || '任务已停止');
    const runnerAccountId = getAssignedAccountId();
    if (latest && runnerAccountId && latest.accountId && String(latest.accountId) !== String(runnerAccountId)) {
      throw new Error('任务已切换到其它豆包账号');
    }
    await updateTask({ id: task.id, automationHeartbeatAt: Date.now(), stage }).catch(() => {});
    return latest || task;
  }

  async function syncDoubaoPageStatus(task, stage = '') {
    if (!task || !task.id) return null;
    const now = Date.now();
    if (now - lastDoubaoStatusSyncAt < DOUBAO_STATUS_SYNC_INTERVAL_MS) return null;
    lastDoubaoStatusSyncAt = now;
    const pageStatus = parseDoubaoPageStatus(task);
    const patch = {
      id: task.id,
      automationHeartbeatAt: now,
      stage: pageStatus.stage || stage,
      doubaoPageStatus: pageStatus.status,
      doubaoPageMessage: pageStatus.message,
      doubaoProgress: pageStatus.progress,
      doubaoCreditCost: pageStatus.creditCost,
      doubaoCreditRemaining: pageStatus.creditRemaining,
      doubaoEstimatedWait: pageStatus.estimatedWait,
      doubaoModel: pageStatus.model,
      doubaoFailureReason: pageStatus.failureReason,
      doubaoSignals: pageStatus.signals,
      doubaoLastText: pageStatus.sample,
    };
    await updateTask(patch).catch(() => {});
    if (pageStatus.message) {
      setAutomationProgress(task, pageStatus.stage || stage || 'waiting-result', pageStatus.message, {
        status: pageStatus.status === 'failed-signal' ? 'failed' : (task.status || 'submitted'),
        progress: pageStatus.progress || progressForStage(pageStatus.stage || stage || 'waiting-result', task.status || 'submitted'),
        tone: pageStatus.status === 'failed-signal' ? 'bad' : undefined,
      });
    }
    return pageStatus;
  }

  function hasActiveDoubaoGenerationSignal(pageStatus) {
    if (!pageStatus) return false;
    if (['generating', 'queued-signal', 'duration-confirm', 'completed-signal'].includes(pageStatus.status)) return true;
    if (pageStatus.progress && pageStatus.progress > 0) return true;
    if (pageStatus.creditCost && pageStatus.creditCost > 0) return true;
    return false;
  }

  async function preserveActiveGenerationOnError(task, message, stage = 'waiting-result') {
    if (!task || !task.id) return false;
    if (getHardGenerationFailureMessage() || DOUBAO_HARD_FAILURE_PATTERN.test(String(message || ''))) return false;
    let pageStatus = null;
    try {
      pageStatus = parseDoubaoPageStatus(task);
    } catch (error) {}
    if (!hasActiveDoubaoGenerationSignal(pageStatus)) return false;
    const patch = {
      id: task.id,
      status: 'submitted',
      error: '',
      automationHeartbeatAt: Date.now(),
      stage: pageStatus.stage || stage,
      doubaoPageStatus: pageStatus.status,
      doubaoPageMessage: pageStatus.message || String(message || ''),
      doubaoProgress: pageStatus.progress,
      doubaoCreditCost: pageStatus.creditCost,
      doubaoCreditRemaining: pageStatus.creditRemaining,
      doubaoEstimatedWait: pageStatus.estimatedWait,
      doubaoModel: pageStatus.model,
      doubaoFailureReason: pageStatus.failureReason,
      doubaoSignals: pageStatus.signals,
      doubaoLastText: pageStatus.sample,
    };
    await updateTask(patch).catch(() => {});
    if (pageStatus.message) {
      setAutomationProgress(task, pageStatus.stage || stage || 'waiting-result', pageStatus.message, {
        status: task.status || 'submitted',
        progress: pageStatus.progress || progressForStage(pageStatus.stage || stage || 'waiting-result', task.status || 'submitted'),
        tone: 'warn',
      });
    }
    return true;
  }

  function isLocalBridgeDisconnect(error) {
    const message = String(error && error.message || error || '');
    return /Failed to fetch|NetworkError|Load failed|ERR_CONNECTION|ECONNREFUSED|fetch/i.test(message);
  }

  function parseDoubaoPageStatus(task = null) {
    const raw = statusSourceTextForTask(task);
    const text = raw.replace(/\s+/g, ' ').trim();
    const normalized = normalizeStatusText(text);
    const sourceText = normalized.slice(-3200);
    const progressMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.progress);
    const waitMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.wait);
    const costMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.creditCost);
    const remainMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.creditRemaining);
    const modelMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.model);
    const failureSourceText = currentFailureSourceText(task, sourceText);
    const failedMatch = firstStatusMatch(failureSourceText, DOUBAO_STATUS_PATTERNS.failed);
    const completedMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.completed);
    const completedKeywordIndex = latestCompletedStatusIndex(sourceText);
    const guard = task && task.resultGuard || {};
    const completed = (guard.submittedAt || guard.anchorBottom || guard.maxMessageIdBefore)
      ? detectGenerationCompletedNotice(task)
      : Boolean(hasCompletedStatusSignal(sourceText) || completedMatch);
    const failedIndex = failedMatch ? failedMatch.index : -1;
    const failureCompletedIndex = latestCompletedStatusIndex(failureSourceText);
    const completedIndex = Math.max(completedMatch ? completedMatch.index : -1, completedKeywordIndex);
    const confirming = detectDurationAdjustQuestion(task);
    const queued = hasAnyStatusKeyword(sourceText, DOUBAO_STATUS_KEYWORDS.queued);
    const generating = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.generating) || hasAnyStatusKeyword(sourceText, DOUBAO_STATUS_KEYWORDS.generating) || queued;
    const progressNumber = progressMatch ? firstNumber(progressMatch) : null;
    const progress = progressNumber !== null ? Math.max(0, Math.min(100, progressNumber)) : (completed ? 95 : (generating ? 35 : 0));
    const creditCost = costMatch ? firstNumber(costMatch) : null;
    const creditRemaining = remainMatch ? firstNumber(remainMatch) : null;
    const estimatedWait = waitMatch ? cleanStatusValue(waitMatch[1]) : '';
    const model = modelMatch ? cleanStatusValue(modelMatch[1]) : '';
    let status = 'idle';
    let stage = '';
    let message = '';
    if (failedMatch && failedIndex >= failureCompletedIndex) {
      status = 'failed-signal';
      stage = 'doubao-failure-signal';
      message = cleanStatusValue(failedMatch[0]) || '豆包提示生成失败';
    } else if (completed) {
      status = 'completed-signal';
      stage = 'doubao-completed';
      message = '豆包已提示视频生成完成，正在抓取结果';
    } else if (confirming) {
      status = 'duration-confirm';
      stage = 'doubao-duration-confirm';
      message = '豆包要求确认 ' + taskTargetDurationSeconds(task || {}) + ' 秒生成，正在自动回复';
    } else if (generating) {
      status = queued && !hasAnyStatusKeyword(sourceText, DOUBAO_STATUS_KEYWORDS.generating) ? 'queued-signal' : 'generating';
      stage = status === 'queued-signal' ? 'doubao-queued' : 'doubao-generating';
      message = buildDoubaoGeneratingMessage({ creditCost, creditRemaining, estimatedWait, model, progress, queued: status === 'queued-signal' });
    }
    const signals = {
      queued: Boolean(queued),
      generating: Boolean(generating),
      completed: Boolean(completed),
      failed: Boolean(failedMatch),
      confirming: Boolean(confirming),
      creditCost: Boolean(costMatch),
      creditRemaining: Boolean(remainMatch),
      estimatedWait: Boolean(waitMatch),
      progress: Boolean(progressMatch),
      model: Boolean(modelMatch),
    };
    return {
      status,
      stage,
      message,
      progress,
      creditCost,
      creditRemaining,
      estimatedWait,
      model,
      failureReason: failedMatch ? cleanStatusValue(failedMatch[0]) : '',
      signals,
      sample: sourceText.slice(-360),
    };
  }

  function statusSourceTextForTask(task = null, limit = 3200) {
    const pageText = getPageTextWithoutPanel();
    const guard = task && task.resultGuard || {};
    const hasGuard = Boolean(guard.submittedAt || guard.anchorBottom || guard.maxMessageIdBefore || (guard.promptProbe || []).length);
    if (!hasGuard) return pageText.slice(-limit);
    const parts = [];
    const add = (value) => {
      const text = normalizeStatusText(value);
      if (text && !parts.includes(text)) parts.push(text);
    };
    for (const video of extractVisibleResultVideos()) {
      if (scoreTaskVideoCandidate(video, new Set(), guard) > -Infinity) add(video.cardText || '');
    }
    const probes = Array.isArray(guard.promptProbe) ? guard.promptProbe : [];
    const normalizedPage = normalizeStatusText(pageText);
    let latestProbeIndex = -1;
    for (const probe of probes) {
      const key = normalizeStatusText(probe).slice(0, 80);
      if (!key || key.length < 12) continue;
      latestProbeIndex = Math.max(latestProbeIndex, normalizedPage.lastIndexOf(key));
    }
    if (latestProbeIndex >= 0) add(normalizedPage.slice(latestProbeIndex, latestProbeIndex + limit));
    const anchorBottom = Number(guard.anchorBottom || 0);
    if (anchorBottom) {
      const nearTexts = [...document.querySelectorAll('article, [data-testid], [role="article"], [class*="message"], [class*="chat"], [class*="card"], [class*="result"], video')]
        .filter((node) => node instanceof HTMLElement && node.offsetParent !== null && !node.closest(`#${ROOT_ID}`))
        .map((node) => {
          const root = closestResultCard(node) || node;
          const rect = root.getBoundingClientRect?.();
          const text = String(root.innerText || root.textContent || '').replace(/\s+/g, ' ').trim();
          return { rect, text };
        })
        .filter((item) => item.rect && item.text && item.rect.bottom >= anchorBottom - 180)
        .sort((a, b) => a.rect.top - b.rect.top)
        .slice(-8)
        .map((item) => item.text);
      nearTexts.forEach(add);
    }
    add(pageText.slice(-Math.min(limit, 1200)));
    return parts.join(' ').slice(-limit);
  }

  function currentFailureSourceText(task = null, fallbackText = '') {
    const guard = task && task.resultGuard || {};
    const hasGuard = Boolean(guard.submittedAt || guard.anchorBottom || guard.maxMessageIdBefore || (guard.promptProbe || []).length);
    if (!hasGuard) return fallbackText || getPageTextWithoutPanel();
    const parts = [];
    const add = (value) => {
      const text = normalizeStatusText(value);
      if (text && !parts.includes(text)) parts.push(text);
    };
    for (const video of extractVisibleResultVideos()) {
      if (scoreTaskVideoCandidate(video, new Set(), guard) > -Infinity) add(video.cardText || '');
    }
    const source = statusSourceTextForTask(task, 2400);
    const promptEchoLike = Array.isArray(guard.promptProbe) && guard.promptProbe.some((probe) => {
      const key = normalizeStatusText(probe).slice(0, 80);
      return key && key.length >= 12 && source.includes(key);
    });
    if (!promptEchoLike) add(source);
    return parts.join(' ') || '';
  }

  function normalizeStatusText(value) {
    return String(value || '')
      .replace(/\u00a0/g, ' ')
      .replace(/[，,]\s*/g, '，')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function completedStatusMatches(text) {
    const value = normalizeStatusText(text);
    const patterns = [
      /你的视频生成好(?:啦|了)?(?!后|之后|以后)/gi,
      /视频生成好(?:啦|了)?(?!后|之后|以后)/gi,
      /生成好(?:啦|了)(?!后|之后|以后)/gi,
      /生成完成(?!后|之后|以后)/gi,
      /视频已生成(?!后|之后|以后)/gi,
      /已生成视频(?!后|之后|以后)/gi,
      /创作完成(?!后|之后|以后)/gi,
      /下载视频/gi,
      /保存视频/gi,
    ];
    const matches = [];
    for (const pattern of patterns) {
      pattern.lastIndex = 0;
      let match = null;
      while ((match = pattern.exec(value))) {
        matches.push({ index: match.index, text: match[0] });
      }
    }
    return matches.sort((a, b) => a.index - b.index);
  }

  function hasCompletedStatusSignal(text) {
    return completedStatusMatches(text).length > 0;
  }

  function latestCompletedStatusIndex(text) {
    const matches = completedStatusMatches(text);
    return matches.length ? matches[matches.length - 1].index : -1;
  }

  function firstStatusMatch(text, patterns) {
    for (const pattern of patterns || []) {
      const match = String(text || '').match(pattern);
      if (match) return match;
    }
    return null;
  }

  function hasAnyStatusKeyword(text, keywords) {
    const value = String(text || '').toLowerCase();
    return (keywords || []).some((item) => value.includes(String(item || '').toLowerCase()));
  }

  function lastStatusKeywordIndex(text, keywords) {
    const value = String(text || '').toLowerCase();
    let latest = -1;
    for (const item of keywords || []) {
      const index = value.lastIndexOf(String(item || '').toLowerCase());
      if (index > latest) latest = index;
    }
    return latest;
  }

  function firstNumber(match) {
    for (const item of Array.from(match || []).slice(1)) {
      if (item === undefined || item === null || item === '') continue;
      const value = Number(String(item).match(/[0-9.]+/)?.[0] || NaN);
      if (Number.isFinite(value)) return value;
    }
    return null;
  }

  function cleanStatusValue(value) {
    return String(value || '').replace(/\s+/g, '').replace(/[。；;，,]+$/g, '').trim();
  }

  function buildDoubaoGeneratingMessage(info = {}) {
    const parts = [info.queued ? '豆包排队中' : '豆包正在生成'];
    if (info.model) parts.push(info.model);
    if (info.creditCost !== null && info.creditCost !== undefined) parts.push(`消耗${info.creditCost}额度`);
    if (info.creditRemaining !== null && info.creditRemaining !== undefined) parts.push(`剩余${info.creditRemaining}额度`);
    if (info.estimatedWait) parts.push(`预计${info.estimatedWait}`);
    if (info.progress && info.progress > 0 && info.progress < 95) parts.push(`${info.progress}%`);
    return parts.join(' · ');
  }

  async function submitGenerationTask(task) {
    activeGenerationTaskId = task && task.id || '';
    try {
      task = await assertTaskActive(task, 'claimed');
      setAutomationProgress(task, 'claimed', `已领取：${task.title || '分镜视频'}`, {
        status: task.status || 'claimed',
        progress: 10,
      });
      const status = String(task.status || '');
      let beforeKeys = new Set(Array.isArray(task.beforeVideoKeys) ? task.beforeVideoKeys : []);
      let beforeFailureCount = countGenerationFailures(task);
      if (isSemiAutoTask(task) && ['submitted', 'downloading'].includes(status)) {
        const message = '半自动模式：当前任务已交给用户手动处理，VMO 不再自动轮询或下载';
        await updateTask({
          id: task.id,
          status: 'manual',
          stage: 'semi-auto-ready',
          doubaoPageMessage: message,
          automationHeartbeatAt: Date.now(),
        }).catch(() => {});
        setAutomationProgress(task, 'semi-auto-ready', message, {
          status: 'manual',
          progress: 100,
          tone: 'warn',
        });
        activeGenerationTaskId = '';
        return;
      }
      if (['submitted', 'downloading'].includes(status)) {
        setAutomationProgress(task, 'waiting-result', `继续回收：${task.title || '分镜视频'}`, {
          status: task.status || 'submitted',
          progress: 72,
          tone: 'warn',
        });
        await waitAndDownloadGeneratedVideo(task, beforeKeys, beforeFailureCount);
        return;
      }
      setAutomationProgress(task, 'surface-ready', '正在确认豆包视频生成页面', {
        status: task.status || 'claimed',
        progress: 16,
      });
      await ensureVideoCreationSurface();
      await assertVideoSurfaceForSubmit(task, 'surface-ready');
      setAutomationProgress(task, 'surface-ready', '视频生成页面已就绪', {
        status: task.status || 'claimed',
        progress: 18,
      });
      await syncDoubaoPageStatus(task, 'surface-ready');
      await waitForReadyInput();
      let input = findPromptInput();
      if (!input) throw new Error('未找到豆包提示词输入框');
      beforeKeys = await readCurrentVideoKeys();
      beforeFailureCount = 0;
      await assertTaskActive(task, 'uploading');
      setAutomationProgress(task, 'uploading', '正在上传参考图', {
        status: task.status || 'claimed',
        progress: 28,
      });
      const imageResult = await tryUploadTaskImage(task, input);
      if (imageResult && imageResult.hasImage && !imageResult.uploaded) throw new Error(imageResult.error || '首帧图未自动上传');
      await assertVideoSurfaceForSubmit(task, 'after-upload');
      setAutomationProgress(task, 'after-upload', imageResult && imageResult.hasImage ? '参考图已上传' : '无需上传参考图', {
        status: task.status || 'claimed',
        progress: 38,
      });
      input = await waitForWritablePromptInput(input);
      if (!input) throw new Error('上传参考图后未找到豆包提示词输入框');
      const promptText = buildPromptText(task);
      await assertTaskActive(task, 'prompting');
      await assertVideoSurfaceForSubmit(task, 'before-prompt');
      setAutomationProgress(task, 'prompting', '正在写入视频提示词', {
        status: task.status || 'claimed',
        progress: 46,
      });
      await setPromptValue(input, promptText);
      input = await waitForWritablePromptInput(input);
      await waitForPromptValue(input, promptText);
      input = await ensurePromptReadyForSubmit(input, promptText);
      const resultGuard = captureSubmissionAnchor(input, promptText, beforeKeys);
      resultGuard.vmoTaskId = task.id;
      await resetNetworkCaptureForTask(task, resultGuard);
      showPreparedTask(task, imageResult);
      await updateTask({ id: task.id, status: 'prepared', beforeVideoKeys: [...beforeKeys], resultGuard });
      await assertTaskActive(task, 'prepared');
      if (isSemiAutoTask(task)) {
        const message = '半自动模式：已上传参考图并填入提示词，请手动设置时长、比例并点击生成';
        await updateTask({
          id: task.id,
          status: 'manual',
          stage: 'semi-auto-ready',
          beforeVideoKeys: [...beforeKeys],
          resultGuard,
          doubaoPageMessage: message,
          automationHeartbeatAt: Date.now(),
        });
        setAutomationProgress({ ...task, status: 'manual', resultGuard }, 'semi-auto-ready', message, {
          status: 'manual',
          progress: 100,
          tone: 'warn',
        });
        activeGenerationTaskId = '';
        return;
      }
      await assertVideoSurfaceForSubmit(task, 'before-submit');
      setAutomationProgress(task, 'before-submit', '准备提交豆包生成任务', {
        status: 'prepared',
        progress: 62,
      });
      await ensureFixedDurationSelected(task);
      await submitPrompt(input, promptText);
      await waitForSubmissionAccepted(input, beforeFailureCount, task);
      await syncDoubaoPageStatus(task, 'submitted');
      await updateTask({ id: task.id, status: 'submitted', resultGuard });
      setAutomationProgress({ ...task, status: 'submitted', resultGuard }, 'submitted', `已提交：${task.title || '分镜视频'}`, {
        status: 'submitted',
        progress: 68,
      });
      try {
        await waitAndDownloadGeneratedVideo({ ...task, resultGuard }, beforeKeys, beforeFailureCount);
      } catch (error) {
        if (/任务已切换到其它豆包账号/.test(error && error.message || '')) return;
        const latestAfterDownloadError = task && task.id ? await getTaskStatus(task.id).catch(() => null) : null;
        if (latestAfterDownloadError && ['downloaded', 'completed'].includes(String(latestAfterDownloadError.status || ''))) {
          activeGenerationTaskId = '';
          setAutomationProgress(latestAfterDownloadError, latestAfterDownloadError.stage || 'downloaded', '豆包任务已完成', {
            status: latestAfterDownloadError.status || 'downloaded',
            progress: 100,
            tone: 'ok',
          });
          return;
        }
        if (await preserveActiveGenerationOnError({ ...task, resultGuard }, error && error.message || error, 'waiting-result')) {
          activeGenerationTaskId = '';
          return;
        }
        await updateTask({ id: task.id, status: 'failed', error: error.message }).catch(() => {});
        setAutomationProgress(task, 'failed', error.message || '豆包视频自动回收失败', {
          status: 'failed',
          progress: 100,
          tone: 'bad',
        });
      }
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      const retryable = /不在 AI 创作视频页|等待豆包页面|页面加载|暂不可用/.test(message);
      if (isLocalBridgeDisconnect(error)) {
        const id = activeGenerationTaskId || (task && task.id) || getAssignedTaskId();
        const latest = id ? await getTaskStatus(id).catch(() => null) : null;
        if (!latest || ['downloaded', 'completed', 'failed', 'cancelled', 'manual'].includes(String(latest.status || ''))) {
          activeGenerationTaskId = '';
          setAutomationProgress(latest || task, latest?.stage || latest?.status || 'waiting-result', latest && ['downloaded', 'completed'].includes(String(latest.status || '')) ? VMO_TASK_COMPLETED_MESSAGE : VMO_LOCAL_BRIDGE_WAIT_MESSAGE, {
            status: latest?.status || 'waiting',
            tone: latest && ['downloaded', 'completed'].includes(String(latest.status || '')) ? 'ok' : 'warn',
            open: true,
          });
          return;
        }
      }
      if (!retryable && await preserveActiveGenerationOnError(task, message, 'waiting-result')) {
        return;
      }
      await updateTask({ id: task.id, status: retryable ? 'queued' : 'failed', error: message }).catch(() => {});
      if (retryable) {
        setAutomationProgress(task, 'queued', `等待豆包页面可发送：${task.title || '分镜视频'}`, {
          status: 'queued',
          progress: 5,
          tone: 'warn',
        });
      } else {
        setAutomationProgress(task, 'failed', `豆包自动化失败：${message}`, {
          status: 'failed',
          progress: 100,
          tone: 'bad',
        });
      }
      throw error;
    } finally {
      const latest = activeGenerationTaskId ? await getTaskStatus(activeGenerationTaskId).catch(() => null) : null;
      if (!latest || ['downloaded', 'completed', 'failed', 'cancelled', 'manual'].includes(String(latest.status || ''))) {
        activeGenerationTaskId = '';
      }
    }
  }

  async function readCurrentVideoKeys() {
    try {
      const videos = await requestExtract();
      return new Set((Array.isArray(videos) ? videos : []).flatMap((video) => [...videoIdentityKeys(video), ...legacyVideoKeys(video)]).filter(Boolean));
    } catch {
      return new Set();
    }
  }

  async function waitAndDownloadGeneratedVideo(task, beforeKeys, beforeFailureCount) {
    const deadline = Date.now() + 30 * 60 * 1000;
    let lastError = null;
    let retryCount = 0;
    let observedFailureCount = beforeFailureCount || 0;
    let durationAdjustAnswered = false;
    let completedSeenAt = 0;
    let lastRefreshAt = 0;
    let lastActivationAt = 0;
    let stableCandidateKey = '';
    let stableCandidateSeen = 0;
    let downloadAttempts = 0;
    const submittedAt = Number(task?.resultGuard?.submittedAt || 0) || Date.now();
    setAutomationProgress(task, 'waiting-result', `等待豆包生成：${task.title || '分镜视频'}`, {
      status: task.status || 'submitted',
      progress: 72,
      tone: 'warn',
    });
    while (Date.now() < deadline) {
      await sleep(completedSeenAt ? 2000 : 8000);
      await assertTaskActive(task, 'waiting-result');
      const pageStatus = await syncDoubaoPageStatus(task, 'waiting-result') || parseDoubaoPageStatus(task);
      await failOnLowerDurationCap(task);
      if (detectDurationAdjustQuestion(task)) {
        if (!durationAdjustAnswered) {
          durationAdjustAnswered = true;
          await answerDurationAdjustQuestion(task);
        }
        continue;
      }
      const llmDecision = await requestLlmAutomationDecision(task, 'waiting-result', pageStatus, {
        beforeFailureCount: observedFailureCount,
        completedSeenAt,
        lastError: lastError && lastError.message || '',
        downloadAttempts,
      });
      const llmApplied = await applyLlmAutomationDecision(task, llmDecision, {
        pageStatus,
        beforeFailureCount: observedFailureCount,
        completedSeenAt,
      });
      if (llmApplied) {
        if (llmApplied === 'activate') completedSeenAt ||= Date.now();
        if (llmApplied === 'refresh') lastRefreshAt = Date.now();
        continue;
      }
      if ((pageStatus && pageStatus.status === 'completed-signal' && detectGenerationCompletedNotice(task)) || detectGenerationCompletedNotice(task)) {
        completedSeenAt ||= Date.now();
        const now = Date.now();
        await updateTask({ id: task.id, status: 'submitted', stage: 'completed-card-detected', doubaoProgress: 95, doubaoPageStatus: 'completed-signal', doubaoPageMessage: '豆包已提示视频生成完成，正在抓取结果' }).catch(() => {});
        setAutomationProgress(task, 'completed-card-detected', '豆包已提示视频生成完成，正在抓取结果', {
          status: 'submitted',
          progress: 92,
        });
        if (now - lastActivationAt > 10000) {
          lastActivationAt = now;
          await activateLatestResultCard();
        }
      }
      const failure = getGenerationFailureAfter(observedFailureCount, task);
      if (failure) {
        if (!failure.hard && hasActiveDoubaoGenerationSignal(pageStatus)) {
          observedFailureCount = failure.count;
          continue;
        }
        observedFailureCount = failure.count;
        if (failure.hard) {
          throw new Error(failure.message || '豆包拒绝当前素材/提示词');
        }
        if (retryCount < 2) {
          retryCount += 1;
          setAutomationProgress(task, 'prompting', `${failure.message}，已重新填充 ${retryCount}/2，请手动重试：${task.title || '分镜视频'}`, {
            status: 'submitted',
            progress: 50,
            tone: 'warn',
          });
          await resubmitGenerationTask(task, observedFailureCount);
          continue;
        }
        throw new Error(`${failure.message}，已重新填充 2 次仍失败`);
      }
      let videos = [];
      try {
        videos = await requestExtract(task);
      } catch (error) {
        lastError = error;
        continue;
      }
      let nextVideo = await pickNewVideoWithLlm(videos, beforeKeys, task, pageStatus, {
        stage: 'candidate-binding',
        beforeFailureCount: observedFailureCount,
        completedSeenAt,
      });
      if (!nextVideo && completedSeenAt) {
        await activateLatestResultCard();
        try {
          videos = await requestExtract(task);
          nextVideo = await pickNewVideoWithLlm(videos, beforeKeys, task, pageStatus, {
            stage: 'candidate-binding-after-activate',
            beforeFailureCount: observedFailureCount,
            completedSeenAt,
          });
        } catch (error) {
          lastError = error;
        }
      }
      if (!nextVideo) {
        const now = Date.now();
        const shouldRefreshAfterCompleted = completedSeenAt && now - completedSeenAt > 30000;
        const shouldRefreshActive = hasActiveDoubaoGenerationSignal(pageStatus) && now - submittedAt > 90_000;
        if ((shouldRefreshAfterCompleted || shouldRefreshActive) && now - lastRefreshAt > 45_000) {
          lastRefreshAt = now;
          const reason = shouldRefreshAfterCompleted ? 'completed-without-result' : 'active-generation-stale';
          setAutomationProgress(task, 'refreshing-result-page', shouldRefreshAfterCompleted ? '已检测到豆包完成，刷新页面重新读取结果' : '豆包生成页长时间未更新，刷新当前任务页读取结果', {
            status: 'submitted',
            progress: 88,
            tone: 'warn',
          });
          await updateTask({ id: task.id, stage: 'refreshing-result-page', doubaoRefreshReason: reason, doubaoLastRefreshAt: now }).catch(() => {});
          if (!(await devtoolsRefreshTaskPage(task.id, reason))) {
            location.reload();
          }
          await sleep(8000);
        }
        continue;
      }
      const nextKey = videoStableKey(nextVideo);
      if (nextKey && nextKey === stableCandidateKey) stableCandidateSeen += 1;
      else {
        stableCandidateKey = nextKey;
        stableCandidateSeen = 1;
      }
      if (completedSeenAt && stableCandidateSeen < 2) {
        await updateTask({ id: task.id, status: 'submitted', stage: 'result-candidate-confirming', doubaoResultKey: nextKey || '', assetUrl: nextVideo.assetUrl || nextVideo.backupUrl || '', backupUrl: nextVideo.backupUrl || '' }).catch(() => {});
        setAutomationProgress(task, 'result-candidate-confirming', '正在确认视频归属，避免抓到历史结果', {
          status: 'submitted',
          progress: 95,
        });
        await sleep(2500);
        continue;
      }
      await assertTaskActive(task, 'downloading');
      nextVideo = await resolveNoWatermarkForVideo(nextVideo, task, 2);
      let selectedPatch = buildSelectedVideoPatch(nextVideo);
      if (DOUBAO_REQUIRE_NO_WATERMARK_DOWNLOAD && !isWatermarkResolvedCandidate(nextVideo)) {
        await updateTask({
          id: task.id,
          status: 'submitted',
          stage: 'watermark-unresolved',
          doubaoPageMessage: '已抓到视频，但无水印解析未成功，暂不下载带水印版本',
          assetUrl: nextVideo.assetUrl || nextVideo.backupUrl || '',
          backupUrl: nextVideo.backupUrl || '',
          ...selectedPatch,
        }).catch(() => {});
        setAutomationProgress(task, 'watermark-unresolved', '已抓到视频，正在等待无水印解析；不会下载带水印版本', {
          status: 'submitted',
          progress: 96,
          tone: 'warn',
        });
        lastError = new Error(selectedPatch.doubaoWatermarkDiagnostic?.error || '无水印解析未成功');
        stableCandidateKey = '';
        stableCandidateSeen = 0;
        await activateLatestResultCard();
        await sleep(5000);
        continue;
      }
      await updateTask({ id: task.id, status: 'downloading', assetUrl: nextVideo.assetUrl || nextVideo.backupUrl || '', backupUrl: nextVideo.backupUrl || '', ...selectedPatch });
      setAutomationProgress(task, 'downloading', isWatermarkResolvedCandidate(nextVideo) ? '已绑定无水印结果，正在下载视频' : '已绑定本次结果，正在下载视频', {
        status: 'downloading',
        progress: 97,
      });
      try {
        const downloadVideoPayload = { ...nextVideo, requireNoWatermark: DOUBAO_REQUIRE_NO_WATERMARK_DOWNLOAD };
        const result = await chrome.runtime.sendMessage({ type: 'download-video', video: downloadVideoPayload, taskId: task.id });
        if (!result || !result.ok) throw new Error(result && result.error || '????????');
        selectedPatch = buildSelectedVideoPatch(nextVideo);
        await updateTask({ id: task.id, status: 'downloaded', downloadId: result.downloadId || '', downloadFilename: result.filename || '', assetUrl: result.url || nextVideo.assetUrl || nextVideo.backupUrl || '', backupUrl: nextVideo.backupUrl || '', ...selectedPatch });
        setAutomationProgress(task, 'downloaded', `下载完成：${task.title || '分镜视频'}`, {
          status: 'downloaded',
          progress: 100,
          tone: 'ok',
        });
        return;
      } catch (error) {
        lastError = error;
        downloadAttempts += 1;
        await updateTask({ id: task.id, status: 'submitted', stage: 'download-retry-waiting', error: '', doubaoDownloadError: String(error && error.message || error), assetUrl: nextVideo.assetUrl || nextVideo.backupUrl || '', backupUrl: nextVideo.backupUrl || '', ...selectedPatch }).catch(() => {});
        if (downloadAttempts >= 4) throw error;
        setAutomationProgress(task, 'download-retry-waiting', `下载失败，准备重试 ${downloadAttempts}/4`, {
          status: 'submitted',
          progress: 90,
          tone: 'warn',
        });
        stableCandidateKey = '';
        stableCandidateSeen = 0;
        await activateLatestResultCard();
        if (downloadAttempts >= 2 && Date.now() - lastRefreshAt > 45000) {
          lastRefreshAt = Date.now();
          if (!(await devtoolsRefreshTaskPage(task.id, 'download-url-retry'))) location.reload();
          await sleep(8000);
        } else {
          await sleep(5000);
        }
      }
    }
    throw new Error(lastError && lastError.message ? lastError.message : '豆包视频生成超时，未检测到可下载视频');
  }

  function detectGenerationFailure() {
    const taskId = getAssignedTaskId();
    return countGenerationFailures(taskId ? { id: taskId } : null) > 0;
  }

  function getAssignedTaskId() {
    try {
      const url = new URL(location.href);
      const fromUrl = url.searchParams.get('dbvdTaskId') || url.hash.match(/dbvdTaskId=([^&]+)/)?.[1] || '';
      if (fromUrl) {
        sessionStorage.setItem('dbvdTaskId', decodeURIComponent(fromUrl));
        return decodeURIComponent(fromUrl);
      }
      return sessionStorage.getItem('dbvdTaskId') || '';
    } catch {
      return sessionStorage.getItem('dbvdTaskId') || '';
    }
  }

  function captureSubmissionAnchor(input, promptText = '', beforeKeys = null) {
    const freshInput = findPromptInput() || input;
    const inputRect = freshInput ? freshInput.getBoundingClientRect() : null;
    const composerRect = freshInput ? composerRectFor(freshInput) : null;
    const rect = composerRect || inputRect;
    const knownResultKeysBefore = readKnownDoubaoResultKeys();
    if (beforeKeys && beforeKeys.forEach) beforeKeys.forEach((key) => {
      const text = String(key || '').trim();
      if (text && !knownResultKeysBefore.includes(text)) knownResultKeysBefore.push(text);
    });
    return {
      submittedAt: Date.now(),
      anchorTop: Math.round(rect ? rect.top : 0),
      anchorBottom: Math.round(rect ? rect.bottom : 0),
      viewportHeight: Math.round(window.innerHeight || 0),
      maxMessageIdBefore: readKnownMaxMessageId(),
      knownResultKeysBefore: knownResultKeysBefore.slice(-1000),
      knownInternalIdsBefore: knownResultKeysBefore.slice(-1000),
      promptFingerprint: promptFingerprint(promptText),
      promptProbe: buildNetworkPromptProbe(promptText),
      href: location.href,
    };
  }

  function readKnownMaxMessageId() {
    let maxId = '';
    const seen = typeof WeakSet !== 'undefined' ? new WeakSet() : null;
    const visitValue = (value, depth = 0) => {
      if (depth > 8 || value === null || value === undefined) return;
      if (typeof value === 'string' || typeof value === 'number') {
        maxId = maxNumericMessageId(maxId, value);
        return;
      }
      if (typeof value !== 'object') return;
      if (seen) {
        if (seen.has(value)) return;
        seen.add(value);
      }
      if (Array.isArray(value)) {
        value.slice(-300).forEach((item) => visitValue(item, depth + 1));
        return;
      }
      for (const [key, item] of Object.entries(value).slice(-600)) {
        if (/message.?id|item.?id|conversation.?id|chat.?id|id$/i.test(key)) visitValue(item, depth + 1);
        else if (depth < 3 && item && typeof item === 'object') visitValue(item, depth + 1);
      }
    };
    visitValue(globalThis.__MODERN_ROUTER_DATA);
    for (const node of Array.from(document.querySelectorAll('*')).slice(-2000)) {
      const attrs = node.getAttributeNames ? node.getAttributeNames() : [];
      for (const name of attrs) {
        if (/message|conversation|chat|item/i.test(name)) {
          maxId = maxNumericMessageId(maxId, node.getAttribute(name) || '');
        }
      }
    }
    return maxId;
  }

  function maxNumericMessageId(left, right) {
    const a = normalizeNumericMessageId(left);
    const b = normalizeNumericMessageId(right);
    if (!a) return b;
    if (!b) return a;
    return compareNumericMessageIds(a, b) >= 0 ? a : b;
  }

  function normalizeNumericMessageId(value) {
    const match = String(value || '').match(/\d{8,}/);
    return match ? match[0].replace(/^0+/, '') || '0' : '';
  }

  function compareNumericMessageIds(left, right) {
    const a = normalizeNumericMessageId(left);
    const b = normalizeNumericMessageId(right);
    if (!a && !b) return 0;
    if (!a) return -1;
    if (!b) return 1;
    if (a.length !== b.length) return a.length > b.length ? 1 : -1;
    return a === b ? 0 : (a > b ? 1 : -1);
  }

  function promptFingerprint(value) {
    const text = normalizePromptCompare(value).slice(0, 240);
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16);
  }

  function buildNetworkPromptProbe(value) {
    const text = normalizePromptCompare(value);
    if (!text) return [];
    const probes = [];
    const add = (item) => {
      const part = String(item || '').replace(/\s+/g, ' ').trim();
      if (part.length >= 12 && !probes.includes(part)) probes.push(part.slice(0, 220));
    };
    add(text.slice(0, 180));
    add(text.slice(Math.max(0, text.length - 180)));
    if (text.length > 360) {
      const middle = Math.max(0, Math.floor(text.length / 2) - 90);
      add(text.slice(middle, middle + 180));
    }
    for (const match of text.match(/[\u4e00-\u9fa5A-Za-z0-9][^。；;.!?！？]{24,160}/g) || []) {
      add(match);
      if (probes.length >= 6) break;
    }
    return probes.slice(0, 6);
  }

  function getAssignedAccountId() {
    try {
      const url = new URL(location.href);
      const fromUrl = url.searchParams.get('dbvdAccountId') || url.hash.match(/dbvdAccountId=([^&]+)/)?.[1] || '';
      if (fromUrl) {
        sessionStorage.setItem('dbvdAccountId', decodeURIComponent(fromUrl));
        return decodeURIComponent(fromUrl);
      }
      return sessionStorage.getItem('dbvdAccountId') || '';
    } catch {
      return sessionStorage.getItem('dbvdAccountId') || '';
    }
  }

  function getGenerationFailureAfter(previousCount, task = null) {
    const hardFailure = getHardGenerationFailureMessage(currentFailureSourceText(task));
    if (hardFailure) return { count: Math.max(previousCount + 1, countGenerationFailures(task)), message: hardFailure, hard: true };
    const count = countGenerationFailures(task);
    if (count <= previousCount) return null;
    return { count, message: getGenerationFailureMessage(task) || '豆包视频生成失败' };
  }

  function countGenerationFailures(task = null) {
    const text = currentFailureSourceText(task, getPageTextWithoutPanel());
    return (text.match(/视频生成失败|生成失败|生成额度未扣除|额度未扣除|未扣除|出于肖像保护考虑|肖像保护|真实人脸素材|人脸素材作为参考|换张参考图/gi) || []).length;
  }

  function getGenerationFailureMessage(task = null) {
    const text = currentFailureSourceText(task, getPageTextWithoutPanel());
    const hardFailure = getHardGenerationFailureMessage(text);
    if (hardFailure) return hardFailure;
    const match = text.match(/视频生成失败[^\n。；;]*[。；;]?|生成失败[^\n。；;]*[。；;]?|生成额度未扣除[^\n。；;]?|额度未扣除[^\n。；;]?|未扣除[^\n。；;]?/i);
    return match ? match[0].trim() || '豆包视频生成失败' : '';
  }

  function getHardGenerationFailureMessage(text = getPageTextWithoutPanel()) {
    const match = String(text || '').match(DOUBAO_HARD_FAILURE_PATTERN);
    return match ? match[0].trim() || '豆包拒绝当前素材/提示词' : '';
  }

  async function resubmitGenerationTask(task, beforeFailureCount) {
    await ensureVideoCreationSurface();
    await waitForReadyInput();
    let input = findPromptInput();
    if (!input) throw new Error('重新填充失败：未找到豆包提示词输入框');
    setAutomationProgress(task, 'prompting', '正在重新填充视频提示词', {
      status: task.status || 'submitted',
      progress: 46,
      tone: 'warn',
    });
    const imageResult = await tryUploadTaskImage(task, input);
    if (imageResult && imageResult.hasImage && !imageResult.uploaded) throw new Error(imageResult.error || '首帧图未自动上传');
    const promptText = buildPromptText(task);
    await setPromptValue(input, promptText);
    input = await waitForWritablePromptInput(input);
    await waitForPromptValue(input, promptText);
    input = await ensurePromptReadyForSubmit(input, promptText);
    const resultGuard = captureSubmissionAnchor(input, promptText, await readCurrentVideoKeys());
    resultGuard.vmoTaskId = task.id;
    await resetNetworkCaptureForTask(task, resultGuard);
    showPreparedTask(task, imageResult, true);
    await submitPrompt(input, promptText);
    await waitForSubmissionAccepted(input, beforeFailureCount, task);
    await updateTask({ id: task.id, status: 'submitted', resultGuard });
    task.resultGuard = resultGuard;
    setAutomationProgress(task, 'submitted', `已重新提交：${task.title || '分镜视频'}`, {
      status: 'submitted',
      progress: 68,
    });
  }

  function pickNewVideo(videos, beforeKeys, task = null) {
    if (!Array.isArray(videos) || !videos.length) return null;
    const guard = task && task.resultGuard || {};
    const sorted = rankVideoCandidates(videos, beforeKeys, task);
    return sorted.find(({ video }) => {
      const key = videoKey(video);
      return key && !isKnownVideoCandidate(video, beforeKeys, guard);
    })?.video || (beforeKeys.size === 0 ? sorted[0]?.video || null : null);
  }

  function rankVideoCandidates(videos, beforeKeys, task = null) {
    if (!Array.isArray(videos) || !videos.length) return [];
    const guard = task && task.resultGuard || {};
    return [...videos]
      .map((video) => ({ video, score: scoreTaskVideoCandidate(video, beforeKeys, guard) }))
      .filter((entry) => entry.score > -Infinity && hasUsableVideoUrl(entry.video))
      .sort((a, b) => b.score - a.score);
  }

  function summarizeVideoCandidatesForLlm(entries, task = null) {
    return entries.slice(0, 8).map((entry, index) => {
      const video = entry.video || entry;
      return {
        index,
        key: videoStableKey(video) || videoKey(video) || '',
        resultKey: video.doubaoResultKey || '',
        score: Number(entry.score || video.score || 0),
        source: String(video.source || '').slice(0, 120),
        cardText: String(video.cardText || '').replace(/\s+/g, ' ').slice(0, 500),
        ids: {
          task: video.doubaoInternalTaskId || '',
          message: video.doubaoInternalMessageId || video.messageId || '',
          video: video.doubaoInternalVideoId || video.vid || '',
          networkTask: video.networkAnchorTaskId || '',
          networkAccount: video.networkAnchorAccountId || '',
        },
        hasUrl: Boolean(video.assetUrl || video.backupUrl),
        networkTrusted: Boolean(video.networkAnchorTrusted || video.networkPromptMatched),
        extractedAt: Number(video.extractedAt || 0),
        taskPromptHint: task && task.prompt ? String(task.prompt).slice(0, 240) : '',
      };
    });
  }

  function candidateFromLlmDecision(decision, entries) {
    if (!decision || decision.action !== 'bind_candidate' || !Array.isArray(entries)) return null;
    if (Number(decision.confidence || 0) < 0.55) return null;
    if (decision.candidateIndex !== undefined) {
      const entry = entries[Number(decision.candidateIndex)];
      if (entry && entry.video) return entry.video;
    }
    const key = String(decision.candidateKey || '');
    if (key) {
      const entry = entries.find((item) => {
        const video = item.video || item;
        return key === (videoStableKey(video) || '') || key === (videoKey(video) || '') || key === String(video.doubaoResultKey || '');
      });
      if (entry && entry.video) return entry.video;
    }
    return null;
  }

  async function pickNewVideoWithLlm(videos, beforeKeys, task = null, pageStatus = null, context = {}) {
    const ranked = rankVideoCandidates(videos, beforeKeys, task);
    const deterministic = pickNewVideo(videos, beforeKeys, task);
    const top = ranked[0];
    const second = ranked[1];
    const topStrong = top && (
      top.score >= 5000
      || Boolean(top.video && (top.video.networkAnchorTrusted || top.video.networkPromptMatched))
      || (second ? top.score - second.score > 1800 : top.score > 1200)
    );
    if (deterministic && topStrong) return deterministic;
    if (!ranked.length) return deterministic;
    const candidateVideos = summarizeVideoCandidatesForLlm(ranked, task);
    const decision = await requestLlmAutomationDecision(task, context.stage || 'candidate-binding', pageStatus, {
      candidateVideos,
      deterministicCandidate: deterministic ? summarizeVideoCandidatesForLlm([{ video: deterministic, score: top && top.video === deterministic ? top.score : 0 }], task)[0] : {},
      decisionKey: `candidates:${candidateVideos.map((item) => `${item.index}:${item.key}:${item.score}`).join('|')}`,
      beforeFailureCount: context.beforeFailureCount || 0,
      completedSeenAt: context.completedSeenAt || 0,
    });
    const selected = candidateFromLlmDecision(decision, ranked);
    if (selected) {
      await updateTask({
        id: task.id,
        status: 'submitted',
        stage: 'llm-candidate-bound',
        doubaoLlmDecision: decision,
        doubaoLlmDecisionAt: Date.now(),
        doubaoLlmAction: 'bind_candidate',
        doubaoPageMessage: decision.candidateReason || decision.reason || 'LLM 已辅助绑定视频候选',
      }).catch(() => {});
      return selected;
    }
    if (decision && decision.action && decision.action !== 'bind_candidate') {
      const applied = await applyLlmAutomationDecision(task, decision, { pageStatus, beforeFailureCount: context.beforeFailureCount || 0 });
      if (applied) return null;
    }
    return deterministic;
  }

  function scoreTaskVideoCandidate(video, beforeKeys, guard = {}) {
    const key = videoKey(video);
    if (!key || isKnownVideoCandidate(video, beforeKeys, guard)) return -Infinity;
    let score = Number(video.score || 0);
    const guardTaskId = String(guard.vmoTaskId || guard.taskId || '');
    const networkTaskId = String(video.networkAnchorTaskId || '');
    const networkTrusted = Boolean(video.networkAnchorTrusted || video.networkPromptMatched);
    if (guardTaskId && networkTaskId && guardTaskId !== networkTaskId) return -Infinity;
    if (guardTaskId && networkTaskId && guardTaskId === networkTaskId) score += networkTrusted ? 5200 : 1200;
    const identities = videoIdentityKeys(video);
    const exactGuardKeys = exactGuardKeySet(guard);
    if (exactGuardKeys.size && identities.some((item) => exactGuardKeys.has(item))) score += 10000;
    if (networkTrusted) score += 2400;
    if (identities.some((item) => /^(task|message|video|result):/i.test(item))) score += 1800;
    const rectTop = Number(video.rectTop);
    const rectBottom = Number(video.rectBottom);
    const anchorTop = Number(guard.anchorTop || 0);
    const anchorBottom = Number(guard.anchorBottom || 0);
    const submittedAt = Number(guard.submittedAt || 0);
    const networkSubmittedAt = Number(video.networkAnchorSubmittedAt || 0);
    const extractedAt = Number(video.extractedAt || 0);
    const hasRect = Number.isFinite(rectTop) && Number.isFinite(rectBottom) && (rectTop !== 0 || rectBottom !== 0);
    const messageId = normalizeNumericMessageId(video.messageId || '');
    const maxBeforeMessageId = normalizeNumericMessageId(guard.maxMessageIdBefore || '');
    const sourceText = String(video.source || '');
    const watermarkResolved = isWatermarkResolvedCandidate(video);
    if (submittedAt && networkSubmittedAt && networkSubmittedAt < submittedAt - 2500) return -Infinity;
    if (submittedAt && extractedAt && extractedAt < submittedAt - 2500) return -Infinity;
    if (maxBeforeMessageId && messageId && compareNumericMessageIds(messageId, maxBeforeMessageId) <= 0) return -Infinity;
    const hasStrongInternalId = Boolean(video.doubaoInternalTaskId || video.doubaoInternalMessageId || video.doubaoInternalVideoId || video.vid || video.messageId);
    if (anchorBottom && !hasRect && !messageId && !(networkTrusted && hasStrongInternalId) && !watermarkResolved) return -Infinity;
    if (/network|performance/i.test(sourceText) && !hasRect && !networkTrusted && !hasStrongInternalId) score -= 1800;
    if (watermarkResolved) score += 3600;
    if (hasRect && anchorBottom) {
      if (rectBottom < anchorTop - 40) return -Infinity;
      if (rectTop >= anchorTop - 40) score += 1200;
      if (rectTop >= anchorBottom - 160) score += 900;
      score += Math.max(0, 800 - Math.abs(rectTop - anchorBottom));
    }
    if (submittedAt && extractedAt >= submittedAt) score += 250;
    const cardText = String(video.cardText || '');
    if (/\u4f60\u7684\u89c6\u9891\u751f\u6210\u597d|\u89c6\u9891\u751f\u6210\u597d|\u751f\u6210\u597d\u4e86|\u751f\u6210\u5b8c\u6210|\u4e0b\u8f7d\u89c6\u9891|\u4fdd\u5b58\u89c6\u9891|(?:10|15)\s*\u79d2|Seedance/i.test(cardText)) score += 500;
    if (String(video.messageId || '')) score += 50;
    if (String(video.doubaoInternalTaskId || '')) score += 250;
    if (String(video.doubaoInternalMessageId || '')) score += 180;
    if (String(video.doubaoInternalVideoId || '')) score += 220;
    return score;
  }

  function videoKey(video) {
    const keys = videoIdentityKeys(video);
    if (keys.length) return keys[0];
    return String(video && (video.vid || video.messageId || video.assetUrl || video.backupUrl) || '');
  }

  function videoStableKey(video) {
    if (!video) return '';
    const idKey = videoKey(video);
    const url = normalizeVideoUrl(video.assetUrl || video.backupUrl || '');
    return [idKey, url].filter(Boolean).join('|');
  }

  function buildPromptText(task) {
    const prompt = forcePromptDuration(normalizePromptText(task.prompt || ''), taskTargetDurationSeconds(task));
    const parts = [prompt];
    if (task.ratio && !/画面比例|比例\s*[:：]|aspect\s*ratio/i.test(prompt)) parts.push(`画面比例：${task.ratio}`);
    return parts.filter(Boolean).join('\n');
  }

  function forcePromptDuration(value, seconds = DOUBAO_FIXED_VIDEO_DURATION_SECONDS) {
    const text = normalizePromptText(value);
    const durationField = /((?:总时长|视频时长|生成时长|时长|duration)\s*[:：]?\s*)\d+(?:\.\d+)?\s*(?:秒|s|sec|seconds?)?/gi;
    const hasDurationField = (line) => /(?:总时长|视频时长|生成时长|时长|duration)\s*[:：]?\s*\d+(?:\.\d+)?\s*(?:秒|s|sec|seconds?)?/i.test(line);
    const skipLine = (line) => /时间轴|^\s*(?:\d+(?:\.\d+)?\s*[-~至到]\s*\d+(?:\.\d+)?\s*秒|[【\[]?(?:动作过程|镜头动态|运镜\/景别)[】\]]?)/.test(line);
    let rewritten = false;
    const lines = (text ? text.split('\n') : []).map((line) => {
      if (!hasDurationField(line) || skipLine(line)) return line;
      rewritten = true;
      return line.replace(durationField, (_, label) => `${label}${seconds}秒`);
    });
    if (!rewritten) lines.push(`时长：${seconds}秒`);
    return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
  }

  function normalizePromptText(value) {
    return String(value || '')
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/p>\s*<p[^>]*>/gi, '\n')
      .replace(/<\/?(?:p|div)[^>]*>/gi, '\n')
      .replace(/&nbsp;/gi, ' ')
      .replace(/&amp;/gi, '&')
      .replace(/&lt;/gi, '<')
      .replace(/&gt;/gi, '>')
      .replace(/\r\n?/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  async function uploadTaskImageIfNeeded(task, promptInput) {
    const imagePaths = getTaskImagePaths(task);
    if (!task || !imagePaths.length || task.__imageUploaded) return;
    const files = [];
    for (let index = 0; index < imagePaths.length; index++) {
      const url = await getTaskImageUrl(task.id, index);
      const imageResponse = await fetch(url, { cache: 'no-store' });
      if (!imageResponse.ok) throw new Error(`???? ${index + 1} ?????HTTP ${imageResponse.status}`);
      const blob = await imageResponse.blob();
      const filename = String(imagePaths[index]).split(/[\\/]/).pop() || `reference-image-${index + 1}.png`;
      files.push(new File([blob], filename, { type: blob.type || guessImageMime(filename) }));
    }

    const dt = new DataTransfer();
    files.forEach((file) => dt.items.add(file));

    setAutomationProgress(task, 'uploading', `正在上传参考图 ${files.length} 张：${task.title || '分镜视频'}`, {
      status: task.status || 'claimed',
      progress: 28,
    });
    const fileInput = await waitForImageFileInput(true);
    if (fileInput) {
      setFileInputFiles(fileInput, dt.files);
      await sleep(1200);
    } else {
      const dropTarget = findUploadDropTarget(promptInput);
      dispatchImageDrop(dropTarget, dt);
      dispatchImagePaste(dropTarget, dt);
      await sleep(1200);
    }

    await waitForReferenceImageAttached(files.length);
    task.__imageUploaded = true;
    setAutomationProgress(task, 'after-upload', `参考图已上传 ${files.length} 张：${task.title || '分镜视频'}`, {
      status: task.status || 'claimed',
      progress: 38,
    });
    await sleep(800);
  }

  function getTaskImagePaths(task) {
    const paths = Array.isArray(task && task.imagePaths) ? task.imagePaths : [];
    if (paths.length) return paths.slice(0, 9).filter(Boolean);
    return task && task.imagePath ? [task.imagePath] : [];
  }

  async function tryUploadTaskImage(task, promptInput) {
    const imagePaths = getTaskImagePaths(task);
    if (!imagePaths.length) return { hasImage: false, uploaded: false, count: 0 };
    if (task.__imageUploaded) return { hasImage: true, uploaded: true, count: imagePaths.length };
    try {
      await uploadTaskImageIfNeeded(task, promptInput);
      return { hasImage: true, uploaded: true, count: imagePaths.length };
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      console.warn('[page-service-channel] upload task image failed:', error);
      setAutomationProgress(task, 'uploading', `参考图上传失败：${task.title || '分镜视频'}`, {
        status: 'failed',
        progress: 100,
        tone: 'bad',
      });
      return { hasImage: true, uploaded: false, count: imagePaths.length, error: message };
    }
  }

  function showPreparedTask(task, imageResult, retried = false) {
    const title = task.title || '分镜视频';
    const count = imageResult && imageResult.count || 0;
    const imageText = !imageResult || !imageResult.hasImage
      ? '无需参考图'
      : imageResult.uploaded
        ? `已上传 ${count || 1} 张参考图`
        : `${count || 1} 张参考图未上传`;
    const text = `${retried ? '重新准备' : '已准备'}：${title}，${imageText}，等待提交生成`;
    setAutomationProgress(task, 'prepared', text, {
      status: imageResult && imageResult.hasImage && !imageResult.uploaded ? 'failed' : 'prepared',
      progress: imageResult && imageResult.hasImage && !imageResult.uploaded ? 100 : 58,
      tone: imageResult && imageResult.hasImage && !imageResult.uploaded ? 'bad' : 'active',
    });
  }

  function guessImageMime(filename) {
    const lower = String(filename || '').toLowerCase();
    if (lower.endsWith('.png')) return 'image/png';
    if (lower.endsWith('.webp')) return 'image/webp';
    return 'image/jpeg';
  }

  async function waitForImageFileInput(allowClick = true) {
    let input = findImageFileInput();
    if (input) return input;
    if (!allowClick) return null;

    const uploadEntry = findUploadEntry();
    if (uploadEntry) {
      clickElement(uploadEntry);
      await sleep(800);
      input = findImageFileInput();
      if (input) return input;
    }

    const plusEntry = findClickableByText(/^\+$|上传|图片|参考图|首帧|添加/i);
    if (plusEntry) {
      clickElement(plusEntry);
      await sleep(800);
      input = findImageFileInput();
      if (input) return input;
    }

    for (let i = 0; i < 20; i++) {
      input = findImageFileInput();
      if (input) return input;
      await sleep(250);
    }
    return null;
  }

  function findImageFileInput() {
    const inputs = [...document.querySelectorAll('input[type="file"]')];
    return inputs.find((item) => {
      if (!(item instanceof HTMLInputElement)) return false;
      const accept = String(item.accept || '').toLowerCase();
      return /image|\.(png|jpe?g|webp)/i.test(accept);
    }) || inputs.find((item) => item instanceof HTMLInputElement) || null;
  }

  function setFileInputFiles(input, files) {
    try {
      const proto = Object.getPrototypeOf(input);
      const setter = Object.getOwnPropertyDescriptor(proto, 'files')?.set;
      if (setter) setter.call(input, files);
      else input.files = files;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (error) {
      throw new Error('无法写入豆包图片上传控件：' + (error && error.message || error));
    }
  }

  function findUploadEntry() {
    const candidates = [...document.querySelectorAll('button, [role="button"], label, div, span')];
    return candidates.find((item) => {
      if (!(item instanceof HTMLElement) || item.offsetParent === null) return false;
      if (document.getElementById(ROOT_ID)?.contains(item)) return false;
      const text = String(item.innerText || item.textContent || item.getAttribute('aria-label') || item.getAttribute('title') || '').trim();
      return text && text.length <= 32 && /上传|添加图片|参考图|首帧|图片|图像生成/i.test(text);
    }) || null;
  }

  function findUploadDropTarget(promptInput) {
    const uploadEntry = findUploadEntry();
    if (uploadEntry) return uploadEntry;
    let node = promptInput;
    for (let i = 0; node && i < 5; i++, node = node.parentElement) {
      if (node instanceof HTMLElement) return node;
    }
    return document.body;
  }

  function dispatchImageDrop(target, dataTransfer) {
    const candidates = [...new Set([target, document.body, document.documentElement].filter(Boolean))];
    for (const item of candidates) {
      for (const type of ['dragenter', 'dragover', 'drop']) {
        item.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer }));
      }
    }
  }

  function dispatchImagePaste(target, dataTransfer) {
    const promptInput = findPromptInput();
    const candidates = [...new Set([target, document.body, document.documentElement].filter(Boolean))]
      .filter((item) => item && item !== promptInput && !(promptInput && item.contains?.(promptInput)));
    for (const item of candidates) {
      try {
        item.focus?.();
        item.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dataTransfer }));
      } catch (error) {
        console.warn('[page-service-channel] paste image failed:', error);
      }
    }
  }

  async function waitForReferenceImageAttached(expectedCount = 1) {
    const before = Date.now();
    for (let i = 0; i < 24; i++) {
      const text = getPageTextWithoutPanel();
      if (/重新上传|删除图片|添加照片|已上传|上传成功|图片上传/i.test(text)) return;
      const media = [...document.querySelectorAll('img, canvas, video')].filter((item) => {
        if (!(item instanceof HTMLElement) || item.closest(`#${ROOT_ID}`)) return false;
        const rect = item.getBoundingClientRect();
        return rect.width >= 40 && rect.height >= 40 && rect.top > 0 && rect.left > 0;
      });
      if (media.length >= Math.min(1, expectedCount)) return true;
      if (Date.now() - before > 12000) break;
      await sleep(250);
    }
    throw new Error('参考图上传后未在豆包页面检测到缩略图');
  }

  const CN_MORE = '\u66f4\u591a';
  const CN_VIDEO = '\u89c6\u9891';
  const CN_VIDEO_GENERATION = '\u89c6\u9891\u751f\u6210';
  const CN_ADD_PHOTO_VIDEO_HINT = '\u6dfb\u52a0\u7167\u7247\uff0c\u63cf\u8ff0\u4f60\u60f3\u751f\u6210\u7684\u89c6\u9891';
  const CN_DESCRIBE_VIDEO_HINT = '\u63cf\u8ff0\u4f60\u60f3\u8981\u7684\u89c6\u9891';

  function isDoubaoVisibleElement(item) {
    if (!(item instanceof HTMLElement)) return false;
    if (item.closest(`#${ROOT_ID}`)) return false;
    const rect = item.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) return false;
    const style = getComputedStyle(item);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0.01) return false;
    return true;
  }

  function textOfElement(item) {
    if (!item) return '';
    const values = [
      item.getAttribute && item.getAttribute('aria-label'),
      item.getAttribute && item.getAttribute('title'),
      item.innerText,
      item.textContent,
    ].map((value) => String(value || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
    return values.sort((a, b) => a.length - b.length)[0] || '';
  }

  function compactText(value) {
    return String(value || '').replace(/\s+/g, '');
  }

  function isMoreText(value) {
    const compact = compactText(value);
    return compact === CN_MORE || compact === '更多' || /^More$/i.test(compact);
  }

  function isVideoGenerationText(value) {
    const text = String(value || '').trim();
    const compact = compactText(text);
    return compact === CN_VIDEO_GENERATION || compact === '视频生成' || /(^|\s)视频生成(\s|$)|Video\s*Generation/i.test(text);
  }

  function durationTargetPattern(seconds = DOUBAO_FIXED_VIDEO_DURATION_SECONDS) {
    const value = String(seconds).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp('(^|[^0-9])' + value + '\\s*(?:s|秒)([^0-9]|$)', 'i');
  }

  function isDurationText(value) {
    return /时长|duration|(^|[^0-9])(?:5|10|15)\s*(?:s|秒)([^0-9]|$)/i.test(String(value || ''));
  }

  function durationSecondsInText(value) {
    const seconds = new Set();
    const pattern = /(^|[^0-9])(\d{1,2})\s*(?:s|秒)([^0-9]|$)/gi;
    let match = null;
    while ((match = pattern.exec(String(value || '')))) {
      const number = Number(match[2]);
      if (DOUBAO_VIDEO_DURATION_OPTIONS.includes(number)) seconds.add(number);
    }
    return [...seconds];
  }

  function durationControlText(item) {
    const values = [
      item && item.getAttribute && item.getAttribute('aria-label'),
      item && item.getAttribute && item.getAttribute('title'),
      item && item.innerText,
      item && item.textContent,
    ].map((value) => String(value || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
    return values.sort((a, b) => a.length - b.length)[0] || '';
  }

  function durationElementSnapshot(item) {
    if (!(item instanceof HTMLElement)) return null;
    const rect = item.getBoundingClientRect();
    return {
      tag: item.tagName,
      role: item.getAttribute('role') || '',
      text: durationControlText(item).slice(0, 120),
      ariaLabel: String(item.getAttribute('aria-label') || '').slice(0, 120),
      title: String(item.getAttribute('title') || '').slice(0, 120),
      className: String(item.className || '').slice(0, 120),
      rect: rectSnapshot(rect),
    };
  }

  function durationCandidates(seconds = DOUBAO_FIXED_VIDEO_DURATION_SECONDS) {
    const input = findPromptInput() || findAnyPromptInput();
    const inputRect = input && input.getBoundingClientRect();
    const composerRect = input && composerRectFor(input);
    const pattern = durationTargetPattern(seconds);
    return [...document.querySelectorAll('button, [role="button"], [role="option"], [aria-label], [title], li, div, span')]
      .filter(isDoubaoVisibleElement)
      .map((item) => clickableTargetFor(item) || item)
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .filter((item) => isDoubaoVisibleElement(item) && !isLikelyConversationTitleOrHistoryTarget(item))
      .map((item) => {
        const text = durationControlText(item);
        const rect = item.getBoundingClientRect();
        const center = rectCenter(rect);
        let score = 0;
        if (!isDurationText(text)) score -= 2000;
        if (/时长|duration/i.test(text)) score += 1200;
        if (pattern.test(text)) score += 6000;
        if (/(?:^|[^0-9])10\s*(?:s|秒)(?:[^0-9]|$)/i.test(text)) score += 500;
        if (/(?:^|[^0-9])5\s*(?:s|秒)(?:[^0-9]|$)/i.test(text)) score += 250;
        if (/选中|已选|selected|checked/i.test(text + ' ' + (item.getAttribute('aria-selected') || '') + ' ' + (item.getAttribute('aria-checked') || ''))) score += 400;
        if (inputRect) {
          if (center.y < inputRect.top - 140 || center.y > inputRect.bottom + 180) score -= 1600;
          if (center.x < inputRect.left - 120 || center.x > inputRect.right + 180) score -= 900;
        }
        if (composerRect) {
          if (center.y < composerRect.top - 80 || center.y > composerRect.bottom + 140) score -= 1000;
          if (center.x < composerRect.left - 100 || center.x > composerRect.right + 160) score -= 700;
        }
        score -= Math.max(0, rect.width * rect.height - 24000) / 800;
        return { el: item, text, score };
      })
      .filter((entry) => entry.score > -500 && isDurationText(entry.text))
      .sort((a, b) => b.score - a.score);
  }

  function inspectDoubaoDurationCapability(taskOrSeconds = null) {
    const requiredSeconds = typeof taskOrSeconds === 'number' ? selectDoubaoDuration(taskOrSeconds) : taskTargetDurationSeconds(taskOrSeconds || {});
    const pattern = durationTargetPattern(requiredSeconds);
    const candidates = durationCandidates(requiredSeconds);
    const labels = [...new Set(candidates.map((entry) => entry.text).filter(Boolean))].slice(0, 30);
    const availableSeconds = [...new Set(labels.flatMap(durationSecondsInText))].sort((a, b) => a - b);
    const target = candidates.find((entry) => pattern.test(entry.text));
    const hasTarget = Boolean(target) || availableSeconds.includes(requiredSeconds);
    return {
      requiredSeconds,
      availableSeconds,
      hasTarget,
      selectedTarget: Boolean(target && /选中|已选|selected|checked|true/i.test(target.text + ' ' + (target.el.getAttribute('aria-selected') || '') + ' ' + (target.el.getAttribute('aria-checked') || ''))),
      options: labels,
      target: target ? durationElementSnapshot(target.el) : null,
      message: hasTarget ? '豆包页面已检测到 ' + requiredSeconds + ' 秒选项' : '当前豆包页面未检测到 ' + requiredSeconds + ' 秒选项',
    };
  }

  async function ensureFixedDurationSelected(task = null) {
    const requiredSeconds = taskTargetDurationSeconds(task || {});
    const pattern = durationTargetPattern(requiredSeconds);
    const before = inspectDoubaoDurationCapability(task || requiredSeconds);
    let candidates = durationCandidates(requiredSeconds);
    let visibleSeconds = [...new Set(candidates.flatMap((entry) => durationSecondsInText(entry.text)))];
    if (visibleSeconds.length === 1 && visibleSeconds[0] === requiredSeconds) {
      await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'duration-already-selected', {
        stage: 'duration-already-selected',
        requiredSeconds,
        before,
      });
      return before;
    }
    let target = candidates.find((entry) => pattern.test(entry.text));
    if (!target) {
      const opener = candidates[0];
      if (opener) {
        if (!(await devtoolsClick(opener.el, task && task.id || getAssignedTaskId()))) clickElement(opener.el);
        await sleep(500);
        candidates = durationCandidates(requiredSeconds);
        visibleSeconds = [...new Set(candidates.flatMap((entry) => durationSecondsInText(entry.text)))];
        if (visibleSeconds.length === 1 && visibleSeconds[0] === requiredSeconds) {
          const after = inspectDoubaoDurationCapability(task || requiredSeconds);
          await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'duration-already-selected', {
            stage: 'duration-already-selected',
            requiredSeconds,
            before,
            after,
          });
          return after;
        }
        target = candidates.find((entry) => pattern.test(entry.text));
      }
    }
    if (!target) {
      const after = inspectDoubaoDurationCapability(task || requiredSeconds);
      await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'fixed-duration-not-available', {
        stage: 'fixed-duration-not-available',
        requiredSeconds,
        before,
        after,
        pageTextTail: getPageTextWithoutPanel().replace(/\s+/g, ' ').slice(-500),
      });
      throw new Error('豆包目标 ' + requiredSeconds + ' 秒未在页面生效：当前持续时间控件未出现对应选项，请确认页面处于视频生成模式');
    }
    if (!(await devtoolsClick(target.el, task && task.id || getAssignedTaskId()))) clickElement(target.el);
    await sleep(350);
    const after = inspectDoubaoDurationCapability(task || requiredSeconds);
    await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'fixed-duration-selected', {
      stage: 'fixed-duration-selected',
      requiredSeconds,
      before,
      after,
    });
    return after;
  }

  function elementTextContext(item) {
    if (!item) return '';
    const parts = [
      item.getAttribute && item.getAttribute('placeholder'),
      item.getAttribute && item.getAttribute('aria-label'),
      item.getAttribute && item.getAttribute('data-placeholder'),
      item.getAttribute && item.getAttribute('title'),
      item.innerText,
      item.textContent,
    ];
    let node = item.parentElement;
    for (let i = 0; node && i < 4; i++, node = node.parentElement) {
      parts.push(node.getAttribute && node.getAttribute('aria-label'));
      parts.push(node.innerText || node.textContent || '');
    }
    return parts.map((value) => String(value || '').replace(/\s+/g, ' ').trim()).filter(Boolean).join(' ');
  }

  function isInsideConversationTitleDialog(item) {
    let node = item;
    for (let i = 0; node && i < 8; i++, node = node.parentElement) {
      if (!(node instanceof HTMLElement)) break;
      const text = String(node.innerText || node.textContent || '');
      const role = String(node.getAttribute('role') || '').toLowerCase();
      if ((role === 'dialog' || /modal|dialog|popover/i.test(String(node.className || ''))) && /编辑对话名称|对话名称|conversation name|rename/i.test(text)) return true;
    }
    return false;
  }

  function findConversationTitleDialog() {
    return [...document.querySelectorAll('[role="dialog"], [aria-modal="true"], div, section')]
      .filter((item) => item instanceof HTMLElement && item.offsetParent !== null && !item.closest(`#${ROOT_ID}`))
      .find((item) => {
        const rect = item.getBoundingClientRect();
        if (rect.width < 180 || rect.height < 80) return false;
        const text = String(item.innerText || item.textContent || '');
        return /编辑对话名称|对话名称|conversation name|rename/i.test(text);
      }) || null;
  }

  function isConversationTitleEditModalOpen() {
    return Boolean(findConversationTitleDialog());
  }

  function isAllowedAutomationNavigationText(text) {
    const compact = compactText(text);
    return compact === 'AI创作'
      || compact === CN_MORE
      || compact === '更多'
      || compact === CN_VIDEO
      || compact === '视频'
      || compact === CN_VIDEO_GENERATION
      || compact === '视频生成'
      || compact === '根据图片生成视频'
      || compact === '制作特定动作视频'
      || /添加照片|参考图|首帧|尾帧|上传|图片生成视频|动作视频|Create|Video\s*Generation/i.test(String(text || ''));
  }

  function isLikelyConversationTitleOrHistoryTarget(item) {
    if (!(item instanceof HTMLElement) || item.closest(`#${ROOT_ID}`)) return false;
    if (isInsideConversationTitleDialog(item)) return false;
    const rect = item.getBoundingClientRect();
    const text = textOfElement(item);
    const raw = elementTextContext(item);
    if (/编辑对话名称|对话名称|conversation name|rename/i.test(raw)) return true;
    const center = rectCenter(rect);
    const inTopTitleBar = rect.top < Math.max(112, window.innerHeight * 0.12)
      && center.x > Math.max(360, window.innerWidth * 0.24)
      && center.x < window.innerWidth - 220
      && rect.width < Math.min(520, window.innerWidth * 0.48);
    if (inTopTitleBar) return true;
    if (isAllowedAutomationNavigationText(text)) return false;
    const inLeftHistoryList = rect.left < Math.max(420, window.innerWidth * 0.34)
      && rect.top > Math.max(260, window.innerHeight * 0.20)
      && rect.height <= 96
      && /历史对话|主对话|生成视频|\d+秒|短视频|对话|chat/i.test(raw);
    return inLeftHistoryList;
  }

  async function closeConversationTitleModal() {
    const dialog = findConversationTitleDialog();
    if (!dialog) return false;
    const button = [...dialog.querySelectorAll('button, [role="button"], [aria-label], [title], div, span')]
      .filter(isDoubaoVisibleElement)
      .map((item) => nearestClickableElement(item) || item)
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .find((item) => /取消|关闭|Close|Cancel/i.test(textOfElement(item)));
    if (button) {
      clickElement(button);
      await sleep(300);
    } else {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }));
      document.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }));
      await sleep(300);
    }
    return !isConversationTitleEditModalOpen();
  }

  function isPromptInputRejected(item) {
    if (!(item instanceof HTMLElement) || item.closest(`#${ROOT_ID}`)) return true;
    if (isInsideConversationTitleDialog(item)) return true;
    const text = elementTextContext(item);
    if (/编辑对话名称|对话名称|标题|名称|rename|conversation name/i.test(text)) return true;
    if (/搜索|历史|评论|comment|search/i.test(text)) return true;
    return false;
  }

  function isImageModePromptInput(item) {
    const text = elementTextContext(item);
    const hasVideo = /描述你想(?:要|生成)的视频|添加照片[^。；;]*视频|生成视频|Seedance|首帧|尾帧|时长|分辨率/i.test(text);
    const hasImage = /描述你想要的图片|生成图片|Seedream|AI\s*抠图|擦除|图像模式/i.test(text);
    return hasImage && !hasVideo;
  }

  function isVideoPromptInput(item) {
    if (!(item instanceof HTMLElement) || isPromptInputRejected(item)) return false;
    if (isImageModePromptInput(item)) return false;
    const text = elementTextContext(item);
    return /描述你想(?:要|生成)的视频|添加照片[^。；;]*视频|生成视频|Seedance|首帧|尾帧|时长|分辨率|视频模式/i.test(text);
  }

  function findVideoPromptInput() {
    const candidates = [
      ...document.querySelectorAll('textarea'),
      ...document.querySelectorAll('[contenteditable="true"]'),
      ...document.querySelectorAll('[role="textbox"]'),
    ].filter((item) => item instanceof HTMLElement && item.offsetParent !== null && isVideoPromptInput(item));
    return candidates
      .map((item) => {
        const rect = item.getBoundingClientRect();
        let score = rect.width * rect.height / 1000;
        const text = elementTextContext(item);
        if (/描述你想(?:要|生成)的视频|添加照片[^。；;]*视频/i.test(text)) score += 18000;
        if (/Seedance|时长|分辨率|首帧|尾帧/i.test(text)) score += 8000;
        if (rect.bottom > window.innerHeight * 0.35) score += 1000;
        return { item, score };
      })
      .sort((a, b) => b.score - a.score)[0]?.item || null;
  }

  function findAnyPromptInput() {
    const candidates = [
      ...document.querySelectorAll('textarea'),
      ...document.querySelectorAll('[contenteditable="true"]'),
      ...document.querySelectorAll('[role="textbox"]'),
    ].filter((item) => item instanceof HTMLElement && item.offsetParent !== null && !isPromptInputRejected(item));
    return candidates
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.width * br.height + br.bottom) - (ar.width * ar.height + ar.bottom);
      })[0] || null;
  }

  function scoreComposerVideoModeTab(item, inputRect, composerRect, source = null) {
    if (!(item instanceof HTMLElement)) return -Infinity;
    if (isLikelyConversationTitleOrHistoryTarget(item) || isLikelyConversationTitleOrHistoryTarget(source)) return -Infinity;
    const rect = item.getBoundingClientRect();
    if (rect.width < 24 || rect.height < 18 || rect.width > 120 || rect.height > 72) return -Infinity;
    const targetText = compactText(textOfElement(item));
    const sourceText = compactText(textOfElement(source || item));
    if (targetText !== CN_VIDEO && targetText !== '视频' && sourceText !== CN_VIDEO && sourceText !== '视频') return -Infinity;
    const center = rectCenter(rect);
    if (inputRect) {
      if (center.x < inputRect.left - 80 || center.x > inputRect.right + 140) return -Infinity;
      if (center.y < inputRect.top - 40 || center.y > inputRect.bottom + 170) return -Infinity;
    } else if (rect.top < window.innerHeight * 0.35) {
      return -Infinity;
    }
    if (composerRect) {
      if (center.x < composerRect.left - 40 || center.x > composerRect.right + 40) return -Infinity;
      if (center.y < composerRect.top - 20 || center.y > composerRect.bottom + 40) return -Infinity;
    }
    let score = 12000;
    if (inputRect) score += 1200 - Math.min(1200, Math.abs(center.y - (inputRect.bottom + 38)));
    if (composerRect) score += 700 - Math.min(700, Math.abs(center.x - (composerRect.left + composerRect.width * 0.18)));
    score -= rect.width * rect.height / 500;
    return score;
  }

  function findComposerVideoModeTab(input = null) {
    const inputNode = input || findAnyPromptInput() || findPromptInput();
    const inputRect = inputNode ? inputNode.getBoundingClientRect() : null;
    const composerRect = inputNode ? composerRectFor(inputNode) : null;
    const scored = [...document.querySelectorAll('button, [role="button"], [role="tab"], [aria-label], [title], div, span')]
      .filter(isDoubaoVisibleElement)
      .map((source) => {
        const target = clickableTargetFor(source) || source;
        const targetScore = scoreComposerVideoModeTab(target, inputRect, composerRect, source);
        const sourceScore = scoreComposerVideoModeTab(source, inputRect, composerRect, source);
        return { item: targetScore >= sourceScore ? target : source, source, score: Math.max(targetScore, sourceScore) };
      })
      .filter((entry, index, list) => entry.item instanceof HTMLElement && list.findIndex((other) => other.item === entry.item) === index)
      .filter((entry) => Number.isFinite(entry.score))
      .sort((a, b) => b.score - a.score);
    return scored[0]?.item || null;
  }

  async function ensureVideoModeSelected() {
    await closeConversationTitleModal();
    if (isVideoCreationSurface()) return true;
    const tab = findComposerVideoModeTab();
    if (!tab) return false;
    clickElement(tab);
    for (let i = 0; i < 8; i++) {
      await sleep(500);
      if (isVideoCreationSurface()) return true;
    }
    return false;
  }

  async function assertVideoSurfaceForSubmit(task = null, stage = 'submit') {
    await closeConversationTitleModal();
    const input = findVideoPromptInput();
    if (isVideoCreationSurface() && input && !isConversationTitleEditModalOpen()) return input;
    const anyInput = findAnyPromptInput() || findPromptInput();
    await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'video-surface-not-ready', {
      stage,
      href: location.href,
      imageMode: Boolean(anyInput && isImageModePromptInput(anyInput)),
      titleModal: isConversationTitleEditModalOpen(),
      inputContext: elementTextContext(anyInput).slice(0, 500),
      submitDebug: submitDebugInfo(anyInput),
      pageTextTail: getPageTextWithoutPanel().replace(/\s+/g, ' ').slice(-500),
    });
    throw new Error('不在 AI 创作视频页：当前未切到“视频”模式，已阻止误提交');
  }

  function rectCenter(rect) {
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }

  function nearestClickableElement(item) {
    let node = item;
    for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
      if (!(node instanceof HTMLElement) || node === document.body || node === document.documentElement) break;
      if (!isDoubaoVisibleElement(node)) continue;
      const role = String(node.getAttribute('role') || '').toLowerCase();
      const tag = node.tagName.toLowerCase();
      const style = getComputedStyle(node);
      if (tag === 'button' || tag === 'a' || tag === 'label' || role === 'button' || role === 'menuitem' || role === 'tab') return node;
      if (node.hasAttribute('tabindex') && style.cursor === 'pointer') return node;
      if (typeof node.onclick === 'function' || style.cursor === 'pointer') return node;
    }
    return null;
  }

  function clickableTargetFor(item) {
    if (!(item instanceof Element)) return null;
    const base = item instanceof HTMLElement ? item : item.parentElement;
    if (!(base instanceof HTMLElement)) return null;
    const rect = base.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    const hitBase = hit instanceof HTMLElement ? hit : hit?.parentElement;
    const target = nearestClickableElement(hitBase) || nearestClickableElement(base) || base;
    return isLikelyConversationTitleOrHistoryTarget(target) ? null : target;
  }

  function findComposerRoot(input) {
    if (!input) return null;
    const inputRect = input.getBoundingClientRect();
    const choices = [];
    let node = input;
    for (let depth = 0; node && depth < 9; depth++, node = node.parentElement) {
      if (!(node instanceof HTMLElement) || node === document.body || node === document.documentElement) continue;
      const rect = node.getBoundingClientRect();
      if (rect.width < Math.max(260, window.innerWidth * 0.24)) continue;
      if (rect.height < Math.max(36, inputRect.height)) continue;
      if (rect.height > Math.min(420, window.innerHeight * 0.58)) continue;
      if (rect.bottom < window.innerHeight * 0.42) continue;
      if (inputRect.left < rect.left - 8 || inputRect.right > rect.right + 8) continue;
      const rawText = String(node.innerText || node.textContent || '');
      let score = 0;
      score += Math.min(rect.width, 1100) / 16;
      score += rect.bottom > window.innerHeight * 0.66 ? 240 : 0;
      score += Math.max(0, 260 - Math.abs(rect.bottom - inputRect.bottom));
      score -= Math.max(0, rect.height - 240);
      if (/热点|历史对话|下载电脑版/.test(rawText) && rawText.length > 120) score -= 500;
      choices.push({ node, score });
    }
    choices.sort((a, b) => b.score - a.score);
    return choices[0]?.node || null;
  }

  function composerRectFor(input) {
    const inputRect = input && input.getBoundingClientRect();
    const root = input && findComposerRoot(input);
    if (root) return root.getBoundingClientRect();
    if (!inputRect) return null;
    const left = Math.max(0, inputRect.left - 48);
    const right = Math.min(window.innerWidth, Math.max(inputRect.right + 360, inputRect.right));
    const top = Math.max(0, inputRect.top - 130);
    const bottom = Math.min(window.innerHeight, inputRect.bottom + 170);
    return { left, right, top, bottom, width: right - left, height: bottom - top };
  }

  function rectLabel(item) {
    if (!item) return 'none';
    const rect = item.getBoundingClientRect();
    return `${Math.round(rect.left)},${Math.round(rect.top)},${Math.round(rect.width)}x${Math.round(rect.height)}:${textOfElement(item).slice(0, 16)}`;
  }

  function scoreBottomMoreEntry(item, inputRect, composerRect) {
    if (!(item instanceof HTMLElement) || isLikelyConversationTitleOrHistoryTarget(item)) return -Infinity;
    const rect = item.getBoundingClientRect();
    const center = rectCenter(rect);
    if (rect.width < 18 || rect.height < 18 || rect.width > 180 || rect.height > 88) return -Infinity;
    if (rect.top < window.innerHeight * 0.36) return -Infinity;
    if (inputRect) {
      if (center.x < inputRect.left - 90) return -Infinity;
      if (rect.bottom < inputRect.top - 130 || rect.top > inputRect.bottom + 190) return -Infinity;
    }
    if (composerRect) {
      if (center.x < composerRect.left - 72 || center.x > composerRect.right + 72) return -Infinity;
      if (center.y < composerRect.top - 100 || center.y > composerRect.bottom + 100) return -Infinity;
    }
    let score = 1000;
    score += center.x / 3;
    score += center.y / 8;
    if (composerRect) score += 900 - Math.min(900, Math.abs(center.y - composerRect.bottom + 34));
    if (inputRect) score += 700 - Math.min(700, Math.abs(center.y - inputRect.bottom));
    score -= (rect.width * rect.height) / 180;
    return score;
  }

  function scoreDirectVideoGenerationEntry(target, source, inputRect, composerRect) {
    if (!(target instanceof HTMLElement) || isLikelyConversationTitleOrHistoryTarget(target) || isLikelyConversationTitleOrHistoryTarget(source)) return -Infinity;
    const rect = target.getBoundingClientRect();
    const center = rectCenter(rect);
    const sourceCompact = compactText(textOfElement(source || target));
    const targetCompact = compactText(textOfElement(target));
    const xSlack = Math.max(96, Math.min(320, window.innerWidth * 0.18));
    const ySlack = Math.max(96, Math.min(240, window.innerHeight * 0.18));
    const toolbarSlack = Math.max(90, Math.min(220, (composerRect?.height || 120) * 1.8));
    if (sourceCompact !== CN_VIDEO_GENERATION && sourceCompact !== '视频生成' && targetCompact !== CN_VIDEO_GENERATION && targetCompact !== '视频生成') return -Infinity;
    if (rect.width < 36 || rect.height < 20 || rect.width > 180 || rect.height > 88) return -Infinity;
    if (rect.top < window.innerHeight * 0.35) return -Infinity;
    if (inputRect) {
      if (center.x < inputRect.left - xSlack || center.x > inputRect.right + xSlack) return -Infinity;
      if (center.y < inputRect.top - ySlack || center.y > inputRect.bottom + ySlack) return -Infinity;
    }
    if (composerRect) {
      if (center.x < composerRect.left - 72 || center.x > composerRect.right + 72) return -Infinity;
      if (center.y < composerRect.bottom - toolbarSlack || center.y > composerRect.bottom + Math.max(72, window.innerHeight * 0.08)) return -Infinity;
    }
    let score = 10000;
    if (sourceCompact === CN_VIDEO_GENERATION || sourceCompact === '视频生成') score += 6000;
    if (targetCompact === CN_VIDEO_GENERATION || targetCompact === '视频生成') score += 3000;
    if (inputRect) score += 1200 - Math.min(1200, Math.abs(center.y - (inputRect.bottom + 38)));
    if (composerRect) score += 1200 - Math.min(1200, Math.abs(center.y - (composerRect.bottom - 34)));
    score -= (rect.width * rect.height) / 900;
    return score;
  }

  function collectMoreDiagnostics() {
    return [...document.querySelectorAll('button, [role="button"], [aria-label], [title], div, span')]
      .filter(isDoubaoVisibleElement)
      .filter((item) => isMoreText(textOfElement(item)))
      .slice(0, 10)
      .map(rectLabel)
      .join('|') || 'none';
  }

  function collectVideoDiagnostics() {
    return [...document.querySelectorAll('button, [role="button"], [role="menuitem"], [aria-label], [title], li, div, span')]
      .filter(isDoubaoVisibleElement)
      .filter((item) => isVideoGenerationText(textOfElement(item)))
      .slice(0, 10)
      .map(rectLabel)
      .join('|') || 'none';
  }

  async function ensureVideoCreationSurface() {
    await closeConversationTitleModal();
    if (isVideoCreationSurface()) return;
    if (await ensureVideoModeSelected()) return;

    let creationVideoEntry = findCreationVideoTab();
    if (creationVideoEntry) {
      clickElement(creationVideoEntry);
      await sleep(1800);
      if (isVideoCreationSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }

    let directEntry = findDirectVideoGenerationEntry();
    if (directEntry) {
      clickElement(directEntry);
      await sleep(1800);
      if (isVideoCreationSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }

    await enterCreationHub();
    if (isVideoCreationSurface()) return;
    if (await ensureVideoModeSelected()) return;

    creationVideoEntry = findCreationVideoTab();
    if (creationVideoEntry) {
      clickElement(creationVideoEntry);
      await sleep(1800);
      if (isVideoCreationSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }

    directEntry = findDirectVideoGenerationEntry();
    if (directEntry) {
      clickElement(directEntry);
      await sleep(1800);
      if (isVideoCreationSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }

    let sidebarTemplateEntry = findSidebarVideoTemplateEntry();
    if (sidebarTemplateEntry) {
      clickElement(sidebarTemplateEntry);
      await sleep(2200);
      if (isVideoCreationSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }

    let sidebarMoreEntry = findSidebarMoreEntry();
    if (sidebarMoreEntry) {
      clickElement(sidebarMoreEntry);
      await sleep(800);
      sidebarTemplateEntry = findSidebarVideoTemplateEntry();
      if (sidebarTemplateEntry) {
        clickElement(sidebarTemplateEntry);
        await sleep(2200);
        if (isVideoCreationSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }
    }

    let moreEntry = findBottomMoreEntry();
    if (moreEntry) {
      const moreRect = moreEntry.getBoundingClientRect();
      clickElement(moreEntry);
      await sleep(600);
      const menuVideoEntry = findMenuVideoGenerationEntry(moreRect);
      if (menuVideoEntry) {
        clickElement(menuVideoEntry);
        await sleep(1600);
        if (await ensureVideoModeSelected()) return;
      }
    }

    for (let i = 0; i < 8; i++) {
      if (isVideoCreationSurface()) return;
      if (await ensureVideoModeSelected()) return;
      directEntry = findDirectVideoGenerationEntry();
      if (directEntry) {
        clickElement(directEntry);
        await sleep(1200);
        if (isVideoCreationSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }
      creationVideoEntry = findCreationVideoTab();
      if (creationVideoEntry) {
        clickElement(creationVideoEntry);
        await sleep(1200);
        if (isVideoCreationSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }
      sidebarTemplateEntry = findSidebarVideoTemplateEntry();
      if (sidebarTemplateEntry) {
        clickElement(sidebarTemplateEntry);
        await sleep(1600);
        if (isVideoCreationSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }
      await sleep(500);
    }

    moreEntry = findBottomMoreEntry();
    const moreRect = moreEntry && moreEntry.getBoundingClientRect();
    if (moreEntry) {
      clickElement(moreEntry);
      await sleep(500);
    }
    const videoEntry = findMenuVideoGenerationEntry(moreRect) || findVideoGenerationEntry();
    if (videoEntry) {
      clickElement(videoEntry);
      await sleep(1600);
      if (await ensureVideoModeSelected()) return;
    }

    for (let i = 0; i < 16; i++) {
      if (isVideoCreationSurface()) return;
      if (await ensureVideoModeSelected()) return;
      directEntry = findDirectVideoGenerationEntry();
      if (directEntry) {
        clickElement(directEntry);
        await sleep(1200);
        if (isVideoCreationSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }
      creationVideoEntry = findCreationVideoTab();
      if (creationVideoEntry) {
        clickElement(creationVideoEntry);
        await sleep(1200);
        if (isVideoCreationSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }
      sidebarTemplateEntry = findSidebarVideoTemplateEntry();
      if (sidebarTemplateEntry) {
        clickElement(sidebarTemplateEntry);
        await sleep(1600);
        if (isVideoCreationSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }
      sidebarMoreEntry = findSidebarMoreEntry();
      if (sidebarMoreEntry && i % 4 === 1) {
        clickElement(sidebarMoreEntry);
        await sleep(500);
      }
      await sleep(500);
    }
    const selectedMore = rectLabel(findBottomMoreEntry());
    const selectedDirect = rectLabel(findDirectVideoGenerationEntry());
    const selectedCreationVideo = rectLabel(findCreationVideoTab());
    const selectedSidebarMore = rectLabel(findSidebarMoreEntry());
    const selectedSidebarTemplate = rectLabel(findSidebarVideoTemplateEntry());
    const sample = getPageTextWithoutPanel().replace(/\s+/g, ' ').slice(0, 220);
    throw new Error(`未进入豆包主对话的视频生成模式：selectedDirect=${selectedDirect} selectedCreationVideo=${selectedCreationVideo} selectedSidebarMore=${selectedSidebarMore} selectedSidebarTemplate=${selectedSidebarTemplate} selectedMore=${selectedMore} moreList=${collectMoreDiagnostics()} videoList=${collectVideoDiagnostics()} page=${sample}`);
  }

  function findBottomMoreEntry() {
    const input = findPromptInput();
    const inputRect = input && input.getBoundingClientRect();
    const composerRect = composerRectFor(input);
    const scored = [...document.querySelectorAll('button, [role="button"], [aria-label], [title], div, span')]
      .filter(isDoubaoVisibleElement)
      .filter((item) => isMoreText(textOfElement(item)))
      .map((item) => {
        const target = clickableTargetFor(item);
        return { item: target, source: item, score: scoreBottomMoreEntry(target, inputRect, composerRect) };
      })
      .filter((entry) => Number.isFinite(entry.score))
      .filter((entry, index, list) => list.findIndex((other) => other.item === entry.item) === index)
      .sort((a, b) => b.score - a.score);
    return scored[0]?.item || null;
  }

  function findDirectVideoGenerationEntry() {
    const input = findPromptInput();
    const inputRect = input && input.getBoundingClientRect();
    const composerRect = composerRectFor(input);
    const scored = [...document.querySelectorAll('button, [role="button"], [aria-label], [title], div, span')]
      .filter(isDoubaoVisibleElement)
      .filter((item) => {
        const text = textOfElement(item);
        const compact = compactText(text);
        return text && text.length <= 36 && (compact === CN_VIDEO_GENERATION || compact === '视频生成');
      })
      .map((item) => {
        const target = clickableTargetFor(item);
        return { item: target, source: item, score: scoreDirectVideoGenerationEntry(target, item, inputRect, composerRect) };
      })
      .filter((entry) => Number.isFinite(entry.score))
      .filter((entry, index, list) => list.findIndex((other) => other.item === entry.item) === index)
      .sort((a, b) => b.score - a.score);
    return scored[0]?.item || null;
  }

  function findCreationHubEntry() {
    const candidates = [...document.querySelectorAll('a, button, [role="button"], [aria-label], [title], div, span')]
      .filter(isDoubaoVisibleElement)
      .filter((item) => {
        const text = textOfElement(item);
        const compact = compactText(text);
        const href = String(item.href || item.getAttribute?.('href') || '');
        if (href.includes('/chat/create-image')) return true;
        return compact === 'AI创作' || /AI\s*创作|Create/i.test(text);
      })
      .map((item) => clickableTargetFor(item))
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .filter((item) => !isLikelyConversationTitleOrHistoryTarget(item))
      .filter((item) => {
        const rect = item.getBoundingClientRect();
        return rect.width >= 36 && rect.height >= 20 && rect.left < Math.max(420, window.innerWidth * 0.52);
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        const ah = String(a.href || a.getAttribute?.('href') || '');
        const bh = String(b.href || b.getAttribute?.('href') || '');
        const as = (ah.includes('/chat/create-image') ? 10000 : 0) - ar.left - ar.top / 3;
        const bs = (bh.includes('/chat/create-image') ? 10000 : 0) - br.left - br.top / 3;
        return bs - as;
      });
    return candidates[0] || null;
  }

  function findCreationVideoTab() {
    const pageText = String(document.body && document.body.innerText || '');
    if (!/AI\s*创作|我的创作|Seedream|参考图|Previous slide|Next slide/i.test(pageText)) return null;
    const candidates = [...document.querySelectorAll('button, [role="tab"], [role="button"], [aria-label], [title], div, span')]
      .filter(isDoubaoVisibleElement)
      .filter((item) => {
        const text = textOfElement(item);
        const compact = compactText(text);
        if (compact !== CN_VIDEO && compact !== '视频') return false;
        const rect = item.getBoundingClientRect();
        return rect.width >= 28 && rect.height >= 18
          && rect.top > window.innerHeight * 0.08
          && rect.top < window.innerHeight * 0.55
          && rect.left > Math.min(220, window.innerWidth * 0.18);
      })
      .map((item) => clickableTargetFor(item))
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .filter((item) => !isLikelyConversationTitleOrHistoryTarget(item))
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        const as = ar.left + ar.top - (String(a.getAttribute?.('role') || '').toLowerCase() === 'tab' ? 5000 : 0);
        const bs = br.left + br.top - (String(b.getAttribute?.('role') || '').toLowerCase() === 'tab' ? 5000 : 0);
        return as - bs;
      });
    return candidates[0] || null;
  }

  function findSidebarVideoTemplateEntry() {
    const candidates = [...document.querySelectorAll('a, button, [role="button"], [aria-label], [title], div, span')]
      .filter(isDoubaoVisibleElement)
      .filter((item) => {
        const text = textOfElement(item);
        const compact = compactText(text);
        return compact === '根据图片生成视频' || compact === '制作特定动作视频' || /图片生成视频|动作视频|视频生成/i.test(text);
      })
      .map((item) => clickableTargetFor(item))
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .filter((item) => !isLikelyConversationTitleOrHistoryTarget(item))
      .filter((item) => {
        const rect = item.getBoundingClientRect();
        return rect.width >= 36 && rect.height >= 20 && rect.left < Math.max(420, window.innerWidth * 0.52);
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        const at = compactText(textOfElement(a));
        const bt = compactText(textOfElement(b));
        const as = (at === '根据图片生成视频' ? 5000 : 0) - ar.top - ar.left / 4;
        const bs = (bt === '根据图片生成视频' ? 5000 : 0) - br.top - br.left / 4;
        return bs - as;
      });
    return candidates[0] || null;
  }

  function findSidebarMoreEntry() {
    const candidates = [...document.querySelectorAll('button, [role="button"], [aria-label], [title], div, span')]
      .filter(isDoubaoVisibleElement)
      .filter((item) => isMoreText(textOfElement(item)))
      .map((item) => clickableTargetFor(item))
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .filter((item) => !isLikelyConversationTitleOrHistoryTarget(item))
      .filter((item) => {
        const rect = item.getBoundingClientRect();
        return rect.width >= 36 && rect.height >= 20
          && rect.left < Math.max(360, window.innerWidth * 0.42)
          && rect.top > 48
          && rect.top < window.innerHeight * 0.55;
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (ar.left + ar.top / 4) - (br.left + br.top / 4);
      });
    return candidates[0] || null;
  }

  function isDolaHost() {
    return /(^|\.)dola\.com$/i.test(location.hostname || '');
  }

  function siteCreationPath() {
    return isDolaHost() ? '/chat/' : '/chat/create-image';
  }

  async function enterCreationHub() {
    if (isDolaHost() && /^\/chat\/?$/i.test(location.pathname || '')) return true;
    if (!isDolaHost() && /\/chat\/create-image/.test(location.pathname || '')) return true;
    const hub = findCreationHubEntry();
    if (hub) {
      clickElement(hub);
      await sleep(2200);
      return true;
    }
    try {
      const next = new URL(siteCreationPath(), location.origin);
      location.href = next.toString();
      await sleep(2600);
      return true;
    } catch (error) {
      return false;
    }
  }

  function findMenuVideoGenerationEntry(anchorRect = null) {
    const candidates = [...document.querySelectorAll('button, [role="button"], [role="menuitem"], [aria-label], [title], li, div, span')]
      .filter((item) => {
        if (!isDoubaoVisibleElement(item)) return false;
        const text = textOfElement(item);
        if (!text || text.length > 72) return false;
        if (!isVideoGenerationText(text)) return false;
        const rect = item.getBoundingClientRect();
        if (rect.width < 36 || rect.height < 20) return false;
        if (!anchorRect) return true;
        const center = rectCenter(rect);
        return center.x >= anchorRect.left - 300
          && center.x <= anchorRect.right + 430
          && center.y >= anchorRect.top - 560
          && center.y <= anchorRect.bottom + 260;
      })
      .map((item) => clickableTargetFor(item))
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .filter((item) => !isLikelyConversationTitleOrHistoryTarget(item))
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        const at = textOfElement(a);
        const bt = textOfElement(b);
        const targetX = anchorRect ? anchorRect.left + Math.min(anchorRect.width, 96) : window.innerWidth;
        const targetY = anchorRect ? anchorRect.top - 120 : window.innerHeight;
        const ac = rectCenter(ar);
        const bc = rectCenter(br);
        const as = (compactText(at) === '视频生成' ? 12000 : 0) - Math.hypot(ac.x - targetX, ac.y - targetY) - (ar.width * ar.height / 8000);
        const bs = (compactText(bt) === '视频生成' ? 12000 : 0) - Math.hypot(bc.x - targetX, bc.y - targetY) - (br.width * br.height / 8000);
        return bs - as;
      });
    return candidates[0] || null;
  }

  function isVideoCreationSurface() {
    const pageText = String(document.body && document.body.innerText || '');
    const hasVideoWorkspace = pageText.includes(CN_ADD_PHOTO_VIDEO_HINT)
      || pageText.includes(CN_DESCRIBE_VIDEO_HINT)
      || /Seedance/i.test(pageText)
      || (pageText.includes(CN_VIDEO_GENERATION) && /添加照片|描述你想|生成视频|参考图|首帧|比例|分辨率|时长/i.test(pageText));
    return hasVideoWorkspace && Boolean(findVideoPromptInput());
  }

  function findClickableByText(pattern) {
    const candidates = [...document.querySelectorAll('button, a, [role="button"], [role="tab"], div, span')];
    return candidates.find((item) => {
      if (!(item instanceof HTMLElement) || item.offsetParent === null) return false;
      const text = String(item.innerText || item.textContent || item.getAttribute('aria-label') || '').trim();
      return text && text.length <= 24 && pattern.test(text);
    }) || null;
  }

  async function waitForReadyInput() {
    for (let i = 0; i < 40; i++) {
      if (findPromptInput()) return;
      await sleep(500);
    }
  }

  function findPromptInput() {
    const candidates = [
      ...document.querySelectorAll('textarea'),
      ...document.querySelectorAll('[contenteditable="true"]'),
      ...document.querySelectorAll('[role="textbox"]'),
    ];
    return candidates
      .filter((item) => item instanceof HTMLElement && item.offsetParent !== null)
      .sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight))[0] || null;
  }

  async function setPromptValue(input, text, options = {}) {
    const finalText = String(text || '');
    const allowChatReply = Boolean(options && options.allowChatReply);
    const nextPromptInput = () => (allowChatReply ? findAnyPromptInput() : findPromptInput());
    let target = await waitForWritablePromptInput(input, options);
    if (!target) throw new Error('未找到豆包提示词输入框');
    target.scrollIntoView?.({ block: 'center', inline: 'center' });
    target.focus();
    await sleep(80);
    await clearPromptTarget(target);
    if ('value' in target) {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(target), 'value')?.set;
      if (setter) setter.call(target, finalText);
      else target.value = finalText;
      dispatchTextInputEvents(target, finalText);
      await sleep(180);
      if (promptLooksWritten(target, finalText)) return;
    }

    try {
      const dt = new DataTransfer();
      dt.setData('text/plain', finalText);
      target.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
    } catch (error) {}
    await sleep(180);

    target = nextPromptInput() || target;
    if (!promptLooksWritten(target, finalText)) {
      target.focus();
      await clearPromptTarget(target);
      document.execCommand('insertText', false, finalText);
      await sleep(180);
    }

    target = nextPromptInput() || target;
    if (!promptLooksWritten(target, finalText)) {
      target.focus();
      await clearPromptTarget(target);
      try {
        target.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'insertText', data: finalText }));
      } catch (error) {}
      document.execCommand('insertText', false, finalText);
      await sleep(180);
    }

    target = nextPromptInput() || target;
    if (!promptLooksWritten(target, finalText)) {
      await clearPromptTarget(target);
      target.focus();
      insertTextBySelection(target, finalText);
      await sleep(180);
    }
    dispatchTextInputEvents(target, finalText);
    let parent = target.parentElement;
    for (let i = 0; parent && i < 4; i++, parent = parent.parentElement) {
      dispatchTextInputEvents(parent, finalText);
    }
    target = nextPromptInput() || target;
    if (!promptLooksWritten(target, finalText)) {
      await reportAutomationDiagnostic(getAssignedTaskId(), 'prompt-write-failed', {
        stage: 'prompt-write-failed',
        expectedPromptPreview: normalizePromptCompare(finalText).slice(0, 240),
        observedPromptPreview: promptObservationText(target).slice(0, 240),
        promptDebug: promptDebugInfo(target, finalText),
      });
      throw new Error('提示词未成功写入豆包输入框');
    }
  }

  async function waitForPromptValue(input, expected, options = {}) {
    const allowChatReply = Boolean(options && options.allowChatReply);
    for (let i = 0; i < 30; i++) {
      input = (allowChatReply ? findAnyPromptInput() : findPromptInput()) || input;
      if (promptLooksWritten(input, expected)) return true;
      await sleep(150);
    }
    await reportAutomationDiagnostic(getAssignedTaskId(), 'prompt-wait-timeout', {
      stage: 'prompt-wait-timeout',
      expectedPromptPreview: normalizePromptCompare(expected).slice(0, 240),
      observedPromptPreview: promptObservationText(input).slice(0, 240),
      promptDebug: promptDebugInfo(input, expected),
    });
    throw new Error('提示词未成功写入豆包输入框');
  }

  function normalizePromptCompare(value) {
    return String(value || '')
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/p>\s*<p[^>]*>/gi, '\n')
      .replace(/<\/?(?:p|div)[^>]*>/gi, '\n')
      .replace(/&nbsp;/gi, ' ')
      .replace(/&amp;/gi, '&')
      .replace(/&lt;/gi, '<')
      .replace(/&gt;/gi, '>')
      .replace(/\uFEFF/g, '')
      .replace(/\u00a0/g, ' ')
      .replace(/\r\n?/g, '\n')
      .replace(/[：:]\s*/g, '：')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function compactPromptCompare(value) {
    return normalizePromptCompare(value)
      .replace(/[，,。.;；:：、"'“”‘’`~!！?？()\[\]{}<>《》【】\-_/\\|]+/g, '')
      .replace(/\s+/g, '')
      .toLowerCase();
  }

  function getPromptInputText(input) {
    return input && 'value' in input ? String(input.value || '') : getEditableText(input);
  }

  function promptLooksWritten(input, expected) {
    const current = promptObservationText(input);
    const target = normalizePromptCompare(expected);
    if (!current || !target) return false;
    if (current === target) return true;
    if (current.includes(target) || target.includes(current)) return Math.min(current.length, target.length) >= Math.min(120, target.length);
    const compactCurrent = compactPromptCompare(current);
    const compactTarget = compactPromptCompare(target);
    if (compactCurrent && compactTarget) {
      if (compactCurrent === compactTarget || compactCurrent.includes(compactTarget)) return true;
      if (compactTarget.includes(compactCurrent) && compactCurrent.length >= Math.min(60, compactTarget.length)) return true;
      const important = buildPromptProbeSegments(compactTarget, 36).filter(Boolean);
      if (important.length && important.filter((part) => compactCurrent.includes(part)).length >= Math.min(2, important.length)) return true;
      const durationOk = DOUBAO_VIDEO_DURATION_OPTIONS.some((seconds) => {
        const pattern = new RegExp('时长?' + seconds + '秒|' + seconds + '秒');
        return pattern.test(compactCurrent) && pattern.test(compactTarget);
      });
      const ratioOk = !/画面比例|比例|169|16[:：]?9/.test(compactTarget) || /画面比例|比例|169|16[:：]?9/.test(compactCurrent);
      const head = compactTarget.slice(0, Math.min(48, compactTarget.length));
      if (durationOk && ratioOk && head.length >= 18 && compactCurrent.includes(head)) return true;
    }
    const probes = buildPromptProbeSegments(target);
    if (!probes.length) return false;
    return probes.filter((part) => current.includes(part)).length >= Math.min(2, probes.length);
  }

  function promptObservationText(input) {
    const parts = [];
    const add = (value) => {
      const text = normalizePromptCompare(value);
      if (text && !parts.includes(text)) parts.push(text);
    };
    add(getPromptInputText(input));
    const active = document.activeElement;
    if (active && active instanceof HTMLElement) add(getPromptInputText(active));
    const found = findPromptInput();
    if (found && found !== input) add(getPromptInputText(found));
    const root = findComposerRoot(input || found);
    if (root) add(root.innerText || root.textContent || '');
    return normalizePromptCompare(parts.join('\n'));
  }

  function buildPromptProbeSegments(target, size = 56) {
    const text = normalizePromptCompare(target);
    const parts = [];
    const len = text.length;
    for (const start of [0, Math.floor(len * 0.35), Math.floor(len * 0.7)]) {
      const part = text.slice(start, start + size).trim();
      if (part.length >= Math.min(18, size) && !parts.includes(part)) parts.push(part);
    }
    return parts;
  }

  function promptDebugInfo(input, expected = '') {
    const current = promptObservationText(input);
    return {
      expectedLength: normalizePromptCompare(expected).length,
      observedLength: current.length,
      expectedCompactLength: compactPromptCompare(expected).length,
      observedCompactLength: compactPromptCompare(current).length,
      observedPreview: current.slice(0, 300),
      activeTag: document.activeElement && document.activeElement.tagName || '',
      inputTag: input && input.tagName || '',
      inputRole: input && input.getAttribute && input.getAttribute('role') || '',
      inputEditable: Boolean(input && input.isContentEditable),
    };
  }

  function insertTextBySelection(input, text) {
    input.focus?.();
    if (!input.isContentEditable && input.getAttribute('role') !== 'textbox') return false;
    try {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(input);
      range.collapse(false);
      selection.removeAllRanges();
      selection.addRange(range);
      return document.execCommand('insertText', false, text);
    } catch (error) {
      return false;
    }
  }

  async function waitForWritablePromptInput(previous = null, options = {}) {
    const allowChatReply = Boolean(options && options.allowChatReply);
    let last = previous;
    for (let i = 0; i < 30; i++) {
      const input = (allowChatReply ? findAnyPromptInput() : findPromptInput()) || last;
      const allowedInput = allowChatReply ? !isPromptInputRejected(input) : isVideoPromptInput(input);
      if (input && input instanceof HTMLElement && input.offsetParent !== null && !input.closest(`#${ROOT_ID}`) && allowedInput) {
        input.scrollIntoView?.({ block: 'center', inline: 'center' });
        input.focus?.();
        await sleep(100);
        if (document.activeElement === input || input.contains(document.activeElement) || 'value' in input || input.isContentEditable || input.getAttribute('role') === 'textbox') {
          return input;
        }
        last = input;
      }
      await sleep(150);
    }
    return (allowChatReply ? findAnyPromptInput() : findPromptInput()) || last;
  }

  async function ensurePromptReadyForSubmit(input, expected) {
    let target = await waitForWritablePromptInput(input);
    if (!promptLooksWritten(target, expected)) {
      await setPromptValue(target, expected);
      target = await waitForWritablePromptInput(target);
      await waitForPromptValue(target, expected);
    }
    dispatchTextInputEvents(target, expected);
    await sleep(180);
    if (!promptLooksWritten(target, expected)) throw new Error('提交前提示词为空，已阻止只发送图片');
    return target;
  }

  function findPromptInput() {
    return findVideoPromptInput();
  }

  function clickElement(element) {
    if (!element) return;
    if (isLikelyConversationTitleOrHistoryTarget(element)) return false;
    element.scrollIntoView?.({ block: 'center', inline: 'center' });
    const rect = element.getBoundingClientRect();
    const eventInit = { bubbles: true, cancelable: true, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
    const target = clickableTargetFor(element);
    if (!target) return false;
    if (isLikelyConversationTitleOrHistoryTarget(target)) return false;
    target.focus?.();
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      const Ctor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
      target.dispatchEvent(new Ctor(type, eventInit));
    }
    try { target.click?.(); } catch (error) {}
    if (target !== element) {
      try { element.click?.(); } catch (error) {}
    }
    return true;
  }

  function selectEditableContents(input) {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(input);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  async function clearPromptTarget(input) {
    if (!input) return;
    input.focus?.();
    if ('value' in input) {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')?.set;
      if (setter) setter.call(input, '');
      else input.value = '';
      dispatchTextInputEvents(input, '');
      await sleep(60);
      return;
    }
    selectEditableContents(input);
    try {
      input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'deleteContentBackward', data: null }));
    } catch (error) {}
    try { document.execCommand('delete'); } catch (error) {}
    await sleep(60);
    if (normalizePromptCompare(getEditableText(input))) {
      input.textContent = '';
    }
    dispatchTextInputEvents(input, '');
    await sleep(60);
  }

  function getEditableText(input) {
    if (!input) return '';
    const clone = input.cloneNode(true);
    try {
      clone.querySelectorAll('[data-slate-placeholder="true"], [data-slate-zero-width]').forEach((node) => node.remove());
    } catch (error) {}
    return String(clone.innerText || clone.textContent || '').replace(/\uFEFF/g, '').replace(/\r\n?/g, '\n').trim();
  }

  function dispatchTextInputEvents(target, text = '') {
    try { target.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'insertReplacementText', data: null })); } catch (error) {}
    try { target.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertReplacementText', data: null })); } catch (error) {}
    target.dispatchEvent(new Event('change', { bubbles: true }));
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Process', code: 'Unidentified', bubbles: true }));
    target.dispatchEvent(new KeyboardEvent('keyup', { key: 'Process', code: 'Unidentified', bubbles: true }));
  }

  async function submitPrompt(input, expectedPrompt = '') {
    if (expectedPrompt) input = await ensurePromptReadyForSubmit(input, expectedPrompt);
    for (let i = 0; i < 4; i++) {
      const button = await waitForGenerateButton(input);
      if (button) {
        if (!(await devtoolsClick(button))) clickElement(button);
        await sleep(700);
      }
      if (isSubmissionLikelyAccepted(input)) return;
      submitPromptByKeyboard(input);
      await sleep(700);
      if (isSubmissionLikelyAccepted(input)) return;
    }
  }

  async function waitForSubmissionAccepted(input, beforeFailureCount = 0, task = null) {
    let durationAdjustAnswered = false;
    for (let i = 0; i < 12; i++) {
      await failOnLowerDurationCap(task || { id: getAssignedTaskId() });
      if (detectDurationAdjustQuestion(task)) {
        if (!durationAdjustAnswered) {
          durationAdjustAnswered = true;
          await answerDurationAdjustQuestion(task);
        }
        return;
      }
      if (getGenerationFailureAfter(beforeFailureCount, task)) return;
      if (isSubmissionLikelyAccepted(input)) return;
      await sleep(500);
    }
    throw new Error('豆包页面未确认提交，请检查“视频生成”按钮是否可点击');
  }

  function detectDurationAdjustQuestion(task = null) {
    const text = getPageTextWithoutPanel();
    const seconds = String(taskTargetDurationSeconds(task || automationPanelState?.task || {})).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp('不支持生成超过\\s*' + seconds + '\\s*秒的视频[\\s\\S]{0,80}调整为\\s*' + seconds + '\\s*秒继续生成|超过\\s*' + seconds + '\\s*秒[\\s\\S]{0,80}' + seconds + '\\s*秒继续生成|调整为\\s*' + seconds + '\\s*秒继续生成', 'i').test(text);
  }

  function detectLowerDurationCapQuestion() {
    const text = getPageTextWithoutPanel();
    const patterns = [
      /不支持生成超过\s*(\d{1,2})\s*秒的视频[\s\S]{0,100}调整为\s*(\d{1,2})\s*秒继续生成/i,
      /超过\s*(\d{1,2})\s*秒[\s\S]{0,100}(\d{1,2})\s*秒继续生成/i,
      /调整为\s*(\d{1,2})\s*秒继续生成/i,
    ];
    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (!match) continue;
      const values = match.slice(1).map((value) => Number(value)).filter((value) => Number.isFinite(value) && value > 0);
      const cap = Math.min(...values);
      if (Number.isFinite(cap) && cap < DOUBAO_FIXED_VIDEO_DURATION_SECONDS) return cap;
    }
    return 0;
  }

  async function failOnLowerDurationCap(task = null) {
    const requiredSeconds = taskTargetDurationSeconds(task || automationPanelState?.task || {});
    const cap = detectLowerDurationCapQuestion();
    if (!cap) return false;
    if (cap >= requiredSeconds) return false;
    const message = '豆包页面提示只能调整为 ' + cap + ' 秒继续生成，低于当前目标 ' + requiredSeconds + ' 秒';
    await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'duration-cap-lower-than-required', {
      stage: 'duration-cap-lower-than-required',
      capSeconds: cap,
      requiredSeconds,
      durationCapability: inspectDoubaoDurationCapability(task || requiredSeconds),
    });
    throw new Error(message);
  }

  function detectGenerationCompletedNotice(task = null) {
    const guard = task && task.resultGuard || {};
    if (guard.submittedAt || guard.anchorBottom || guard.maxMessageIdBefore) {
      return extractVisibleResultVideos().some((video) => {
        if (!hasCompletedStatusSignal(String(video.cardText || ''))) return false;
        return scoreTaskVideoCandidate(video, new Set(), guard) > -Infinity;
      });
    }
    return hasCompletedStatusSignal(getPageTextWithoutPanel());
  }

  async function answerDurationAdjustQuestion(task = null) {
    const input = findAnyPromptInput() || findPromptInput();
    const reply = durationConfirmReply(task);
    const seconds = taskTargetDurationSeconds(task || automationPanelState?.task || {});
    if (!input) throw new Error('豆包要求确认 ' + seconds + ' 秒生成，但未找到回复输入框');
    showTaskToast('已自动确认：生成' + seconds + '秒视频', task || automationPanelState?.task || null, {
      stage: 'doubao-duration-confirm',
      progress: 78,
      tone: 'warn',
    });
    await setPromptValue(input, reply, { allowChatReply: true });
    await waitForPromptValue(input, reply, { allowChatReply: true });
    await submitDurationConfirmReply(input);
    await sleep(1200);
  }

  async function submitDurationConfirmReply(input) {
    input = findAnyPromptInput() || input;
    if (!input) return false;
    const rect = input.getBoundingClientRect();
    const composerRect = composerRectFor(input);
    const buttons = [...document.querySelectorAll('button, [role="button"], [aria-label], [title], label, div, span, svg')]
      .filter((item) => item instanceof HTMLElement && item.offsetParent !== null && !item.closest(`#${ROOT_ID}`))
      .map((item) => clickableTargetFor(item) || item)
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .filter((item) => item.offsetParent !== null && !isLikelyConversationTitleOrHistoryTarget(item) && item.getAttribute('aria-disabled') !== 'true' && !item.hasAttribute('disabled'))
      .filter((item) => isElementWithinComposer(item, rect, composerRect, 120) && !isNonSubmitControlText(textOfElement(item)))
      .sort((a, b) => scoreSubmitControl(b, rect, composerRect) - scoreSubmitControl(a, rect, composerRect));
    if (buttons[0]) {
      if (!(await devtoolsClick(buttons[0]))) clickElement(buttons[0]);
      return true;
    }
    input.focus?.();
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
    input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
    return true;
  }

  function isSubmissionLikelyAccepted(input) {
    const current = getEditableText(input);
    const pageText = getPageTextWithoutPanel();
    const accepted = /正在为您生成|正在生成|生成中|排队中|队列中|等待生成|取消生成|停止生成|任务已创建|已提交|创作中|视频生成失败|生成失败|额度未扣除/i.test(pageText);
    if (!accepted) return false;
    if (!current) return true;
    const button = findGenerateButton(input);
    return !button || button.getAttribute('aria-disabled') === 'true' || button.hasAttribute('disabled');
  }

  function getPageTextWithoutPanel() {
    const clone = document.body && document.body.cloneNode(true);
    if (!clone) return '';
    const panel = clone.querySelector(`#${ROOT_ID}`);
    if (panel) panel.remove();
    return String(clone.innerText || clone.textContent || '');
  }

  async function waitForGenerateButton(input = null) {
    for (let i = 0; i < 12; i++) {
      const button = findGenerateButton(input);
      if (button) return button;
      await sleep(250);
    }
    return null;
  }

  function submitPromptByKeyboard(input) {
    input.focus();
    for (const eventInit of [
      { key: 'Enter', code: 'Enter', ctrlKey: true },
      { key: 'Enter', code: 'Enter', metaKey: true },
      { key: 'Enter', code: 'Enter' },
    ]) {
      input.dispatchEvent(new KeyboardEvent('keydown', { ...eventInit, bubbles: true, cancelable: true }));
      input.dispatchEvent(new KeyboardEvent('keyup', { ...eventInit, bubbles: true, cancelable: true }));
    }
  }

  function findGenerateButton(input = null) {
    const buttons = [...document.querySelectorAll('button, [role="button"]')]
      .filter((item) => {
        if (!(item instanceof HTMLElement)) return false;
        if (document.getElementById(ROOT_ID)?.contains(item)) return false;
        if (item.closest(`#${ROOT_ID}`)) return false;
        if (item.classList.contains('dbvd-fab') || item.classList.contains('dbvd-action')) return false;
        return true;
      });
    const inputNode = input || findPromptInput();
    const inputRect = inputNode ? inputNode.getBoundingClientRect() : null;
    const textButton = buttons.find((item) => {
      if (!(item instanceof HTMLElement) || item.offsetParent === null) return false;
      if (item.getAttribute('aria-disabled') === 'true' || item.hasAttribute('disabled')) return false;
      const text = String(item.innerText || item.textContent || item.getAttribute('aria-label') || '').trim();
      const rect = item.getBoundingClientRect();
      if (/关闭|以后再说|Refresh|Save|保存|参考图|比例|更多|Seedance/i.test(text)) return false;
      if (inputRect && (rect.top < inputRect.top - 120 || rect.bottom > inputRect.bottom + 120)) return false;
      return /生成视频|生成|发送|提交|Create|Generate|Send/i.test(text);
    });
    if (textButton) return textButton;

    if (!inputNode || !inputRect) return null;
    const visualButtons = buttons
      .filter((item) => {
        if (!(item instanceof HTMLElement) || item.offsetParent === null) return false;
        if (item.getAttribute('aria-disabled') === 'true' || item.hasAttribute('disabled')) return false;
        const rect = item.getBoundingClientRect();
        const text = String(item.innerText || item.textContent || item.getAttribute('aria-label') || '').trim();
        if (/麦克风|语音|microphone|voice|Refresh|Save|保存|上传|图片|参考图|首帧|附件|add|upload|image/i.test(text)) return false;
        const style = getComputedStyle(item);
        const colorHint = `${style.backgroundColor} ${style.color}`;
        const shapeScore = rect.width >= 40 && rect.height >= 40 && Math.abs(rect.width - rect.height) <= 18 ? 2 : 0;
        const blueScore = /rgb\(0,\s*102,\s*255\)|rgb\(22,\s*119,\s*255\)|rgb\(24,\s*144,\s*255\)|#1677ff|#06f/i.test(colorHint) ? 3 : 0;
        return rect.width >= 28 && rect.height >= 28
          && rect.left >= inputRect.left
          && rect.right <= inputRect.right + 30
          && rect.top >= inputRect.top - 80
          && rect.bottom <= inputRect.bottom + 100
          && (shapeScore + blueScore >= 2 || rect.left > inputRect.left + inputRect.width * 0.75);
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.right + br.bottom) - (ar.right + ar.bottom);
      });
    return visualButtons[0] || null;
  }

  async function submitPrompt(input, expectedPrompt = '') {
    input = await assertVideoSurfaceForSubmit({ id: getAssignedTaskId() }, 'submit');
    if (expectedPrompt) input = await ensurePromptReadyForSubmit(input, expectedPrompt);
    for (let i = 0; i < 5; i++) {
      const button = await waitForGenerateButton(input);
      if (button) {
        if (!(await devtoolsClick(button))) clickElement(button);
        await sleep(700);
      }
      if (isSubmissionLikelyAccepted(input)) return true;
      submitPromptByKeyboard(input);
      await sleep(700);
      if (isSubmissionLikelyAccepted(input)) return true;
    }
    await reportAutomationDiagnostic(getAssignedTaskId(), 'submit-not-accepted', {
      stage: 'submit-not-accepted',
      submitDebug: submitDebugInfo(input, expectedPrompt),
      pageStatus: parseDoubaoPageStatus(getAssignedTaskId() ? { id: getAssignedTaskId() } : null),
    });
    return false;
  }

  async function waitForSubmissionAccepted(input, beforeFailureCount = 0, task = null) {
    let durationAdjustAnswered = false;
    for (let i = 0; i < 16; i++) {
      await failOnLowerDurationCap(task || { id: getAssignedTaskId() });
      if (detectDurationAdjustQuestion(task)) {
        if (!durationAdjustAnswered) {
          durationAdjustAnswered = true;
          await answerDurationAdjustQuestion(task);
        }
        return true;
      }
      if (getGenerationFailureAfter(beforeFailureCount, task)) return true;
      if (isSubmissionLikelyAccepted(input)) return true;
      const button = i % 4 === 2 ? await waitForGenerateButton(input) : null;
      if (button && isVideoCreationSurface() && !(await devtoolsClick(button))) clickElement(button);
      await sleep(500);
    }
    await reportAutomationDiagnostic(getAssignedTaskId(), 'submit-confirm-timeout', {
      stage: 'submit-confirm-timeout',
      submitDebug: submitDebugInfo(input),
      pageStatus: parseDoubaoPageStatus(getAssignedTaskId() ? { id: getAssignedTaskId() } : null),
    });
    const fallbackTask = task || { id: getAssignedTaskId(), status: 'prepared' };
    const pageStatus = parseDoubaoPageStatus(fallbackTask);
    const decision = await requestLlmAutomationDecision(fallbackTask, 'submit-confirm-timeout', pageStatus, {
      beforeFailureCount,
      submitDebug: submitDebugInfo(input),
    });
    const applied = await applyLlmAutomationDecision(fallbackTask, decision, { pageStatus, beforeFailureCount });
    if (applied) return true;
    throw new Error('豆包页面未确认提交，请检查视频生成按钮是否可点击');
  }

  function isSubmissionLikelyAccepted(input) {
    const freshInput = findPromptInput() || input;
    const current = normalizePromptCompare(getPromptInputText(freshInput));
    const pageText = getPageTextWithoutPanel();
    const accepted = /\u6b63\u5728\u4e3a\u60a8\u751f\u6210|\u751f\u6210\u4e2d|\u6b63\u5728\u751f\u6210|\u6392\u961f\u4e2d|\u961f\u5217\u4e2d|\u7b49\u5f85\u751f\u6210|\u4efb\u52a1\u5df2\u521b\u5efa|\u5df2\u63d0\u4ea4|\u521b\u4f5c\u4e2d|\u89c6\u9891\u751f\u6210\u5931\u8d25|\u751f\u6210\u5931\u8d25|\u989d\u5ea6\u672a\u6263\u9664|\u53d6\u6d88\u751f\u6210|\u505c\u6b62\u751f\u6210/i.test(pageText);
    if (!accepted) return false;
    if (!current) return true;
    const button = findGenerateButton(freshInput);
    return !button || button.getAttribute('aria-disabled') === 'true' || button.hasAttribute('disabled');
  }

  async function waitForGenerateButton(input = null) {
    for (let i = 0; i < 14; i++) {
      const button = findGenerateButton(input);
      if (button) return button;
      await sleep(250);
    }
    return null;
  }

  function submitPromptByKeyboard(input) {
    if (!isVideoCreationSurface()) return false;
    input = findPromptInput() || input;
    if (!input || !isVideoPromptInput(input)) return false;
    input?.focus?.();
    for (const eventInit of [
      { key: 'Enter', code: 'Enter', ctrlKey: true },
      { key: 'Enter', code: 'Enter', metaKey: true },
      { key: 'Enter', code: 'Enter' },
    ]) {
      input?.dispatchEvent(new KeyboardEvent('keydown', { ...eventInit, bubbles: true, cancelable: true }));
      input?.dispatchEvent(new KeyboardEvent('keyup', { ...eventInit, bubbles: true, cancelable: true }));
    }
    return true;
  }

  function findGenerateButton(input = null) {
    const inputNode = input || findPromptInput();
    const inputRect = inputNode ? inputNode.getBoundingClientRect() : null;
    const composerRect = inputNode ? composerRectFor(inputNode) : null;
    if (!inputNode || !inputRect || !composerRect || !isVideoPromptInput(inputNode) || !isVideoCreationSurface()) return null;
    const controls = [...document.querySelectorAll('button, [role="button"], [aria-label], [title], label, div, span, svg')]
      .filter((item) => item instanceof HTMLElement && item.offsetParent !== null && !item.closest(`#${ROOT_ID}`));
    const candidates = controls
      .map((item) => clickableTargetFor(item) || item)
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .filter((item) => item.offsetParent !== null && !item.closest(`#${ROOT_ID}`) && !isLikelyConversationTitleOrHistoryTarget(item) && item.getAttribute('aria-disabled') !== 'true' && !item.hasAttribute('disabled'));
    const textButton = candidates.find((item) => {
      const text = String(item.innerText || item.textContent || item.getAttribute('aria-label') || item.getAttribute('title') || '').trim();
      const rect = item.getBoundingClientRect();
      if (!isElementWithinComposer(item, inputRect, composerRect, 96)) return false;
      if (!text || text.length > 32) return false;
      if (isNonSubmitControlText(text)) return false;
      return /\u751f\u6210\u89c6\u9891|\u751f\u6210|\u53d1\u9001|\u63d0\u4ea4|Create|Generate|Send/i.test(text);
    });
    if (textButton) return textButton;
    const visual = candidates
      .filter((item) => isLikelySubmitControl(item, inputRect, composerRect))
      .sort((a, b) => scoreSubmitControl(b, inputRect, composerRect) - scoreSubmitControl(a, inputRect, composerRect));
    if (visual[0]) return visual[0];
    return findElementAtSubmitHotspot(inputRect, composerRect);
  }

  function isNonSubmitControlText(text) {
    return /\u5173\u95ed|\u4ee5\u540e\u518d\u8bf4|Refresh|Save|\u4fdd\u5b58|\u53c2\u8003\u56fe|\u6bd4\u4f8b|\u66f4\u591a|Seedance|\u4e0a\u4f20|\u56fe\u7247|\u9996\u5e27|\u9644\u4ef6|图像|图片|视频$|AI\s*抠图|擦除|推荐|下载豆包电脑版|add|upload|image|microphone|voice/i.test(String(text || ''));
  }

  function isLikelySubmitControl(item, inputRect, composerRect) {
    if (!(item instanceof HTMLElement)) return false;
    if (!inputRect || !composerRect || !isElementWithinComposer(item, inputRect, composerRect, 96)) return false;
    const rect = item.getBoundingClientRect();
    if (rect.width < 24 || rect.height < 24 || rect.width > 96 || rect.height > 96) return false;
    const text = textOfElement(item);
    if (isNonSubmitControlText(text)) return false;
    const className = String(item.className || '');
    const submitClassHint = /send|submit|generate|creation|btn|button|highlight/i.test(className);
    const center = rectCenter(rect);
    if (inputRect) {
      if (center.x < inputRect.left + inputRect.width * 0.42 || center.x > inputRect.right + 90) return false;
      if (center.y < inputRect.top - 95 || center.y > inputRect.bottom + 125) return false;
    }
    if (composerRect) {
      if (center.x < composerRect.left + composerRect.width * 0.50 || center.x > composerRect.right + 90) return false;
      if (center.y < composerRect.top - 20 || center.y > composerRect.bottom + 70) return false;
    }
    return scoreSubmitControl(item, inputRect, composerRect) > (submitClassHint ? -250 : 0);
  }

  function scoreSubmitControl(item, inputRect, composerRect) {
    if (!isElementWithinComposer(item, inputRect, composerRect, 96)) return -Infinity;
    const rect = item.getBoundingClientRect();
    const center = rectCenter(rect);
    const style = getComputedStyle(item);
    const colorHint = `${style.backgroundColor} ${style.color} ${style.borderColor}`;
    const classHint = String(item.className || '');
    const attrHint = `${item.getAttribute('aria-label') || ''} ${item.getAttribute('title') || ''}`;
    let score = 0;
    if (/send-btn-wrapper|send|submit|generate|creation|highlight|primary/i.test(`${classHint} ${attrHint}`)) score += 1200;
    if (/rgb\(0,\s*102,\s*255\)|rgb\(22,\s*119,\s*255\)|rgb\(24,\s*144,\s*255\)|rgb\(0,\s*122,\s*255\)|#1677ff|#06f/i.test(colorHint)) score += 900;
    if (rect.width >= 34 && rect.height >= 34 && Math.abs(rect.width - rect.height) <= 20) score += 420;
    if (inputRect) {
      score += Math.max(0, 700 - Math.abs(center.x - (inputRect.right - 28)) * 3);
      score += Math.max(0, 500 - Math.abs(center.y - (inputRect.bottom - 26)) * 4);
      if (center.x > inputRect.left + inputRect.width * 0.75) score += 300;
    }
    if (composerRect) {
      score += Math.max(0, 650 - Math.abs(center.x - (composerRect.right - 34)) * 3);
      score += Math.max(0, 520 - Math.abs(center.y - (composerRect.bottom - 34)) * 4);
    }
    score -= Math.max(0, rect.width * rect.height - 3600) / 12;
    return score;
  }

  function submitDebugInfo(input = null, expectedPrompt = '') {
    const freshInput = findPromptInput() || input;
    const inputRect = freshInput ? freshInput.getBoundingClientRect() : null;
    const composerRect = freshInput ? composerRectFor(freshInput) : null;
    const button = findGenerateButton(freshInput);
    const candidates = describeSubmitCandidates(freshInput).slice(0, 8);
    return {
      href: location.href,
      inputTextPreview: promptObservationText(freshInput).slice(0, 260),
      expectedPromptPreview: normalizePromptCompare(expectedPrompt).slice(0, 260),
      promptLooksWritten: expectedPrompt ? promptLooksWritten(freshInput, expectedPrompt) : undefined,
      inputRect: inputRect ? rectSnapshot(inputRect) : null,
      composerRect: composerRect ? rectSnapshot(composerRect) : null,
      selectedButton: button ? describeElement(button, freshInput) : null,
      candidates,
      pageTextTail: getPageTextWithoutPanel().replace(/\s+/g, ' ').slice(-500),
    };
  }

  function describeSubmitCandidates(input = null) {
    const inputNode = input || findPromptInput();
    const inputRect = inputNode ? inputNode.getBoundingClientRect() : null;
    const composerRect = inputNode ? composerRectFor(inputNode) : null;
    const controls = [...document.querySelectorAll('button, [role="button"], [aria-label], [title], label, div, span, svg')]
      .filter((item) => item instanceof HTMLElement && item.offsetParent !== null && !item.closest(`#${ROOT_ID}`));
    return controls
      .map((item) => clickableTargetFor(item) || item)
      .filter((item, index, list) => item instanceof HTMLElement && list.indexOf(item) === index)
      .filter((item) => item.offsetParent !== null && !item.closest(`#${ROOT_ID}`) && item.getAttribute('aria-disabled') !== 'true' && !item.hasAttribute('disabled'))
      .filter((item) => !isLikelyConversationTitleOrHistoryTarget(item))
      .filter((item) => isElementWithinComposer(item, inputRect, composerRect, 120))
      .map((item) => ({ item, score: scoreSubmitControl(item, inputRect, composerRect), rect: item.getBoundingClientRect() }))
      .filter((entry) => entry.score > -300 && entry.rect.width >= 16 && entry.rect.height >= 16)
      .sort((a, b) => b.score - a.score)
      .map((entry) => describeElement(entry.item, inputNode, entry.score));
  }

  function describeElement(item, input = null, score = undefined) {
    const rect = item.getBoundingClientRect();
    return {
      tag: item.tagName,
      role: item.getAttribute('role') || '',
      text: textOfElement(item).slice(0, 80),
      className: String(item.className || '').slice(0, 160),
      ariaLabel: String(item.getAttribute('aria-label') || '').slice(0, 80),
      title: String(item.getAttribute('title') || '').slice(0, 80),
      rect: rectSnapshot(rect),
      score,
      distanceToInputRight: input ? Math.round(rectCenter(rect).x - input.getBoundingClientRect().right) : null,
    };
  }

  function rectSnapshot(rect) {
    return {
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      right: Math.round(rect.right),
      bottom: Math.round(rect.bottom),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  }

  function findElementAtSubmitHotspot(inputRect, composerRect) {
    const rect = composerRect || inputRect;
    if (!rect || !inputRect || !composerRect || !isVideoCreationSurface()) return null;
    const points = [
      [rect.right - 34, rect.bottom - 34],
      [rect.right - 48, rect.bottom - 42],
      [rect.right - 28, rect.bottom - 58],
    ];
    for (const [x, y] of points) {
      const hit = document.elementFromPoint(Math.max(0, Math.min(window.innerWidth - 1, x)), Math.max(0, Math.min(window.innerHeight - 1, y)));
      const target = clickableTargetFor(hit);
      if (target instanceof HTMLElement && target.offsetParent !== null && !target.closest(`#${ROOT_ID}`) && isElementWithinComposer(target, inputRect, composerRect, 64) && !isNonSubmitControlText(textOfElement(target))) return target;
    }
    return null;
  }

  function isElementWithinComposer(item, inputRect, composerRect, slack = 72) {
    if (!(item instanceof HTMLElement) || !inputRect || !composerRect) return false;
    if (item.closest(`#${ROOT_ID}`) || isInsideConversationTitleDialog(item)) return false;
    const rect = item.getBoundingClientRect();
    const center = rectCenter(rect);
    if (center.x < composerRect.left - slack || center.x > composerRect.right + slack) return false;
    if (center.y < composerRect.top - Math.min(80, slack) || center.y > composerRect.bottom + slack) return false;
    if (center.y < window.innerHeight * 0.30) return false;
    if (rect.width > Math.max(180, composerRect.width * 0.42) || rect.height > 120) return false;
    return true;
  }

  function findVideoGenerationEntry() {
    const candidates = [...document.querySelectorAll('button, a, [role="button"], [role="tab"], div, span')];
    return candidates
      .filter((item) => {
        if (!(item instanceof HTMLElement) || item.offsetParent === null) return false;
        const text = String(item.innerText || item.textContent || item.getAttribute('aria-label') || '').trim();
        return text && text.length <= 36 && /视频生成|AI\s*视频|Video/i.test(text);
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.bottom + br.right) - (ar.bottom + ar.right);
      })[0] || null;
  }

  function showTaskToast(text, task = null, patch = {}) {
    const root = document.getElementById(ROOT_ID);
    if (!root) return;
    root.dataset.open = 'true';
    const currentTask = task || automationPanelState?.task || null;
    const status = patch.status || automationPanelState?.status || currentTask?.status || '';
    const stage = patch.stage || automationPanelState?.stage || currentTask?.stage || status || 'waiting-result';
    updateAutomationPanel({
      task: currentTask,
      status,
      stage,
      progress: patch.progress ?? automationPanelState?.progress ?? progressForStage(stage, status),
      message: text,
      open: true,
      ...(patch || {}),
    });
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
})();
