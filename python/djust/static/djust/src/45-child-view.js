
// 45-child-view.js — Embedded child LiveView dispatch (Phase A of Sticky LiveViews).
//
// Handles `child_update` WS frames that target a specific child view by
// `view_id`. Phase A shape: `{type:'child_update', view_id, patches, version}`.
//
// Each `{% live_render %}` in a parent template emits:
//     <div dj-view data-djust-embedded="child_N"> ... </div>
// and stamps the same id inside every event-attribute-bearing element
// (as `data-djust-embedded`) in the subtree. The client's existing DOM
// walker (`getEmbeddedViewId` in 01-dom-helpers-turbo.js) picks it up
// via `dataset.djustEmbedded` and surfaces it on outbound events as
// the `view_id` wire-protocol field.
//
// PHASE A SCOPE: This module is wire-only for `child_update` frames.
// The frame is received, validated against a DOM-present child root, and
// a `djust:child-updated` CustomEvent is dispatched for app observers.
// VDOM patch application (reconciling the child subtree) is deliberately
// DEFERRED TO PHASE B. The existing `applyPatches` does not accept a
// `root:` scoping option and would apply child patches against the
// document root, colliding with the parent's VDOM coordinates. Phase B
// ships the scoped patch applier; Phase A lays the receiver plumbing.

(function () {
    "use strict";

    const djust = globalThis.djust = globalThis.djust || {};

    // Fallback for environments where CSS.escape is missing (jsdom < 22
    // ships a partial CSSOM). Escapes anything other than [A-Za-z0-9_-].
    function _escapeSelector(value) {
        if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
            return CSS.escape(value);
        }
        return String(value).replace(/([^A-Za-z0-9_-])/g, "\\$1");
    }

    /**
     * Look up the DOM root of an embedded child view by its view_id.
     * Returns null if no matching [dj-view] element is in the document.
     */
    function _getChildRoot(viewId) {
        if (!viewId) return null;
        const selector =
            '[dj-view][data-djust-embedded="' + _escapeSelector(viewId) + '"]';
        return document.querySelector(selector);
    }

    /**
     * Handle an inbound `child_update` frame.
     *
     * Phase A behavior: validates the frame's view_id resolves to a
     * `[dj-view]` subtree, dispatches `djust:child-updated` for app
     * observers, and returns. Does NOT apply the frame's patches —
     * scoped patch application lands in Phase B. When debug mode is
     * enabled, a warning is logged to make the deferred scope explicit.
     */
    function handleChildUpdate(message) {
        if (!message) return;
        const viewId = message.view_id;
        if (!viewId) {
            if (globalThis.djustDebug) {
                console.warn("[djust] child_update frame missing view_id", message);
            }
            return;
        }
        const root = _getChildRoot(viewId);
        if (!root) {
            if (globalThis.djustDebug) {
                console.warn("[djust] child_update for unknown view_id=" + viewId);
            }
            return;
        }

        if (globalThis.djustDebug) {
            // Honest announcement: Phase A does not apply the patches.
            // Phase B wires the scoped applier; until then we dispatch
            // the lifecycle event so app code can observe the update
            // (e.g. to flip loading state), but the DOM stays as it was.
            console.warn(
                "[djust] child_update received for view_id=" + viewId +
                    " — patches NOT applied in Phase A (deferred to Phase B)"
            );
        }

        // Emit a lifecycle CustomEvent so app code / hooks can react.
        try {
            root.dispatchEvent(new CustomEvent("djust:child-updated", {
                bubbles: true,
                detail: {
                    view_id: viewId,
                    version: message.version,
                    patches: message.patches || [],
                    phase: "A",
                },
            }));
        } catch (_err) {
            // CustomEvent constructor missing in ancient runtimes — ignore.
        }
    }

    /**
     * Emit `djust:child-mounted` CustomEvent for every `[dj-view]` that
     * carries a `data-djust-embedded` in the initial DOM on
     * DOMContentLoaded. Apps can listen to react to child view lifecycle
     * (e.g. third-party embeds, analytics).
     */
    function emitChildMountedEvents() {
        const children = document.querySelectorAll(
            "[dj-view][data-djust-embedded]"
        );
        children.forEach(function (el) {
            const viewId = el.getAttribute("data-djust-embedded");
            try {
                el.dispatchEvent(new CustomEvent("djust:child-mounted", {
                    bubbles: true,
                    detail: { view_id: viewId },
                }));
            } catch (_err) {
                // CustomEvent constructor missing — skip silently.
            }
        });
    }

    djust.childView = {
        handleChildUpdate: handleChildUpdate,
        _getChildRoot: _getChildRoot,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", emitChildMountedEvents);
    } else {
        emitChildMountedEvents();
    }
})();
