// djust - WebSocket + HTTP Fallback Client

// ============================================================================
// Global Namespace
// ============================================================================

// Create djust namespace at the top to ensure it's available for all exports
window.djust = window.djust || {};

// ============================================================================
// djLog: debug-gated console.log (#761)
// ============================================================================
// Per djust/CLAUDE.md: "No console.log in JS without if (globalThis.djustDebug)
// guard". Rather than sprinkle that conditional at every callsite, client.js
// uses djLog which checks the flag once per call. Tree-shakes nothing (we
// still evaluate the args in the call), but at least the console stays clean
// when djustDebug is false.
//
// console.warn / console.error are NOT guarded — those indicate real problems
// and should be visible in prod.
window.djLog = function djLog(...args) {
    if (globalThis.djustDebug) console.log(...args);
};

// ============================================================================
// Double-Load Guard
// ============================================================================
// Prevent double execution when client.js is included in both base template
// (for TurboNav compatibility) and injected by LiveView.
if (window._djustClientLoaded) {
    if (globalThis.djustDebug) console.log('[LiveView] client.js already loaded, skipping duplicate initialization');
} else {
window._djustClientLoaded = true;

// ============================================================================
// Security Constants
// ============================================================================
// Dangerous keys that could cause prototype pollution attacks
const UNSAFE_KEYS = ['__proto__', 'constructor', 'prototype'];

// ============================================================================
// dj-cloak CSS injection — hide [dj-cloak] elements until mount completes
// ============================================================================
(function() {
    const style = document.createElement('style');
    style.textContent = '[dj-cloak] { display: none !important; }';
    document.head.appendChild(style);
})();
