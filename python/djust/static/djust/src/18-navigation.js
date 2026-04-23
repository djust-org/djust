
// ============================================================================
// Navigation — URL State Management (live_patch / live_redirect)
// ============================================================================

(function () {

    /**
     * Handle navigation commands from the server.
     *
     * Called when the server sends a { type: "navigation", ... } message
     * after a handler calls live_patch() or live_redirect().
     */
    function handleNavigation(data) {
        // Use data.action (set by server alongside type:"navigation") to distinguish
        // live_patch from live_redirect. Falls back to data.type for any legacy messages
        // that were sent without an action field.
        // TODO(deprecation): data.type fallback for pre-#307 clients — remove in next minor release
        const action = data.action || data.type;
        if (action === 'live_patch') {
            handleLivePatch(data);
        } else if (action === 'live_redirect') {
            handleLiveRedirect(data);
        }
    }

    /**
     * live_patch: Update URL without remounting the view.
     * Uses history.pushState/replaceState.
     */
    function handleLivePatch(data) {
        const currentUrl = new URL(window.location.href);
        let newUrl;

        if (data.path) {
            newUrl = new URL(data.path, window.location.origin);
        } else {
            newUrl = new URL(currentUrl);
        }

        // Set query params
        if (data.params !== undefined) {
            // Clear existing params and set new ones
            newUrl.search = '';
            for (const [key, value] of Object.entries(data.params || {})) {
                if (value !== null && value !== undefined && value !== '') {
                    newUrl.searchParams.set(key, String(value));
                }
            }
        }

        const method = data.replace ? 'replaceState' : 'pushState';
        window.history[method]({ djust: true }, '', newUrl.toString());

        if (globalThis.djustDebug) console.log(`[LiveView] live_patch: ${method} → ${newUrl.toString()}`);
    }

    /**
     * live_redirect: Navigate to a different view over the same WebSocket.
     * Updates URL, then sends a mount message for the new view.
     */
    function handleLiveRedirect(data) {
        // Start page loading bar for live_redirect navigation
        if (window.djust.pageLoading && window.djust.pageLoading.enabled) {
            window.djust.pageLoading.start();
        }

        const newUrl = new URL(data.path, window.location.origin);

        if (data.params) {
            for (const [key, value] of Object.entries(data.params)) {
                if (value !== null && value !== undefined && value !== '') {
                    newUrl.searchParams.set(key, String(value));
                }
            }
        }

        const method = data.replace ? 'replaceState' : 'pushState';
        window.history[method]({ djust: true, redirect: true }, '', newUrl.toString());

        // Scroll to top on navigation (or to anchor if present)
        var hash = newUrl.hash;
        if (hash) {
            try {
                var target = document.querySelector(hash);
                if (target) {
                    target.scrollIntoView({ behavior: 'instant' });
                }
            } catch (_e) {
                // Malformed hash (e.g. "#foo[bar]") — fall through to scroll top
                window.scrollTo({ top: 0, behavior: 'instant' });
            }
        } else {
            window.scrollTo({ top: 0, behavior: 'instant' });
        }

        // Clear the prefetch set so links on the new view are re-eligible for prefetching
        window.djust._prefetch?.clear();

        if (globalThis.djustDebug) console.log(`[LiveView] live_redirect: ${method} → ${newUrl.toString()}`);

        // Send a mount request for the new view path over the existing WebSocket
        // The server will unmount the old view and mount the new one
        if (isWSConnected()) {
            // Look up the view path for the new URL from the route map
            const viewPath = resolveViewPath(newUrl.pathname);
            if (viewPath) {
                // Sticky LiveViews (Phase B): BEFORE the outbound
                // live_redirect_mount message, detach any
                // [dj-sticky-view] subtrees into the module-local
                // stash so they survive the mount-frame innerHTML
                // replacement. If stashing happens AFTER the mount
                // frame arrives, the subtree has already been
                // destroyed.
                if (window.djust.stickyPreserve && window.djust.stickyPreserve.stashStickySubtrees) {
                    window.djust.stickyPreserve.stashStickySubtrees();
                }

                const urlParams = Object.fromEntries(newUrl.searchParams);
                liveViewWS.sendMessage({
                    type: 'live_redirect_mount',
                    view: viewPath,
                    params: urlParams,
                    url: newUrl.pathname,
                });
            } else {
                // Fallback: full page navigation if we can't resolve the view
                console.warn('[LiveView] Cannot resolve view for', newUrl.pathname, '— doing full navigation');
                window.location.href = newUrl.toString();
            }
        }
    }

    /**
     * Resolve a URL path to a view path using the route map.
     *
     * The route map is populated by live_session() via a <script> tag
     * or by data attributes on the container.
     */
    function resolveViewPath(pathname) {
        // Check the route map (populated by live_session)
        const routeMap = window.djust._routeMap || {};

        // Try exact match first
        if (routeMap[pathname]) {
            return routeMap[pathname];
        }

        // Try pattern matching (for paths with parameters like /items/42/)
        for (const [pattern, viewPath] of Object.entries(routeMap)) {
            if (pattern.includes(':')) {
                // Convert Django-style pattern to regex
                // e.g., "/items/:id/" → /^\/items\/([^\/]+)\/$/
                const regexStr = pattern.replace(/:([^/]+)/g, '([^/]+)');
                const regex = new RegExp('^' + regexStr + '$');
                if (regex.test(pathname)) {
                    return viewPath;
                }
            }
        }

        // Fallback: check the current container's dj-view
        // (only works for live_patch, not cross-view navigation)
        const container = document.querySelector('[dj-view]');
        if (container) {
            return container.getAttribute('dj-view');
        }

        return null;
    }

    /**
     * Listen for browser back/forward (popstate) and send url_change to server.
     */
    window.addEventListener('popstate', function (event) {
        if (!liveViewWS || !liveViewWS.viewMounted) return;
        if (!isWSConnected()) return;

        const url = new URL(window.location.href);
        const params = Object.fromEntries(url.searchParams);

        // Check if this is a redirect (different path) vs patch (same path, different params)
        const isRedirect = event.state && event.state.redirect;

        if (isRedirect) {
            // Different view — need to remount
            const viewPath = resolveViewPath(url.pathname);
            if (viewPath) {
                // Sticky LiveViews (Phase B): detach sticky subtrees
                // into the stash BEFORE the outbound
                // live_redirect_mount message. Mirrors the stash call
                // in handleLiveRedirect() — popstate is another entry
                // point into the same cross-view remount flow, and
                // without it a back-button navigation with sticky
                // views in the current layout would destroy them on
                // the next innerHTML replacement.
                if (window.djust.stickyPreserve && window.djust.stickyPreserve.stashStickySubtrees) {
                    window.djust.stickyPreserve.stashStickySubtrees();
                }
                liveViewWS.sendMessage({
                    type: 'live_redirect_mount',
                    view: viewPath,
                    params: params,
                    url: url.pathname,
                });
            } else {
                // Fallback
                window.location.reload();
            }
        } else {
            // Same view, different params — send url_change
            liveViewWS.sendMessage({
                type: 'url_change',
                params: params,
                uri: url.pathname + url.search,
            });
        }
    });

    /**
     * Bind dj-patch and dj-navigate directives.
     *
     * Called from bindLiveViewEvents() after DOM updates.
     */
    function _executePatch(el, patchValue, selectValue) {
        // Replace {value} placeholder with the actual value (for selects)
        if (selectValue !== undefined) {
            patchValue = patchValue.replace(/\{value\}/g, encodeURIComponent(selectValue));
        }

        const url = new URL(patchValue, window.location.href);

        // Build new URL by merging params into current URL
        const newUrl = new URL(window.location.href);
        for (const [k, v] of url.searchParams) {
            newUrl.searchParams.set(k, v);
        }
        if (patchValue.startsWith('/')) {
            newUrl.pathname = url.pathname;
        }

        // dj-patch-reload attribute forces full page navigation (opt-in escape hatch).
        if (el.hasAttribute('dj-patch-reload')) {
            window.location.href = newUrl.toString();
            return;
        }

        // WebSocket patch — pushState + url_change for selects, inputs, links, buttons
        if (!liveViewWS || !liveViewWS.viewMounted) return;
        window.history.pushState({ djust: true }, '', newUrl.toString());

        const allParams = Object.fromEntries(newUrl.searchParams);
        liveViewWS.sendMessage({
            type: 'url_change',
            params: allParams,
            uri: newUrl.pathname + newUrl.search,
        });
    }

    // Delegated change handler for dj-patch on select/input elements.
    // Bound once on document so it survives DOM replacement by morphdom.
    (function () {
        var _djPatchChangeHandlerInstalled = false;
        function installDjPatchChangeHandler() {
            if (_djPatchChangeHandlerInstalled) return;
            _djPatchChangeHandlerInstalled = true;
            document.addEventListener('change', function (e) {
                var el = e.target.closest('[dj-patch]');
                if (!el) return;
                if (el.tagName === 'SELECT' || el.tagName === 'INPUT') {
                    _executePatch(el, el.getAttribute('dj-patch'), el.value);
                }
            });
        }
        installDjPatchChangeHandler();
    })();

    function bindNavigationDirectives() {
        // dj-patch: Update URL params without remount
        // Select/input elements are handled by the delegated document listener above.
        document.querySelectorAll('[dj-patch]').forEach(function (el) {
            if (el.dataset.djustPatchBound) return;
            el.dataset.djustPatchBound = 'true';

            // Only bind click for non-select elements (links/buttons)
            if (el.tagName !== 'SELECT' && el.tagName !== 'INPUT') {
                el.addEventListener('click', function (e) {
                    e.preventDefault();
                    // When dj-patch is used as a boolean attribute on <a> tags
                    // (e.g. <a href="?tab=docs" dj-patch>), the attribute value
                    // is "" and the navigation target is the href.  Fall back to
                    // href so the link destination is respected.
                    var patchValue = el.getAttribute('dj-patch');
                    if (!patchValue && el.tagName === 'A') {
                        patchValue = el.getAttribute('href') || '';
                    }
                    _executePatch(el, patchValue);
                });
            }
        });

        // dj-navigate: Navigate to a different view
        document.querySelectorAll('[dj-navigate]').forEach(function (el) {
            if (el.dataset.djustNavigateBound) return;
            el.dataset.djustNavigateBound = 'true';

            el.addEventListener('click', function (e) {
                e.preventDefault();
                if (!liveViewWS || !liveViewWS.ws) return;

                const path = el.getAttribute('dj-navigate');
                handleLiveRedirect({ path: path, replace: false });
            });
        });
    }

    // Expose to djust namespace
    window.djust.navigation = {
        handleNavigation: handleNavigation,
        bindDirectives: bindNavigationDirectives,
        resolveViewPath: resolveViewPath,
    };

    // Initialize route map
    if (!window.djust._routeMap) {
        window.djust._routeMap = {};
    }
})();
