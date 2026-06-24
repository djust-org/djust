
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

        // pushState forbids cross-origin URLs (the browser throws
        // SecurityError). When data.path is an absolute URL whose origin
        // differs from the current page, fall back to a full-page
        // navigation — that's the caller's intent. (#1599)
        if (newUrl.origin !== window.location.origin) {
            // Validate scheme/origin before the hard navigation. A
            // `javascript:`/`data:` data.path parses to an opaque origin that
            // is `!== location.origin`, so it lands here — reject it (finding
            // #16, #1646). Legit absolute http(s) sister-site URLs pass.
            const safe = window.djust.safeNavigationTarget(newUrl.toString());
            if (!safe) {
                if (globalThis.djustDebug) {
                    console.warn('[LiveView] live_patch rejected unsafe target: %s', newUrl.toString());
                }
                return;
            }
            if (globalThis.djustDebug) {
                console.log(
                    '[LiveView] live_patch cross-origin → full-page nav: %s',
                    safe,
                );
            }
            window.location.href = safe; // codeql[js/xss] -- validated via safeNavigationTarget
            return;
        }

        const method = data.replace ? 'replaceState' : 'pushState';
        // eslint-disable-next-line security/detect-object-injection
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

        // pushState forbids cross-origin URLs (the browser throws
        // SecurityError). When data.path is an absolute URL whose origin
        // differs from the current page (e.g. a dj-navigate link pointing
        // at a sister site), fall back to a full-page navigation. (#1599)
        if (newUrl.origin !== window.location.origin) {
            // Validate scheme/origin before the hard navigation. A
            // `javascript:`/`data:` data.path parses to an opaque origin that
            // is `!== location.origin`, so it lands here — reject it (finding
            // #16, #1646). Legit absolute http(s) sister-site URLs pass.
            const safe = window.djust.safeNavigationTarget(newUrl.toString());
            if (!safe) {
                if (globalThis.djustDebug) {
                    console.warn('[LiveView] live_redirect rejected unsafe target: %s', newUrl.toString());
                }
                // Stop the page-loading bar we started above.
                if (window.djust.pageLoading && window.djust.pageLoading.enabled) {
                    window.djust.pageLoading.stop?.();
                }
                return;
            }
            if (globalThis.djustDebug) {
                console.log(
                    '[LiveView] live_redirect cross-origin → full-page nav: %s',
                    safe,
                );
            }
            // Stop the page-loading bar we started above; the full nav
            // will trigger the browser's own progress indicator.
            if (window.djust.pageLoading && window.djust.pageLoading.enabled) {
                window.djust.pageLoading.stop?.();
            }
            window.location.href = safe; // codeql[js/xss] -- validated via safeNavigationTarget
            return;
        }

        // Clear the prefetch set so links on the new view are re-eligible for
        // prefetching. Done here (same-origin, both the SPA-mount and the
        // full-page-nav branch) — on a full nav the JS context is torn down
        // anyway so this is harmless; on the SPA mount it frees the previous
        // view's prefetched URLs. (Kept unconditional for same-origin to match
        // the pre-#1934 contract; the strand fix below must not gate it.)
        window.djust._prefetch?.clear();

        // Resolve the LiveView for the target path FIRST, BEFORE touching
        // history. (#1934) The route map (auth-filtered, derived from the
        // Django URLconf) only contains LiveView routes — a plain Django view
        // (TemplateView, etc.) is correctly excluded by
        // `_walk_liveview_routes` (routing.py: `if not issubclass(view_cls,
        // LiveView): continue`). So a route-map miss means "this target is NOT
        // a LiveView" and must be a full-page navigation. If we pushState
        // before this check, a non-LiveView target strands the page: the URL
        // bar moves but no DOM swap happens and the previous LiveView stays
        // mounted.
        //
        // CRITICAL (#1934, symptom-up): use the STRICT resolver
        // (route map only) here, NOT resolveViewPath(). resolveViewPath() has a
        // container fallback (returns the CURRENT [dj-view]'s view when the
        // route map misses) that is documented "only works for live_patch, not
        // cross-view navigation". For a cross-view live_redirect to a
        // non-LiveView, that fallback returns the SOURCE view (truthy), so the
        // SPA branch would re-mount the OLD view under the NEW URL — the exact
        // strand the reporter saw (URL=/onboarding/, but the jira view mounts).
        // The server's #1647 guard (_resolve_view_path_from_url) also returns
        // None for a non-LiveView URL and keeps the stale client-supplied view,
        // so the client must make the full-nav decision here.
        const viewPath = isWSConnected() ? resolveLiveViewPath(newUrl.pathname) : null;

        if (!viewPath) {
            // Non-LiveView target (or no WS connection) → full-page
            // navigation. The URL was NEVER changed (pushState is deferred
            // into the SPA branch below), so the browser load is the single
            // source of truth — no stranded "URL moved, DOM stale" state.
            // newUrl is same-origin here (cross-origin returned above) but is
            // still data.path-derived — validate via the shared guard, exactly
            // as the cross-origin branch does (finding #16).
            const safe = window.djust.safeNavigationTarget(newUrl.toString());
            if (safe) {
                if (globalThis.djustDebug) {
                    console.log('[LiveView] live_redirect non-LiveView target → full-page nav: %s', safe);
                }
                // Stop the page-loading bar we started above; the full nav
                // will trigger the browser's own progress indicator (matches
                // the cross-origin branch's stop semantics).
                if (window.djust.pageLoading && window.djust.pageLoading.enabled) {
                    window.djust.pageLoading.stop?.();
                }
                window.location.href = safe; // codeql[js/xss] -- validated via safeNavigationTarget
            } else {
                if (globalThis.djustDebug) {
                    console.warn('[LiveView] live_redirect fallback rejected unsafe target: %s', newUrl.toString());
                }
                // Stop the page-loading bar — we are not navigating.
                if (window.djust.pageLoading && window.djust.pageLoading.enabled) {
                    window.djust.pageLoading.stop?.();
                }
            }
            return;
        }

        // Target IS a LiveView and the WS is connected → SPA mount over the
        // existing WebSocket. Now (and only now) it is safe to change history,
        // since the DOM swap will follow via the mount frame.
        const method = data.replace ? 'replaceState' : 'pushState';
        // eslint-disable-next-line security/detect-object-injection
        window.history[method]({ djust: true, redirect: true }, '', newUrl.toString());

        // Move the active-nav highlight immediately (the URL is now current),
        // rather than waiting for the WS mount round-trip. (#1756)
        updateAriaCurrent();

        // Scroll to top on navigation (or to anchor if present)
        const hash = newUrl.hash;
        if (hash) {
            try {
                const target = document.querySelector(hash);
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

        if (globalThis.djustDebug) console.log(`[LiveView] live_redirect: ${method} → ${newUrl.toString()}`);

        // Send a mount request for the new view path over the existing WebSocket
        // The server will unmount the old view and mount the new one.
        //
        // Sticky LiveViews (Phase B): BEFORE the outbound
        // live_redirect_mount message, detach any [dj-sticky-view]
        // subtrees into the module-local stash so they survive the
        // mount-frame innerHTML replacement. If stashing happens AFTER
        // the mount frame arrives, the subtree has already been destroyed.
        if (window.djust.stickyPreserve && window.djust.stickyPreserve.stashStickySubtrees) {
            window.djust.stickyPreserve.stashStickySubtrees();
        }

        // State-snapshot capture (v0.6.0) — fire the public event
        // so 46-state-snapshot.js can post the current view's
        // public state to the SW cache BEFORE this URL leaves.
        try {
            window.dispatchEvent(new CustomEvent('djust:before-navigate', {
                detail: { fromUrl: window.location.pathname, toUrl: newUrl.pathname },
            }));
        } catch (_e) { /* CustomEvent may fail in old environments */ }

        const urlParams = Object.fromEntries(newUrl.searchParams);
        const outgoing = {
            type: 'live_redirect_mount',
            view: viewPath,
            params: urlParams,
            url: newUrl.pathname,
        };
        // Attach pending state-snapshot (populated by popstate
        // handler in 46-state-snapshot.js when the user hits back).
        if (window.djust && window.djust._pendingStateSnapshot) {
            outgoing.state_snapshot = window.djust._pendingStateSnapshot;
            window.djust._pendingStateSnapshot = null;
        }
        liveViewWS.sendMessage(outgoing);
    }

    /**
     * Resolve a URL path to a view path using the route map.
     *
     * The route map is populated by live_session() via a <script> tag
     * or by data attributes on the container.
     */
    /**
     * STRICT resolution: look up ``pathname`` in the route map ONLY (exact
     * match, then ``:param`` pattern match). Returns ``null`` on a miss —
     * NO container fallback.
     *
     * The route map (auto-derived from the Django URLconf, #1733, and merged
     * with ``live_session()`` entries) contains ONLY djust LiveView routes —
     * ``_walk_liveview_routes`` (routing.py) excludes non-LiveViews. So a miss
     * authoritatively means "not a LiveView".
     *
     * Use this for the CROSS-VIEW live_redirect decision (#1934): a non-LiveView
     * target must fall through to a full-page navigation, never re-mount the
     * current (source) view. ``resolveViewPath`` (below) adds a container
     * fallback for the live_patch same-view case, which is WRONG for cross-view
     * navigation.
     */
    function resolveLiveViewPath(pathname) {
        // As of #1733 the route map is auto-derived from the Django URLconf and
        // auto-emitted by {% djust_client_config %}; live_session() entries are
        // merged in as well.
        const routeMap = window.djust._routeMap || {};

        // #1361 — `pathname` is user-controllable (URL path). With the route
        // map now populated on EVERY page by default (#1733), the lookup must
        // be prototype-pollution-immune. We walk OWN enumerable entries via
        // `Object.entries` (never `routeMap[pathname]` bracket-indexing), so
        // a polluted `Object.prototype` (e.g. `Object.prototype.toString`)
        // and inherited keys like `constructor` can never resolve to a view.
        // Do NOT reintroduce `routeMap[pathname]` here — it would reopen #1361.
        for (const [routePath, viewPath] of Object.entries(routeMap)) {
            if (routePath === pathname) return viewPath;
        }

        // Try pattern matching (for paths with parameters like /items/42/)
        for (const [pattern, viewPath] of Object.entries(routeMap)) {
            if (pattern.includes(':')) {
                // Convert Django-style pattern to regex
                // e.g., "/items/:id/" → /^\/items\/([^\/]+)\/$/
                // Pattern source is the route map populated server-side by
                // `live_session()` (developer-authored URL config), not
                // user input. The transformation always replaces `:name`
                // with the literal `([^/]+)` group — no nested
                // quantifiers, no user-supplied alternation, no ReDoS
                // surface. Safe to construct.
                const regexStr = pattern.replace(/:([^/]+)/g, '([^/]+)');
                // eslint-disable-next-line security/detect-non-literal-regexp
                const regex = new RegExp('^' + regexStr + '$');
                if (regex.test(pathname)) {
                    return viewPath;
                }
            }
        }

        return null;
    }

    function resolveViewPath(pathname) {
        // Strict route-map resolution first.
        const fromRouteMap = resolveLiveViewPath(pathname);
        if (fromRouteMap) return fromRouteMap;

        // Fallback: check the current container's dj-view
        // (only works for live_patch, not cross-view navigation — cross-view
        // callers must use resolveLiveViewPath, #1934).
        const container = document.querySelector('[dj-view]');
        if (container) {
            return container.getAttribute('dj-view');
        }

        return null;
    }

    /**
     * Listen for browser back/forward (popstate) and send url_change to server.
     *
     * Fix #2: the handler is async so the state-snapshot lookup can be
     * awaited BEFORE sending ``live_redirect_mount`` — the synchronous
     * version captured ``_pendingStateSnapshot`` before the async
     * lookup populated it, causing the first popstate to go out without
     * its cached snapshot.
     *
     * Fix #3: before the live_redirect_mount goes out we also fast-paint
     * cached HTML via ``djust._sw.lookupVdom`` so the user sees
     * something instantly on back-nav; the live WS reply reconciles
     * afterwards via the normal mount handler.
     */
    window.addEventListener('popstate', async function (event) {
        // Keep the active-nav highlight in sync on back/forward (the URL is
        // already current here), regardless of WS state. (#1756)
        updateAriaCurrent();
        if (!liveViewWS || !liveViewWS.viewMounted) return;
        if (!isWSConnected()) return;

        const url = new URL(window.location.href);
        const params = Object.fromEntries(url.searchParams);

        // Check if this is a redirect (different path) vs patch (same path, different params)
        const isRedirect = event.state && event.state.redirect;

        if (isRedirect) {
            // Different view — need to remount. STRICT resolution (#1934): the
            // container fallback would return the CURRENT view on a route-map
            // miss, re-mounting the source view under a non-LiveView URL (the
            // back-nav twin of the live_redirect strand, #1646). On a miss we
            // fall through to window.location.reload() below, which loads the
            // (now-current) non-LiveView URL correctly.
            const viewPath = resolveLiveViewPath(url.pathname);
            if (viewPath) {
                // Sticky LiveViews (Phase B): detach sticky subtrees
                // into the stash BEFORE the outbound
                // live_redirect_mount message.
                if (window.djust.stickyPreserve && window.djust.stickyPreserve.stashStickySubtrees) {
                    window.djust.stickyPreserve.stashStickySubtrees();
                }

                // Fix #3 — VDOM cache fast-paint. If the SW has a
                // recently-cached HTML snapshot for this URL, paint it
                // into the existing ``[dj-view]`` container NOW so the
                // user sees something instantly. The incoming
                // ``mount`` frame's innerHTML replacement reconciles
                // the DOM shortly after.
                try {
                    if (window.djust && window.djust._sw && typeof window.djust._sw.lookupVdom === 'function') {
                        const vdomReply = await window.djust._sw.lookupVdom(url.pathname);
                        if (vdomReply && vdomReply.hit && !vdomReply.stale && typeof vdomReply.html === 'string') {
                            let fastContainer = document.querySelector('[dj-view]:not([dj-sticky-root])');
                            if (!fastContainer) fastContainer = document.querySelector('[dj-root]');
                            if (fastContainer) {
                                // codeql[js/xss] -- html is server-rendered; only reads from SW cache keyed by same-origin url
                                fastContainer.innerHTML = vdomReply.html;
                                window.dispatchEvent(new CustomEvent('djust:vdom-cache-applied', {
                                    detail: { url: url.pathname, version: vdomReply.version },
                                }));
                            }
                        }
                    }
                } catch (_e) { /* fast-paint is best-effort */ }

                // Fix #2 — await the state-snapshot lookup before we
                // send the outbound ``live_redirect_mount`` frame.
                let stateSnapshot = null;
                try {
                    if (window.djust && window.djust._stateSnapshot && typeof window.djust._stateSnapshot.lookupStateForUrl === 'function') {
                        stateSnapshot = await window.djust._stateSnapshot.lookupStateForUrl(url.pathname);
                    } else if (window.djust && window.djust._pendingStateSnapshot) {
                        // Back-compat fallback — if the older async-race
                        // slot happens to be populated, honor it.
                        stateSnapshot = window.djust._pendingStateSnapshot;
                        window.djust._pendingStateSnapshot = null;
                    }
                } catch (_e) { stateSnapshot = null; }

                const outgoing = {
                    type: 'live_redirect_mount',
                    view: viewPath,
                    params: params,
                    url: url.pathname,
                };
                if (stateSnapshot) {
                    outgoing.state_snapshot = stateSnapshot;
                }
                liveViewWS.sendMessage(outgoing);
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
            // newUrl is derived from the dj-patch attribute value — validate via
            // the shared guard so an attacker-influenced dj-patch can't pivot to
            // javascript:/data: DOM-XSS or open-redirect (finding #16).
            const safe = window.djust.safeNavigationTarget(newUrl.toString());
            if (safe) {
                window.location.href = safe; // codeql[js/xss] -- validated via safeNavigationTarget
            } else if (globalThis.djustDebug) {
                console.warn('[LiveView] dj-patch-reload rejected unsafe target: %s', newUrl.toString());
            }
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
        let _djPatchChangeHandlerInstalled = false;
        function installDjPatchChangeHandler() {
            if (_djPatchChangeHandlerInstalled) return;
            _djPatchChangeHandlerInstalled = true;
            document.addEventListener('change', function (e) {
                const el = e.target.closest('[dj-patch]');
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
                    let patchValue = el.getAttribute('dj-patch');
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

        // Keep aria-current="page" in sync with the current URL. A persistent
        // nav usually lives OUTSIDE [dj-root], so dj-navigate's dj-root-only
        // swap never updates a server-rendered active state — this re-derives
        // it client-side. Runs on each call because bindNavigationDirectives is
        // invoked from reinitAfterDOMUpdate (initial load + every SPA mount and
        // patch), so the highlight tracks the current page. (#1756)
        updateAriaCurrent();
    }

    /**
     * Set ``aria-current="page"`` on the ``[dj-navigate]`` link whose path
     * matches the current URL and remove it from the others. Only manages the
     * ``"page"`` value this module sets (never clobbers an app-authored
     * ``aria-current`` of a different value). Cross-origin dj-navigate targets
     * (e.g. a sister-site link) are never "current". Apps style the active link
     * via ``[dj-navigate][aria-current="page"]``.
     */
    function updateAriaCurrent() {
        const here = window.location.pathname;
        document.querySelectorAll('[dj-navigate]').forEach(function (el) {
            let dest;
            try {
                dest = new URL(el.getAttribute('dj-navigate'), window.location.origin);
            } catch (_e) {
                return;
            }
            const isCurrent =
                dest.origin === window.location.origin && dest.pathname === here;
            if (isCurrent) {
                el.setAttribute('aria-current', 'page');
            } else if (el.getAttribute('aria-current') === 'page') {
                el.removeAttribute('aria-current');
            }
        });
    }

    /**
     * auto_navigate (#1734, ADR-021 Stage 2): opt-in Turbo-Drive-style link
     * interception. When enabled (server emits
     * ``<meta name="djust-auto-navigate" content="1">`` from
     * ``LIVEVIEW_CONFIG['auto_navigate']``, default OFF), a SINGLE delegated
     * click listener on ``document`` SPA-navigates plain ``<a href>`` links —
     * but ONLY when the path resolves in the (auth-filtered, #1758) route map.
     * Everything else falls through to normal browser navigation, so non-djust
     * links (admin, logout, external, downloads) and routes the user can't
     * access just load normally.
     *
     * The skip matrix below is correctness-critical (ADR-021): a wrong skip
     * either breaks expected browser behavior (new-tab, downloads) or hijacks a
     * link that should reload. Returning early === "let the browser handle it".
     */
    function _shouldSkipAutoNavigate(e, link) {
        // Another handler already took it (e.g. dj-navigate/dj-patch above).
        if (e.defaultPrevented) return true;
        // Only plain left-clicks; modified clicks mean new tab/window/download.
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return true;
        if (!link) return true;
        // Explicit opt-outs on the link or any ancestor.
        if (link.hasAttribute('download')) return true;
        if (link.closest('[data-no-navigate]')) return true;
        const target = link.getAttribute('target');
        if (target && target !== '_self') return true;
        const rel = (link.getAttribute('rel') || '').toLowerCase();
        if (rel.split(/\s+/).indexOf('external') !== -1) return true;
        return false;
    }

    function _handleAutoNavigateClick(e) {
        const link = e.target && e.target.closest ? e.target.closest('a[href]') : null;
        if (_shouldSkipAutoNavigate(e, link)) return;

        let url;
        try {
            url = new URL(link.getAttribute('href'), window.location.href);
        } catch (_e) {
            return; // unparseable href → let the browser deal with it
        }
        // External origin or non-http(s) scheme (mailto:, tel:, …) → browser.
        if (url.origin !== window.location.origin) return;
        if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
        // Same-document hash-only jump → let the browser scroll, don't hijack.
        if (
            url.pathname === window.location.pathname &&
            url.search === window.location.search &&
            url.hash
        ) {
            return;
        }
        // Only intercept paths the (auth-filtered) route map knows are LiveView
        // routes. Unknown paths (admin, plain Django views, routes the user
        // can't access) fall through to a normal navigation the server gates.
        if (!resolveViewPath(url.pathname)) return;
        if (!liveViewWS || !liveViewWS.ws) return; // no socket → normal nav

        e.preventDefault();
        if (url.pathname === window.location.pathname) {
            // Same view, query-only change → state-preserving url_change (no
            // remount), mirroring the dj-patch wire shape but navigating to the
            // link's EXACT query (replace, not dj-patch's param-merge — a full
            // <a href> is the complete intended target). Falls back to a normal
            // load if the view isn't mounted yet.
            if (!liveViewWS.viewMounted) {
                // url is already same-origin http(s) (validated at the top of
                // this handler), but route through the shared guard for
                // consistency across all location.href sinks (finding #16).
                const safe = window.djust.safeNavigationTarget(url.toString());
                if (safe) {
                    window.location.href = safe; // codeql[js/xss] -- validated via safeNavigationTarget
                } else if (globalThis.djustDebug) {
                    console.warn('[LiveView] auto-navigate rejected unsafe target: %s', url.toString());
                }
                return;
            }
            window.history.pushState({ djust: true }, '', url.pathname + url.search);
            liveViewWS.sendMessage({
                type: 'url_change',
                params: Object.fromEntries(url.searchParams),
                uri: url.pathname + url.search,
            });
        } else {
            // Cross-view → live_redirect over the existing WebSocket.
            handleLiveRedirect({ path: url.pathname + url.search, replace: false });
        }
    }

    let _autoNavigateInstalled = false;

    function installAutoNavigate() {
        if (_autoNavigateInstalled) return;
        if (typeof document === 'undefined') return;
        const meta = document.querySelector('meta[name="djust-auto-navigate"]');
        if (!meta || meta.getAttribute('content') !== '1') return;
        // One delegated listener for the whole document — survives SPA mounts
        // (no per-element binding to re-run on reinit). Default (bubble) phase
        // so app/dj-navigate handlers that call preventDefault run first and
        // set e.defaultPrevented, which the skip matrix honors.
        document.addEventListener('click', _handleAutoNavigateClick);
        _autoNavigateInstalled = true;
    }

    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', installAutoNavigate);
        } else {
            installAutoNavigate();
        }
    }

    // Expose to djust namespace
    window.djust.navigation = {
        handleNavigation: handleNavigation,
        bindDirectives: bindNavigationDirectives,
        resolveViewPath: resolveViewPath,
        updateAriaCurrent: updateAriaCurrent,
        installAutoNavigate: installAutoNavigate,
        // Exposed for tests + advanced callers; the delegated listener is the
        // supported entry point.
        _handleAutoNavigateClick: _handleAutoNavigateClick,
        _shouldSkipAutoNavigate: _shouldSkipAutoNavigate,
    };

    // Initialize route map
    if (!window.djust._routeMap) {
        window.djust._routeMap = {};
    }
})();
