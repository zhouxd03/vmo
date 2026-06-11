(function () {
  'use strict';

  const TARGET_DURATION = 15;
  const TARGET_MODEL = 'seedance_v2.0';
  const STORAGE_KEY = 'codex_doubao_video_duration_choice';
  const MARK = 'data-codex-doubao-cn-15s';
  const STYLE_ID = 'codex-doubao-cn-15s-style';
  let timer = 0;

  function selectedDuration() {
    try {
      return Number(localStorage.getItem(STORAGE_KEY)) || 0;
    } catch (_) {
      return 0;
    }
  }

  function saveDuration(seconds) {
    try {
      if (seconds) localStorage.setItem(STORAGE_KEY, String(seconds));
      else localStorage.removeItem(STORAGE_KEY);
    } catch (_) {}
  }

  function isCompletionUrl(input) {
    const raw = typeof input === 'string'
      ? input
      : (input && (input.url || input.href)) || String(input || '');
    try {
      const url = new URL(raw, location.href);
      return /(^|\.)(doubao|dola)\.com$/.test(url.hostname) && url.pathname === '/chat/completion';
    } catch (_) {
      return /\/chat\/completion(?:\?|$)/.test(raw);
    }
  }

  function parseAbilityParam(value) {
    if (value && typeof value === 'object') return { ...value };
    if (typeof value === 'string' && value.trim()) {
      try {
        const parsed = JSON.parse(value);
        if (parsed && typeof parsed === 'object') return parsed;
      } catch (_) {}
    }
    return {};
  }

  function patchBody(rawBody) {
    if (typeof rawBody !== 'string' || !rawBody.trim()) return { changed: false, body: rawBody };
    if (selectedDuration() !== TARGET_DURATION) return { changed: false, body: rawBody };

    let payload;
    try {
      payload = JSON.parse(rawBody);
    } catch (_) {
      return { changed: false, body: rawBody };
    }

    const ability = payload && payload.chat_ability;
    if (!ability || Number(ability.ability_type) !== 17) return { changed: false, body: rawBody };

    const param = parseAbilityParam(ability.ability_param);
    param.model = TARGET_MODEL;
    param.duration = TARGET_DURATION;
    ability.ability_param = JSON.stringify(param);
    return { changed: true, body: JSON.stringify(payload) };
  }

  function patchFetch() {
    if (typeof window.fetch !== 'function' || window.fetch.__codexDoubaoCn15s) return;
    const originalFetch = window.fetch;

    async function patchedFetch(input, init) {
      try {
        if (!isCompletionUrl(input)) return originalFetch.apply(this, arguments);

        if (init && Object.prototype.hasOwnProperty.call(init, 'body')) {
          const patched = patchBody(init.body);
          if (patched.changed) return originalFetch.call(this, input, { ...init, body: patched.body });
          return originalFetch.apply(this, arguments);
        }

        if (window.Request && input instanceof window.Request && String(input.method || '').toUpperCase() === 'POST') {
          const raw = await input.clone().text();
          const patched = patchBody(raw);
          if (patched.changed) return originalFetch.call(this, new window.Request(input, { body: patched.body }), init);
        }
      } catch (error) {
        console.warn('[Doubao CN 15s] fetch patch failed:', error);
      }
      return originalFetch.apply(this, arguments);
    }

    patchedFetch.__codexDoubaoCn15s = true;
    window.fetch = patchedFetch;
  }

  function patchXhr() {
    const proto = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
    if (!proto || proto.__codexDoubaoCn15s) return;

    const originalOpen = proto.open;
    const originalSend = proto.send;

    proto.open = function (method, url) {
      this.__codexDoubaoCn15sMethod = method;
      this.__codexDoubaoCn15sUrl = url;
      return originalOpen.apply(this, arguments);
    };

    proto.send = function (body) {
      try {
        if (
          String(this.__codexDoubaoCn15sMethod || '').toUpperCase() === 'POST' &&
          isCompletionUrl(this.__codexDoubaoCn15sUrl)
        ) {
          const patched = patchBody(body);
          if (patched.changed) return originalSend.call(this, patched.body);
        }
      } catch (error) {
        console.warn('[Doubao CN 15s] xhr patch failed:', error);
      }
      return originalSend.apply(this, arguments);
    };

    proto.__codexDoubaoCn15s = true;
  }

  function visible(el) {
    if (!el || !el.isConnected) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }

  function text(el) {
    return String((el && el.textContent) || '').replace(/\s+/g, '').replace(/[✓✔√]/g, '').trim();
  }

  function exactDuration(el) {
    const match = text(el).match(/^(5|10|15)(s|秒)$/);
    return match ? Number(match[1]) : 0;
  }

  function installStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      [${MARK}="option"] {
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
        gap: 26px !important;
        cursor: pointer !important;
      }
      [${MARK}="option"] [${MARK}-check] {
        margin-left: auto;
        flex: 0 0 auto;
        color: currentColor;
        font-size: 18px;
        line-height: 1;
      }
      [${MARK}-native-check="hidden"] {
        visibility: hidden !important;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  function closestClickable(el) {
    let current = el && el.nodeType === Node.TEXT_NODE ? el.parentElement : el;
    for (let i = 0; current && i < 7; i += 1, current = current.parentElement) {
      const role = current.getAttribute && current.getAttribute('role');
      if (
        current.tagName === 'BUTTON' ||
        role === 'button' ||
        role === 'menuitem' ||
        role === 'option' ||
        current.tabIndex >= 0 ||
        /pointer/.test(String(getComputedStyle(current).cursor || ''))
      ) {
        return current;
      }
    }
    return el && el.parentElement;
  }

  function findDurationMenuRoot() {
    if (!document.body) return null;
    const candidates = Array.from(document.querySelectorAll('[role="menu"], [data-slot*="dropdown-menu"], div'))
      .filter(visible)
      .filter(el => {
        const t = text(el);
        if (t.length > 220) return false;
        if (!/时长/.test(t) || !/5s/.test(t) || !/10s/.test(t)) return false;
        return !/Seedance/.test(t) && !/比例/.test(t);
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (ar.width * ar.height) - (br.width * br.height);
      });
    return candidates[0] || null;
  }

  function optionTextNodes(root) {
    const nodes = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return /^\s*(5|10|15)(s|秒)\s*$/.test(node.nodeValue || '')
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      },
    });
    while (walker.nextNode()) nodes.push(walker.currentNode);
    return nodes;
  }

  function findMenuOptions(root) {
    const out = [];
    for (const node of optionTextNodes(root)) {
      const parent = node.parentElement;
      if (!visible(parent)) continue;
      const item = closestClickable(parent);
      if (!item || !root.contains(item)) continue;
      if (!exactDuration(item)) continue;
      if (out.some(existing => existing === item || existing.contains(item))) continue;
      for (let i = out.length - 1; i >= 0; i -= 1) {
        if (item.contains(out[i])) out.splice(i, 1);
      }
      out.push(item);
    }
    return out;
  }

  function durationTextNode(el) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      if (/^\s*(5|10|15)(s|秒)\s*$/.test(walker.currentNode.nodeValue || '')) return walker.currentNode;
    }
    return null;
  }

  function removeOwnChecks(el) {
    el.querySelectorAll(`[${MARK}-check]`).forEach(node => node.remove());
  }

  function setNativeChecksHidden(item, hidden) {
    item.querySelectorAll(`[${MARK}-native-check]`).forEach(node => node.removeAttribute(`${MARK}-native-check`));
    if (!hidden) return;

    item.querySelectorAll('svg,img,canvas').forEach(node => node.setAttribute(`${MARK}-native-check`, 'hidden'));

    const nodes = [];
    const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return /[✓✔√]/.test(node.nodeValue || '')
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      },
    });
    while (walker.nextNode()) nodes.push(walker.currentNode);

    for (const node of nodes) {
      const parent = node.parentElement;
      if (parent && /^[\s✓✔√]+$/.test(parent.textContent || '')) {
        parent.setAttribute(`${MARK}-native-check`, 'hidden');
      }
    }
  }

  function scrubClone(clone) {
    clone.removeAttribute('aria-selected');
    clone.removeAttribute('aria-checked');
    clone.removeAttribute('checked');
    clone.removeAttribute('selected');
    removeOwnChecks(clone);
    clone.removeAttribute(`${MARK}-active`);
    const node = durationTextNode(clone);
    if (node) node.nodeValue = '15s';
    else clone.textContent = '15s';
  }

  function setToolbarText(seconds) {
    const next = seconds === TARGET_DURATION ? '15s' : `${seconds}s`;
    const nodes = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return /^\s*(5|10|15)(s|秒)\s*$/.test(node.nodeValue || '')
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      },
    });
    while (walker.nextNode()) nodes.push(walker.currentNode);

    for (const node of nodes) {
      const parent = node.parentElement;
      if (!visible(parent)) continue;
      const click = closestClickable(parent);
      if (!click || !visible(click)) continue;
      let current = click.parentElement;
      for (let i = 0; current && i < 7; i += 1, current = current.parentElement) {
        const t = text(current);
        if (/Seedance/.test(t) && /比例/.test(t) && t.length < 260) {
          node.nodeValue = next;
          if (!click.hasAttribute(`${MARK}-trigger`)) {
            click.setAttribute(`${MARK}-trigger`, '1');
            click.addEventListener('click', () => {
              setTimeout(inject15Option, 80);
              setTimeout(inject15Option, 240);
            }, true);
          }
          break;
        }
      }
    }
  }

  function renderChecks(options) {
    const selected15 = selectedDuration() === TARGET_DURATION;
    for (const item of options) {
      const value = exactDuration(item);
      if (value === 5 || value === 10) setNativeChecksHidden(item, selected15);
      if (value === TARGET_DURATION) {
        removeOwnChecks(item);
        setNativeChecksHidden(item, !selected15);
      }
      item.removeAttribute(`${MARK}-active`);
    }
  }

  function bindNative(item, seconds) {
    if (item.hasAttribute(`${MARK}-native`)) return;
    item.setAttribute(`${MARK}-native`, String(seconds));
    item.addEventListener('click', () => {
      saveDuration(0);
      setToolbarText(seconds);
      setTimeout(inject15Option, 80);
    }, true);
  }

  function bind15(item) {
    if (item.hasAttribute(MARK)) return;
    item.setAttribute(MARK, 'option');
    item.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      saveDuration(TARGET_DURATION);
      setToolbarText(TARGET_DURATION);
      setTimeout(() => document.body && document.body.click(), 30);
    }, true);
  }

  function inject15Option() {
    const root = findDurationMenuRoot();
    if (!root) return;
    const options = findMenuOptions(root);
    if (!options.length) return;

    for (const item of options) {
      const value = exactDuration(item);
      if (value === 5 || value === 10) bindNative(item, value);
      if (value === TARGET_DURATION) bind15(item);
    }

    if (!options.some(item => exactDuration(item) === TARGET_DURATION)) {
      const after = options.find(item => exactDuration(item) === 10) || options[options.length - 1];
      const template = options.find(item => exactDuration(item) === 5) || after;
      if (!after || !template || !after.parentElement) return;
      const clone = template.cloneNode(true);
      scrubClone(clone);
      bind15(clone);
      after.parentElement.insertBefore(clone, after.nextSibling);
      options.push(clone);
    }

    renderChecks(options);
  }

  function tick() {
    if (selectedDuration() === TARGET_DURATION) setToolbarText(TARGET_DURATION);
    inject15Option();
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(tick, 100);
  }

  function start() {
    installStyle();
    tick();
    const observer = new MutationObserver(schedule);
    const waitBody = () => {
      if (!document.body) return setTimeout(waitBody, 200);
      observer.observe(document.body, { childList: true, subtree: true, characterData: true });
      schedule();
    };
    waitBody();
  }

  patchFetch();
  patchXhr();

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
