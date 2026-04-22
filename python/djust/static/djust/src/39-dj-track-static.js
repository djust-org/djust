
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
//   On the FIRST djust:ws-reconnected event the snapshot is empty, so
//   the first connect seeds it. On every subsequent reconnect, each
//   [dj-track-static] element's src/href is compared against the
//   initial snapshot. If any differ, dispatch a dj:stale-assets
//   CustomEvent (detail = { changed: [...urls] }). If any of the
//   changed elements carried dj-track-static="reload", call
//   window.location.reload() instead.

let _djTrackStaticSnapshot = null;

function _urlOf(el) {
    return el.getAttribute('src') || el.getAttribute('href') || '';
}

function _snapshotAssets() {
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
            if ((el.getAttribute('dj-track-static') || '').trim() === 'reload') {
                shouldReload = true;
            }
        }
    });
    return { changed: changed, shouldReload: shouldReload };
}

function _onWsReconnected() {
    const result = _checkStale();
    if (!result) return;  // First connect — snapshot was just seeded.
    if (result.changed.length === 0) return;
    if (result.shouldReload) {
        window.location.reload();
        return;
    }
    document.dispatchEvent(new CustomEvent('dj:stale-assets', {
        detail: { changed: result.changed },
    }));
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
    _resetSnapshot: function () { _djTrackStaticSnapshot = null; },
};
