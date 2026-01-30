// djust - WebSocket + HTTP Fallback Client

// ============================================================================
// Global Namespace
// ============================================================================

// Create djust namespace at the top to ensure it's available for all exports
window.djust = window.djust || {};

// ============================================================================
// Double-Load Guard
// ============================================================================
// Prevent double execution when client.js is included in both base template
// (for TurboNav compatibility) and injected by LiveView.
if (window._djustClientLoaded) {
    console.log('[LiveView] client.js already loaded, skipping duplicate initialization');
} else {
window._djustClientLoaded = true;

// ============================================================================
// Security Constants
// ============================================================================
// Dangerous keys that could cause prototype pollution attacks
const UNSAFE_KEYS = ['__proto__', 'constructor', 'prototype'];
