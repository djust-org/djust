// ============================================================================
// Online / offline body classes — dj-offline-hide / -show / -disable (#3041)
// and the {% djust_offline_indicator %} text and status class (#3051)
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
// It also keeps every `.djust-offline-indicator` in step (#3051): the
// indicator's `.djust-indicator-text` takes `data-online-text` /
// `data-offline-text`, and the element carries the classes named in
// `data-online-class` or `data-offline-class`. Visibility stays in CSS (the
// `dj-offline-show` / `dj-offline-hide` rules); this only swaps text and class.
//
// Browser network state only: a WebSocket reconnect is not "offline" (the
// HTTP fallback still works), so the socket state does not drive these.

(function () {
    // Last known state. `navigator.onLine === false` is the only reliable
    // signal; anything else (true, or no navigator) counts as online.
    let _online = !(typeof navigator !== 'undefined' && navigator.onLine === false);

    function _classList(value) {
        return (value || '').split(/\s+/).filter(Boolean);
    }

    function _syncIndicator(el) {
        const onClasses = _classList(el.getAttribute('data-online-class'));
        const offClasses = _classList(el.getAttribute('data-offline-class'));
        // Remove the other state's classes first, then add this state's, so a
        // class named in both lists stays on.
        const remove = _online ? offClasses : onClasses;
        const add = _online ? onClasses : offClasses;
        remove.forEach(function (c) { el.classList.remove(c); });
        add.forEach(function (c) { el.classList.add(c); });

        const attr = _online ? 'data-online-text' : 'data-offline-text';
        const text = el.querySelector('.djust-indicator-text');
        if (text && el.hasAttribute(attr)) {
            const value = el.getAttribute(attr);
            if (text.textContent !== value) text.textContent = value;
        }
    }

    function _syncIndicators(scope) {
        const root = scope || document;
        if (!root || typeof root.querySelectorAll !== 'function') return;
        if (root.classList && root.classList.contains('djust-offline-indicator')) {
            _syncIndicator(root);
        }
        root.querySelectorAll('.djust-offline-indicator').forEach(_syncIndicator);
    }

    function _apply() {
        const body = document.body;
        if (!body) return;
        body.classList.toggle('djust-online', _online);
        body.classList.toggle('djust-offline', !_online);
        _syncIndicators(document);
    }

    function _set(online) {
        _online = online;
        _apply();
    }

    // reinitAfterDOMUpdate (09-event-binding.js) calls this after every DOM
    // update, so an indicator that a patch or a navigation inserts shows the
    // current state rather than the server-rendered default.
    window.djust._syncOfflineIndicators = _syncIndicators;

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
