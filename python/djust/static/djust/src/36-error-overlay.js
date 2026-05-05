/**
 * Dev-mode error overlay — Next.js/Vite-style full-screen error display.
 *
 * Listens for the `djust:error` CustomEvent (dispatched by 03-websocket.js
 * and 03b-sse.js when the server sends `type: "error"` frames) and renders
 * an in-browser panel with the error message, triggering event, Python
 * traceback, hint, and validation details when present.
 *
 * Only active when `window.DEBUG_MODE === true` (set by the `djust_tags`
 * template tag when Django DEBUG=True). Production builds receive no
 * overlay at all — the server strips `traceback` / `debug_detail` / `hint`
 * in non-DEBUG mode, so the overlay would have nothing interesting to show.
 *
 * Dismissal: Escape key, the close button, or clicking the backdrop.
 * Idempotent — a second error while the overlay is open replaces the
 * content rather than stacking panels.
 */

(function initErrorOverlay() {
    const OVERLAY_ID = 'djust-error-overlay';
    const STYLE_ID = 'djust-error-overlay-style';

    function _escape(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function _injectStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = `
#${OVERLAY_ID} {
    position: fixed; inset: 0; z-index: 2147483646;
    background: rgba(0,0,0,0.72);
    display: flex; align-items: flex-start; justify-content: center;
    padding: 40px 20px; overflow-y: auto;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
#${OVERLAY_ID} .djust-eo-panel {
    background: #1a1a1a; color: #e8e8e8;
    border: 1px solid #5a2626; border-radius: 6px;
    width: 100%; max-width: 960px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.6);
    overflow: hidden;
}
#${OVERLAY_ID} .djust-eo-header {
    background: #3a1515; color: #ffb4b4;
    padding: 14px 20px;
    display: flex; justify-content: space-between; align-items: center;
    font-weight: 600; font-size: 14px;
    border-bottom: 1px solid #5a2626;
}
#${OVERLAY_ID} .djust-eo-close {
    background: transparent; border: none; color: #ffb4b4;
    font-size: 22px; line-height: 1; cursor: pointer;
    padding: 0 4px;
}
#${OVERLAY_ID} .djust-eo-close:hover { color: #fff; }
#${OVERLAY_ID} .djust-eo-body { padding: 18px 22px; }
#${OVERLAY_ID} .djust-eo-error {
    font-size: 15px; color: #ff8a8a;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    margin: 0 0 14px 0; word-break: break-word; white-space: pre-wrap;
}
#${OVERLAY_ID} .djust-eo-meta {
    color: #8a8a8a; font-size: 12px; margin-bottom: 12px;
}
#${OVERLAY_ID} .djust-eo-meta code {
    background: #2a2a2a; color: #c8c8c8; padding: 2px 6px; border-radius: 3px;
}
#${OVERLAY_ID} .djust-eo-section-title {
    color: #bbb; font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.05em;
    margin: 16px 0 6px 0;
}
#${OVERLAY_ID} pre.djust-eo-trace {
    background: #0e0e0e; border: 1px solid #2a2a2a; border-radius: 4px;
    padding: 12px 14px; margin: 0;
    color: #d0d0d0; font-size: 12px; line-height: 1.5;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    overflow-x: auto; white-space: pre; max-height: 360px;
}
#${OVERLAY_ID} .djust-eo-hint {
    background: #1f2a1f; border-left: 3px solid #6fbf73;
    color: #d2ebd4; padding: 10px 14px; border-radius: 3px;
    font-size: 13px; line-height: 1.5;
}
#${OVERLAY_ID} .djust-eo-footer {
    color: #666; font-size: 11px; padding: 10px 22px;
    border-top: 1px solid #2a2a2a; background: #151515;
}
`;
        document.head.appendChild(style);
    }

    function _render(detail) {
        _injectStyles();
        const errorText = detail.error || 'Server error';
        const eventName = detail.event || null;
        const traceback = detail.traceback || null;
        const hint = detail.hint || null;
        const debugDetail = detail.debug_detail || null;
        const validation = detail.validation_details || null;

        const existing = document.getElementById(OVERLAY_ID);
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = OVERLAY_ID;
        overlay.setAttribute('role', 'alertdialog');
        overlay.setAttribute('aria-label', 'djust dev error');

        const panel = document.createElement('div');
        panel.className = 'djust-eo-panel';
        panel.addEventListener('click', (e) => e.stopPropagation());

        let html = '';
        html += '<div class="djust-eo-header">';
        html += '<span>djust · server error</span>';
        html += '<button type="button" class="djust-eo-close" aria-label="Dismiss">&times;</button>';
        html += '</div>';
        html += '<div class="djust-eo-body">';
        html += `<p class="djust-eo-error">${_escape(errorText)}</p>`;
        if (eventName) {
            html += `<div class="djust-eo-meta">Triggered by event <code>${_escape(eventName)}</code></div>`;
        }
        if (debugDetail && debugDetail !== errorText) {
            html += '<div class="djust-eo-section-title">Detail</div>';
            html += `<pre class="djust-eo-trace">${_escape(debugDetail)}</pre>`;
        }
        if (traceback) {
            html += '<div class="djust-eo-section-title">Traceback</div>';
            html += `<pre class="djust-eo-trace">${_escape(traceback)}</pre>`;
        }
        if (hint) {
            html += '<div class="djust-eo-section-title">Hint</div>';
            html += `<div class="djust-eo-hint">${_escape(hint)}</div>`;
        }
        if (validation) {
            html += '<div class="djust-eo-section-title">Validation</div>';
            html += `<pre class="djust-eo-trace">${_escape(JSON.stringify(validation, null, 2))}</pre>`;
        }
        html += '</div>';
        html += '<div class="djust-eo-footer">Press <kbd>Esc</kbd> to dismiss · shown only when Django DEBUG=True</div>';
        panel.innerHTML = html;
        overlay.appendChild(panel);

        const dismiss = () => overlay.remove();
        overlay.addEventListener('click', dismiss);
        panel.querySelector('.djust-eo-close').addEventListener('click', dismiss);

        document.body.appendChild(overlay);
        return overlay;
    }

    function _onKeyDown(e) {
        if (e.key !== 'Escape') return;
        const overlay = document.getElementById(OVERLAY_ID);
        if (overlay) overlay.remove();
    }

    function _onError(e) {
        if (!window.DEBUG_MODE) return;
        const detail = (e && e.detail) || {};
        _render(detail);
    }

    if (typeof window !== 'undefined') {
        window.addEventListener('djust:error', _onError);
        window.addEventListener('keydown', _onKeyDown);
        // Expose for tests and for manual triggering from devtools.
        window.djustErrorOverlay = {
            show: (detail) => _render(detail || {}),
            dismiss: () => {
                const o = document.getElementById(OVERLAY_ID);
                if (o) o.remove();
            },
        };
    }
})();
