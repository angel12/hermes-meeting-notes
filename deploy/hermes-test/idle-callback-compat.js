// Webapp-only compatibility for hosts lacking the idle callback pair (e.g. Safari).
// Timer fallback: yields to the event loop; it does not claim to detect browser idle time.
// Loaded before the upstream renderer, without weakening its CSP.
(() => {
  if (typeof globalThis.requestIdleCallback === 'function' &&
      typeof globalThis.cancelIdleCallback === 'function') return;

  globalThis.requestIdleCallback = callback => setTimeout(() => {
    callback({ didTimeout: false, timeRemaining: () => 0 });
  }, 1);
  globalThis.cancelIdleCallback = handle => clearTimeout(handle);
})();
