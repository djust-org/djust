// ============================================================================
// Online / offline body classes — dj-offline-hide / -show / -disable (#3041)
// ============================================================================
// `{% djust_pwa_head %}` and `{% djust_offline_styles %}` emit CSS keyed on
// `body.djust-online` / `body.djust-offline`:
//
//     body.djust-offline [dj-offline-hide],
//     body:not(.djust-online) [dj-offline-hide] { display: none !important }
//
// Nothing set those classes after the unbundled pwa.js was removed (#2659),
// so `body:not(.djust-online)` always matched: dj-offline-hide elements were
// always hidden, dj-offline-disable always disabled, dj-offline-show never
// shown. This module sets them from `navigator.onLine` at startup and keeps
// them current from the window `online` / `offline` events.
//
// Browser network state only: a WebSocket reconnect is not "offline" (the
// HTTP fallback still works), so the socket state does not drive these.

(function () {
    // Last known state. `navigator.onLine === false` is the only reliable
    // signal; anything else (true, or no navigator) counts as online.
    let _online = !(typeof navigator !== 'undefined' && navigator.onLine === false);

    function _apply() {
        const body = document.body;
        if (!body) return;
        body.classList.toggle('djust-online', _online);
        body.classList.toggle('djust-offline', !_online);
    }

    function _set(online) {
        _online = online;
        _apply();
    }

    window.addEventListener('online', function () { _set(true); });
    window.addEventListener('offline', function () { _set(false); });
    // A layout switch replaces <body> (40-dj-layout.js); re-stamp the new one.
    document.addEventListener('djust:layout-changed', _apply);

    if (document.body) {
        _apply();
    } else {
        document.addEventListener('DOMContentLoaded', _apply, { once: true });
    }
})();
