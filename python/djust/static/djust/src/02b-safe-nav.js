
// ============================================================================
// Safe navigation target validation (security finding #16)
// ============================================================================
//
// Single shared scheme/origin guard applied at EVERY client-side navigation
// sink that assigns a server- or data-derived target to `window.location.href`
// (WS `navigate`, SSE `navigate`, and the live_patch/live_redirect cross-origin
// fallbacks). Both transports route through THIS function so the guard cannot
// drift apart again (#1646 — structural cure over N inline copies).
//
// Threat model: an attacker who influences a navigation target (a wire-protocol
// breach, a server bug, or a developer foot-gun such as
// `self.live_redirect(request.GET.get("next"))`) must not be able to pivot to
// open-redirect (CWE-601) or `javascript:` / `data:` DOM-XSS (CWE-79).
// Closes CodeQL js/client-side-unvalidated-url-redirection at every sink.
//
// Contract — `safeNavigationTarget(value)` returns a string SAFE to assign to
// `window.location.href`, or `null` if the target must be rejected:
//
//   - SAME-ORIGIN absolute path ("/foo", "/foo?x=1#h") → re-resolved against
//     window.location.origin and returned as pathname+search+hash. Accepted
//     ONLY if it genuinely resolves same-origin. Protocol-relative
//     ("//evil.com/x") and backslash/control-char tricks the WHATWG URL
//     parser normalizes off-origin ("/\evil.com", "/\t/evil",
//     "/\n//evil") are REJECTED.
//   - Absolute http:/https: URL ("https://sister.example/x") → returned
//     normalized via new URL(). This is the legitimate #1599 cross-origin
//     sister-site case.
//   - Everything else → null: javascript:/data:/vbscript:/blob:/file: schemes,
//     any opaque-origin result, unparseable / empty / non-string input.
//
// CSP-strict: pure function, no inline scripts, no DOM writes. Published via a
// `window.djust.*` member assignment so cross-module callers reach it by member
// access (minification- and cross-IIFE-lint safe).

(function () {
    window.djust = window.djust || {};

    function safeNavigationTarget(value) {
        // Reject empty / non-string up front.
        if (typeof value !== 'string' || value.length === 0) {
            if (globalThis.djustDebug) {
                console.warn('[djust] navigation target rejected (not a non-empty string): %o', value);
            }
            return null;
        }

        // Same-origin absolute path: must START with '/' (intent preserved
        // from the original — bare relatives like "dashboard" stay rejected).
        // A raw prefix check is NOT enough: the WHATWG URL parser normalizes
        // '\' → '/' and strips ASCII tab/newline, so "/\evil.com",
        // "/\/evil.com", "/\t/evil", "/\n//evil" all resolve CROSS-ORIGIN even
        // though charAt(1) !== '/'. Canonicalize the way the sink does
        // (#1825 — validate AFTER normalize): resolve against our own origin
        // and accept ONLY if the result is genuinely same-origin.
        if (value.charAt(0) === '/') {
            let resolved;
            try {
                resolved = new URL(value, window.location.origin);
            } catch (_e) {
                if (globalThis.djustDebug) {
                    console.warn('[djust] navigation target rejected (unparseable path): %s', value);
                }
                return null;
            }
            if (resolved.origin === window.location.origin) {
                return resolved.pathname + resolved.search + resolved.hash;
            }
            // Resolved off-origin (e.g. "/\evil.com" → evil.com) → reject.
            if (globalThis.djustDebug) {
                console.warn('[djust] navigation target rejected (resolves cross-origin %s): %s', resolved.origin, value);
            }
            return null;
        }

        // Absolute URL: parse and allow only http:/https:. new URL() throws on
        // unparseable input; javascript:/data:/blob:/file:/vbscript: parse but
        // yield a disallowed protocol and/or an opaque ('null') origin.
        let url;
        try {
            url = new URL(value);
        } catch (_e) {
            if (globalThis.djustDebug) {
                console.warn('[djust] navigation target rejected (unparseable): %s', value);
            }
            return null;
        }

        // Opaque-origin schemes (javascript:, data:, blob:, file: in many
        // engines) resolve to origin === 'null'. Reject defensively even if a
        // future engine widens the http/https allow-list above.
        if (url.origin === 'null') {
            if (globalThis.djustDebug) {
                console.warn('[djust] navigation target rejected (opaque origin): %s', value);
            }
            return null;
        }

        if (url.protocol === 'http:' || url.protocol === 'https:') {
            return url.toString();
        }

        if (globalThis.djustDebug) {
            console.warn('[djust] navigation target rejected (scheme %s): %s', url.protocol, value);
        }
        return null;
    }

    window.djust.safeNavigationTarget = safeNavigationTarget;
})();
