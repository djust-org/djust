// Optional ADR-028 player: no module or media requests on non-audio pages.
(function () {
    let loading = false;
    let styled = false;
    const ownerDocument = document;
    function syncAudio() {
        if (!window.document || !ownerDocument.body || !ownerDocument.defaultView) return;
        const marker = document.querySelector('[dj-audio][data-audio-src]');
        if (marker && !styled && marker.hasAttribute('data-audio-css')) {
            styled = true;
            const stylesheet = document.createElement('link');
            stylesheet.rel = 'stylesheet';
            stylesheet.href = marker.getAttribute('data-audio-css');
            document.head.appendChild(stylesheet);
        }
        if (window.djustAudio) { window.djustAudio.sync(); return; }
        if (!marker || loading) return;
        loading = true;
        const script = document.createElement('script');
        script.src = marker.getAttribute('data-audio-src');
        script.onload = function () {
            if (window.djustAudio) window.djustAudio.sync();
        };
        script.onerror = function () {
            document.querySelectorAll('[data-audio-status]').forEach(function (el) {
                el.textContent = 'Sound unavailable';
            });
        };
        document.head.appendChild(script);
    }
    function startAudioLoader() {
        syncAudio();
        let queued = false;
        const observer = new MutationObserver(function (changes) {
            if (!window.document) { observer.disconnect(); return; }
            if (queued || !changes.some(function (change) {
                return change.type === 'childList' || change.attributeName === 'dj-audio';
            })) return;
            queued = true;
            queueMicrotask(function () { queued = false; syncAudio(); });
        });
        observer.observe(document.body, { childList: true, subtree: true,
            attributes: true, attributeFilter: ['dj-audio'] });
        window.addEventListener('pagehide', function () { observer.disconnect(); }, { once: true });
    }
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) startAudioLoader();
    });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startAudioLoader, { once: true });
    } else { startAudioLoader(); }
})();
