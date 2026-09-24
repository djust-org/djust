
// dj-track-static — stale asset detection on WS reconnect (v0.6.0)
//
// Phoenix phx-track-static parity. Without this, clients on long-lived
// WebSocket connections silently run stale JavaScript after a server
// deploy — zero-downtime on the server, broken behavior on connected
// clients.
//
// Usage:
//   <script dj-track-static src="{% static 'js/app.abc123.js' %}"></script>
//   <link dj-track-static rel="stylesheet" href="...">
//   <script dj-track-static="reload" src="..."></script>
//
// Behavior:
//   The page-load snapshot records each [dj-track-static] element's
//   src/href. On every djust:ws-reconnected event:
//     1. each tracked element still in the page is compared with its
//        snapshot URL (catches a patch that rewrote one in place);
//     2. otherwise the page is re-fetched (GET, same origin, no redirects
//        followed) and its [dj-track-static] URLs are compared with the
//        snapshot. A new deploy changes them there, never in the running
//        page's <head> — without this step a deploy was never detected
//        (#2966).
//   If anything changed, dispatch a dj:stale-assets CustomEvent
//   (detail = { changed: [...urls] }). If a changed asset carries
//   dj-track-static="reload", call window.location.reload() instead.

// #880: Using `Map` (not `WeakMap`) deliberately: the reconnect-diff step
// iterates ALL tracked elements to compare snapshot URLs with current URLs.
// WeakMap does not support iteration, so we accept the weak-reference
// tradeoff. If an element is removed from the DOM, the `isConnected` check
// in `_checkStale` skips it — we don't leak observers, just map entries
// that are cleared on the next `_snapshotAssets()` seed.
let _djTrackStaticSnapshot = null;

function _urlOf(el) {
    return el.getAttribute('src') || el.getAttribute('href') || '';
}

function _snapshotAssets() {
    // See #880 comment above: Map chosen for iteration support.
    const snap = new Map();
    document.querySelectorAll('[dj-track-static]').forEach(function (el) {
        snap.set(el, _urlOf(el));
    });
    return snap;
}

function _checkStale() {
    // Normally the snapshot is seeded at DOMContentLoaded and
    // _checkStale is never called with a null snapshot. This branch
    // only triggers after _resetSnapshot() — it's a test hook for
    // exercising the seed path without reloading the document.
    if (_djTrackStaticSnapshot === null) {
        _djTrackStaticSnapshot = _snapshotAssets();
        return null;
    }
    const changed = [];
    let shouldReload = false;
    _djTrackStaticSnapshot.forEach(function (oldUrl, el) {
        // If the tracked element is no longer in the document (VDOM
        // morphed it out entirely), we can't tell whether its
        // replacement carries a new URL or was simply removed. Treat
        // it as unchanged — avoids false-positive reloads on benign
        // morphs. A future enhancement could re-query live
        // [dj-track-static] elements and diff by URL identity.
        if (!el.isConnected) return;
        const currentUrl = _urlOf(el);
        if (currentUrl !== oldUrl) {
            changed.push(currentUrl);
            if (_isReloadAsset(el)) {
                shouldReload = true;
            }
        }
    });
    return { changed: changed, shouldReload: shouldReload };
}

function _isReloadAsset(el) {
    return (el.getAttribute('dj-track-static') || '').trim() === 'reload';
}

/**
 * Compare the tracked assets of a freshly fetched copy of the page with the
 * page-load snapshot (#2966). ``html`` is the page source. Returns
 * ``{changed, shouldReload}`` — ``changed`` lists URLs the server now serves
 * that the running page did not load.
 */
function _compareWithServerPage(html) {
    const empty = { changed: [], shouldReload: false };
    if (!_djTrackStaticSnapshot || _djTrackStaticSnapshot.size === 0) return empty;
    let doc;
    try {
        doc = new DOMParser().parseFromString(html, 'text/html');
    } catch (_e) {
        return empty;
    }
    const served = Array.from(doc.querySelectorAll('[dj-track-static]'));
    // A page with no tracked assets (an error page, a different layout) says
    // nothing about a deploy.
    if (served.length === 0) return empty;
    const loaded = new Set(_djTrackStaticSnapshot.values());
    const servedUrls = new Set(served.map(_urlOf));
    const changed = [];
    let shouldReload = false;
    served.forEach(function (el) {
        const url = _urlOf(el);
        if (url && !loaded.has(url) && changed.indexOf(url) === -1) {
            changed.push(url);
            if (_isReloadAsset(el)) shouldReload = true;
        }
    });
    if (changed.length > 0) {
        // A "reload" asset the server no longer serves also asks for a reload.
        _djTrackStaticSnapshot.forEach(function (oldUrl, el) {
            if (_isReloadAsset(el) && !servedUrls.has(oldUrl)) shouldReload = true;
        });
    }
    return { changed: changed, shouldReload: shouldReload };
}

let _serverCheckInFlight = false;

function _fetchAndCompare() {
    if (_serverCheckInFlight) return Promise.resolve(null);
    if (!_djTrackStaticSnapshot || _djTrackStaticSnapshot.size === 0) return Promise.resolve(null);
    if (typeof window === 'undefined' || typeof window.fetch !== 'function') return Promise.resolve(null);
    _serverCheckInFlight = true;
    return window.fetch(window.location.href, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        // A redirect (for example to a login page) would compare another
        // page's assets: skip the check instead.
        redirect: 'manual',
        headers: { 'Accept': 'text/html' },
    }).then(function (response) {
        if (!response || !response.ok || response.redirected || response.type === 'opaqueredirect') {
            return null;
        }
        return response.text().then(_compareWithServerPage);
    }).catch(function () {
        return null;
    }).finally(function () {
        _serverCheckInFlight = false;
    });
}

function _reportStale(result) {
    if (!result || result.changed.length === 0) return;
    if (result.shouldReload) {
        window.location.reload();
        return;
    }
    document.dispatchEvent(new CustomEvent('dj:stale-assets', {
        detail: { changed: result.changed },
    }));
}

function _onWsReconnected() {
    const result = _checkStale();
    if (!result) return null;  // First connect — snapshot was just seeded.
    if (result.changed.length > 0) {
        _reportStale(result);
        return null;
    }
    // The running page's <head> never changes on a deploy: ask the server
    // for the page it would serve now (#2966).
    return _fetchAndCompare().then(_reportStale);
}

function _installDjTrackStatic() {
    // Seed snapshot on page load (the \"first connect\" for SSR / full page
    // load case). The subsequent djust:ws-reconnected events compare
    // against this baseline.
    _djTrackStaticSnapshot = _snapshotAssets();
    document.addEventListener('djust:ws-reconnected', _onWsReconnected);
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjTrackStatic);
    } else {
        _installDjTrackStatic();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djTrackStatic = {
    _snapshotAssets,
    _checkStale,
    _onWsReconnected,
    _compareWithServerPage,
    _resetSnapshot: function () { _djTrackStaticSnapshot = null; },
};
