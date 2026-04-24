/**
 * Hot View Replacement (HVR) — v0.6.1
 *
 * Client-side dev indicator for state-preserving Python reloads. The server
 * broadcasts a ``{type:'hvr-applied'}`` frame after successfully swapping
 * ``__class__`` on the live view instance; the websocket module (03) routes
 * it here to:
 *   1. Dispatch a ``djust:hvr-applied`` CustomEvent on ``document`` so
 *      application code / tests can observe HVR bursts.
 *   2. In DEBUG mode (``globalThis.djustDebug === true``) paint a small
 *      corner toast confirming the reload. Toast auto-dismisses after 3s.
 *
 * Module is development-only in intent — nothing here runs unless the
 * server actually sends an hvr-applied frame, which only happens when
 * ``DEBUG=True`` + ``LIVEVIEW_CONFIG["hvr_enabled"]``.
 */
(function () {
    "use strict";
    const djust = (globalThis.djust = globalThis.djust || {});

    /**
     * Render a short-lived toast confirming HVR applied successfully.
     * No-op outside debug mode (``globalThis.djustDebug`` falsy).
     *
     * @param {object} data - The ``hvr-applied`` message body. Expected
     *     fields: ``view`` (string), ``version`` (number), ``file`` (string).
     */
    function showIndicator(data) {
        if (!globalThis.djustDebug) return;
        try {
            if (!document || !document.body) return;
            const el = document.createElement("div");
            el.className = "djust-hvr-toast";
            el.setAttribute("role", "status");
            el.setAttribute("aria-live", "polite");
            const view = (data && data.view) || "(unknown)";
            const version = (data && data.version) || 0;
            el.textContent = "HVR applied: " + view + " (v" + version + ")";
            el.style.cssText =
                "position:fixed;bottom:12px;right:12px;" +
                "background:#22c55e;color:#fff;padding:8px 12px;" +
                "border-radius:4px;font:14px system-ui,sans-serif;" +
                "box-shadow:0 2px 8px rgba(0,0,0,.15);z-index:999999;" +
                "pointer-events:none;";
            document.body.appendChild(el);
            setTimeout(function () {
                try {
                    el.remove();
                } catch (err) {
                    if (globalThis.djustDebug) {
                        console.warn("[djust.hvr] toast cleanup failed", err);
                    }
                }
            }, 3000);
        } catch (e) {
            if (globalThis.djustDebug) {
                console.warn("[djust.hvr] showIndicator failed", e);
            }
        }
    }

    djust.hvr = { showIndicator: showIndicator };
})();
