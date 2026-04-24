// djust - WebSocket + HTTP Fallback Client

// ============================================================================
// Global Namespace
// ============================================================================

// Create djust namespace at the top to ensure it's available for all exports
window.djust = window.djust || {};

// ============================================================================
// API prefix resolution (#987) — FORCE_SCRIPT_NAME / sub-path mount support
// ============================================================================
// Resolves window.djust.apiPrefix once at bootstrap. Priority:
//   1. Explicit global (set BEFORE client.js loads) — highest.
//   2. <meta name="djust-api-prefix" content="..."> emitted by the
//      {% djust_client_config %} template tag.
//   3. Compile-time default '/djust/api/'.
//
// Companion helper: djust.apiUrl(path) joins prefix + path with slash
// normalization. Used by 48-server-functions.js (djust.call) and will
// be the canonical API URL builder for future client modules.
(function initApiPrefix() {
    // 1. Explicit override wins — developer set it manually before the
    //    bundle loaded (rare, but it's how integrators inject custom
    //    behaviour without patching client.js).
    //
    //    Backward-compat note: an explicit empty string ('') is treated
    //    as "use default" because the meta-tag fallback below also uses
    //    `prefix || '/djust/api/'`. Integrators who want to genuinely
    //    disable the prefix should set a non-empty sentinel like '/'.
    if (typeof window.djust.apiPrefix !== 'undefined' && window.djust.apiPrefix !== null) {
        return;
    }
    // 2. Meta tag emitted by {% djust_client_config %}. reverse()-derived
    //    so it honors FORCE_SCRIPT_NAME and api_patterns(prefix=...).
    let prefix = '';
    try {
        const meta = document.querySelector('meta[name="djust-api-prefix"]');
        if (meta) {
            const raw = meta.getAttribute('content');
            if (raw) prefix = raw.trim();
        }
    } catch (_) { /* SSR / detached DOM — fall through to default */ }
    // 3. Compile-time default.
    window.djust.apiPrefix = prefix || '/djust/api/';
})();

// Helper: join the configured API prefix with a relative path, normalizing
// slashes so '/prefix/' + '/path' doesn't produce '/prefix//path'. Callers
// pass the portion AFTER the prefix; this helper guarantees exactly one
// slash at the junction regardless of whether either side carries one.
//
// Absolute-URL note: this helper does NOT special-case absolute URLs. A
// call like apiUrl('https://evil.com/') would return
// '/djust/api/https://evil.com/' — the concatenation is naive by design.
// All current callers pass relative paths built from encodeURIComponent()
// segments (see 48-server-functions.js), so an attacker-controlled
// absolute URL cannot reach this helper. If a future caller derives
// `path` from user input, it MUST validate or encode it first.
window.djust.apiUrl = function apiUrl(path) {
    const raw = window.djust.apiPrefix || '/djust/api/';
    const normalizedPrefix = raw.endsWith('/') ? raw : raw + '/';
    const p = path == null ? '' : String(path);
    const normalizedPath = p.startsWith('/') ? p.slice(1) : p;
    return normalizedPrefix + normalizedPath;
};

// ============================================================================
// SSE prefix resolution (#992) — same pattern as apiPrefix, different meta tag
// ============================================================================
// Resolves window.djust.ssePrefix once at bootstrap. Priority matches
// apiPrefix: explicit global > <meta name="djust-sse-prefix"> > '/djust/'.
// Used by 03b-sse.js for EventSource + event POST URL construction.
(function initSsePrefix() {
    if (typeof window.djust.ssePrefix !== 'undefined' && window.djust.ssePrefix !== null) {
        return;
    }
    let prefix = '';
    try {
        const meta = document.querySelector('meta[name="djust-sse-prefix"]');
        if (meta) {
            const raw = meta.getAttribute('content');
            if (raw) prefix = raw.trim();
        }
    } catch (_) { /* SSR / detached DOM — fall through to default */ }
    window.djust.ssePrefix = prefix || '/djust/';
})();

// Mirror of apiUrl() for the SSE prefix. Same slash-normalization rules,
// same absolute-URL caveat (callers must encode/validate user input).
window.djust.sseUrl = function sseUrl(path) {
    const raw = window.djust.ssePrefix || '/djust/';
    const normalizedPrefix = raw.endsWith('/') ? raw : raw + '/';
    const p = path == null ? '' : String(path);
    const normalizedPath = p.startsWith('/') ? p.slice(1) : p;
    return normalizedPrefix + normalizedPath;
};

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
