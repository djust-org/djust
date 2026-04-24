// ============================================================================
// Prefetch on Hover
// ============================================================================
// Posts PREFETCH messages to the service worker when users hover over links.
// Only same-origin links are prefetched; each URL is prefetched at most once.
// The set is cleared on SPA navigation so links on the new view are re-eligible.

(function () {
    const _prefetched = new Set();

    function _shouldPrefetch(link) {
        // No SW controller available
        if (!navigator.serviceWorker || !navigator.serviceWorker.controller) {
            return false;
        }
        // Respect save-data preference
        if (navigator.connection && navigator.connection.saveData) {
            return false;
        }
        // Element opted out
        if (link.hasAttribute('data-no-prefetch')) {
            return false;
        }
        // Must have href and be same-origin
        if (!link.href) {
            return false;
        }
        try {
            const url = new URL(link.href, location.origin);
            if (url.origin !== location.origin) {
                return false;
            }
        } catch (e) {
            return false;
        }
        // Already prefetched
        if (_prefetched.has(link.href)) {
            return false;
        }
        return true;
    }

    document.addEventListener('pointerenter', function (event) {
        if (!(event.target instanceof Element)) return;
        const link = event.target.closest('a');
        if (!link || !_shouldPrefetch(link)) {
            return;
        }
        _prefetched.add(link.href);
        if (globalThis.djustDebug) console.log('[djust] Prefetching:', link.href);
        navigator.serviceWorker.controller.postMessage({
            type: 'PREFETCH',
            url: link.href
        });
    }, true);

    // Expose for testing and for navigation to clear on SPA transition.
    // clear() also clears the intent-prefetch set if the intent IIFE has
    // installed; otherwise the intent module sets its own clear bridge.
    window.djust = window.djust || {};
    window.djust._prefetch = {
        _prefetched: _prefetched,
        _shouldPrefetch: _shouldPrefetch,
        clear: function () {
            _prefetched.clear();
            if (window.djust && window.djust._intentPrefetch
                && typeof window.djust._intentPrefetch.clear === 'function') {
                window.djust._intentPrefetch.clear();
            }
        }
    };
})();

// ============================================================================
// Intent-based prefetch (dj-prefetch)
// ============================================================================
// Layer on top of the SW-mediated hover prefetch above. This IIFE:
//   - fires on `mouseenter` with a 65 ms debounce (cancelled by `mouseleave`),
//   - fires on `touchstart` immediately (no debounce — mobile users commit fast),
//   - injects <link rel="prefetch" as="document"> so the browser manages the
//     cache lifecycle (falls back to low-priority fetch when relList doesn't
//     advertise 'prefetch').
// Links opt in via the `dj-prefetch` attribute. `dj-prefetch="false"` opts out.
// Only same-origin URLs are prefetched; each URL fires at most once.

(function () {
    const HOVER_DEBOUNCE_MS = 65;
    const _intentPrefetched = new Set();
    const _pending = new WeakMap(); // link -> {timer, controller}

    function _supportsLinkPrefetch() {
        try {
            return document.createElement('link').relList.supports('prefetch');
        } catch (_) { return false; }
    }
    const _canUseLinkRel = _supportsLinkPrefetch();

    function _shouldIntentPrefetch(link) {
        if (!link || !link.hasAttribute || !link.hasAttribute('dj-prefetch')) return false;
        const v = link.getAttribute('dj-prefetch');
        if (v === 'false') return false;
        if (navigator.connection && navigator.connection.saveData) return false;
        if (!link.href) return false;
        try {
            const url = new URL(link.href, location.origin);
            if (url.origin !== location.origin) return false;
        } catch (_) { return false; }
        if (_intentPrefetched.has(link.href)) return false;
        return true;
    }

    function _doPrefetch(link) {
        _intentPrefetched.add(link.href);
        if (globalThis.djustDebug) console.log('[djust] Intent prefetch:', link.href);
        if (_canUseLinkRel) {
            const el = document.createElement('link');
            el.rel = 'prefetch';
            el.href = link.href;
            el.setAttribute('as', 'document');
            document.head.appendChild(el);
            return { cancel: function () { /* browser handles; no-op */ } };
        }
        const ctrl = new AbortController();
        try {
            fetch(link.href, {
                credentials: 'same-origin',
                priority: 'low',
                signal: ctrl.signal,
            }).catch(function () { /* aborts + prefetch failures are non-fatal */ });
        } catch (_) { /* fetch unsupported — silently no-op */ }
        return { cancel: function () { ctrl.abort(); } };
    }

    function _onEnter(event) {
        if (!(event.target instanceof Element)) return;
        const link = event.target.closest && event.target.closest('a[dj-prefetch]');
        if (!link || !_shouldIntentPrefetch(link)) return;
        if (_pending.has(link)) return;
        const timer = setTimeout(function () {
            _pending.delete(link);
            _doPrefetch(link);
        }, HOVER_DEBOUNCE_MS);
        _pending.set(link, { timer: timer, controller: null });
    }

    function _onLeave(event) {
        if (!(event.target instanceof Element)) return;
        const link = event.target.closest && event.target.closest('a[dj-prefetch]');
        if (!link) return;
        const p = _pending.get(link);
        if (!p) return;
        clearTimeout(p.timer);
        _pending.delete(link);
    }

    function _onTouch(event) {
        if (!(event.target instanceof Element)) return;
        const link = event.target.closest && event.target.closest('a[dj-prefetch]');
        if (!link || !_shouldIntentPrefetch(link)) return;
        _doPrefetch(link); // no debounce on explicit touch
    }

    document.addEventListener('mouseenter', _onEnter, true);
    document.addEventListener('mouseleave', _onLeave, true);
    document.addEventListener('touchstart', _onTouch, { capture: true, passive: true });

    window.djust = window.djust || {};
    window.djust._intentPrefetch = {
        _prefetched: _intentPrefetched,
        _shouldIntentPrefetch: _shouldIntentPrefetch,
        _onEnter: _onEnter,
        _onLeave: _onLeave,
        _onTouch: _onTouch,
        HOVER_DEBOUNCE_MS: HOVER_DEBOUNCE_MS,
        clear: function () { _intentPrefetched.clear(); },
    };
})();
