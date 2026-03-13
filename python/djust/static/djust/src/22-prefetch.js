// ============================================================================
// Prefetch on Hover
// ============================================================================
// Posts PREFETCH messages to the service worker when users hover over links.
// Only same-origin links are prefetched; each URL is prefetched at most once.
// The set is cleared on SPA navigation so links on the new view are re-eligible.

(function () {
    var _prefetched = new Set();

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
            var url = new URL(link.href, location.origin);
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
        var link = event.target.closest('a');
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

    // Expose for testing and for navigation to clear on SPA transition
    window.djust = window.djust || {};
    window.djust._prefetch = {
        _prefetched: _prefetched,
        _shouldPrefetch: _shouldPrefetch,
        clear: function () { _prefetched.clear(); }
    };
})();
