// Optional ADR-028 player: no module or media requests on non-audio pages.
(function () {
    let loading = false;
    let styled = false;
    const ownerDocument = document;
    // Capture the executing framework script, never a selector-controlled URL.
    const clientSource = document.currentScript && document.currentScript.src;
    const audioSource = clientSource ? new URL('audio.js', clientSource).href : null;
    const audioStyle = clientSource ? new URL('audio.css', clientSource).href : null;
    function syncAudio() {
        if (!window.document || !ownerDocument.body || !ownerDocument.defaultView) return;
        const marker = document.querySelector('[dj-audio]');
        if (marker && !styled && audioStyle) {
            styled = true;
            const stylesheet = document.createElement('link');
            stylesheet.rel = 'stylesheet';
            stylesheet.href = audioStyle;
            document.head.appendChild(stylesheet);
        }
        if (window.djustAudio) { window.djustAudio.sync(); return; }
        if (!marker || loading || !audioSource) return;
        loading = true;
        const script = document.createElement('script');
        script.src = audioSource;
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
