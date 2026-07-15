/**
 * Bug-capture replay viewer client script (B7 iter B, #1562).
 *
 * Served alongside `djust/templates/djust/bug_capture/replay.html` at
 * `/__djust__/replay/<blob>`. CSP-strict: external module, no inline
 * scripts/handlers — a delegated `click` listener on `document` matches
 * the framework's marker-class + delegated-listener convention for new
 * client-side code (CLAUDE.md "CSP-strict defaults for new client-side
 * framework code", #1175).
 *
 * This script ONLY copies the already-rendered `djbug1.` blob to the
 * clipboard. It never sends a network request and never touches the
 * WebSocket layer — the replay page has no live view to talk to.
 */
(function () {
    function copyText(text) {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            return navigator.clipboard.writeText(text);
        }
        return Promise.reject(new Error('clipboard API unavailable'));
    }

    document.addEventListener('click', function (ev) {
        const target = ev.target;
        if (!target || typeof target.closest !== 'function') return;
        const btn = target.closest('.dj-bugcapture-copy');
        if (!btn) return;

        const url = btn.getAttribute('data-bugcapture-url') || '';
        if (!url) return;

        const original = btn.textContent;
        copyText(url).then(function () {
            btn.textContent = 'Copied!';
            setTimeout(function () { btn.textContent = original; }, 1500);
        }).catch(function () {
            btn.textContent = 'Copy failed — select and copy manually';
            setTimeout(function () { btn.textContent = original; }, 2000);
        });
    });
})();
