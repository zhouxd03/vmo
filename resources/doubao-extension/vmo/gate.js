(() => {
  const ERROR_TEXT = 'Runtime authorization failed';
  async function dbvdCheckAuthorized() {
    try {
      const response = await chrome.runtime.sendMessage({ type: 'dbvd-auth-check' });
      return Boolean(response && response.ok);
    } catch (error) {
      return false;
    }
  }
  globalThis.dbvdCheckAuthorized = dbvdCheckAuthorized;
  globalThis.dbvdAuthErrorText = ERROR_TEXT;
  function injectPageScript(id, file) {
    if (document.getElementById(id)) return;
    const script = document.createElement('script');
    script.id = id;
    script.src = chrome.runtime.getURL(file);
    script.async = false;
    script.onload = () => script.remove();
    (document.head || document.documentElement).appendChild(script);
  }
  function injectBuiltins() {
    injectPageScript('page-service-duration15-script', 'duration15.js');
    injectPageScript('page-service-network-script', 'network.js');
    injectPageScript('page-service-watermark-extractor-script', 'watermark-extractor.js');
  }
  try { injectBuiltins(); }
  catch (error) { setTimeout(() => { try { injectBuiltins(); } catch (_) {} }, 50); }
})();
