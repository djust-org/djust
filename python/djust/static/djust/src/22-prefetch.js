
// ============================================================================
// Link Prefetch — hover-based <link rel="prefetch"> injection
// ============================================================================
//
// When the user hovers over an anchor, inject a <link rel="prefetch"> so the
// browser can warm-start the request before the click lands.
//
// Uses pointerenter with capture:true so it fires before any element handler.
// IMPORTANT: text nodes and comment nodes are NOT Elements and do not have
// .closest(); guard with instanceof Element before calling it.

(function () {
    'use strict';

    var prefetched = new Set();

    document.addEventListener('pointerenter', function (event) {
        if (!(event.target instanceof Element)) return;
        var link = event.target.closest('a[href]');
        if (!link) return;

        var href = link.getAttribute('href');
        // Only prefetch same-origin, path-based URLs
        if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;

        try {
            var url = new URL(href, window.location.href);
            if (url.origin !== window.location.origin) return;
            if (prefetched.has(url.href)) return;
            prefetched.add(url.href);

            var el = document.createElement('link');
            el.rel = 'prefetch';
            el.href = url.href;
            document.head.appendChild(el);

            if (globalThis.djustDebug) console.log('[LiveView] prefetch:', url.href);
        } catch (e) {
            // Ignore unparseable hrefs
        }
    }, true);

    // Expose for testing
    window.djust._prefetchedUrls = prefetched;
})();
