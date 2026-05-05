
// 45-child-view.js — Embedded child LiveView dispatch + Sticky LiveView
// preservation across live_redirect.
//
// Phase A (v0.5.7) shipped the child_update wire-frame receiver (WIRE-
// ONLY: validate + dispatch lifecycle event, DOM untouched). Phase B
// (v0.6.0) extends this module with:
//
//   * A scoped VDOM applier — reuses the main ``applyPatches`` via the
//     new ``applyPatches(patches, rootEl)`` variant so child / sticky
//     patches are scoped to their subtree and don't collide with the
//     parent's VDOM coordinates. Closes the Phase A "DOM untouched"
//     TODO: ``handleChildUpdate`` now really updates the child.
//   * Per-view VDOM version tracking — ``clientVdomVersions:
//     Map<view_id, number>`` keyed by ``__root`` for the top-level view
//     and ``view_id`` for each child / sticky.
//   * Sticky stash + reconcile + reattach flow — detach sticky subtrees
//     BEFORE live_redirect sends, reconcile against the server's
//     authoritative ``sticky_hold`` list on mount, then re-attach each
//     stashed subtree at the matching ``[dj-sticky-slot="<id>"]`` via
//     ``replaceWith()`` (DOM identity preserved — form values, scroll,
//     focus, and third-party widget references all survive).
//
// Each ``{% live_render "...Path" sticky=True %}`` on the server emits:
//     <div dj-view dj-sticky-view="<id>" dj-sticky-root
//          data-djust-embedded="<id>">…</div>
// Apps listen to ``djust:sticky-preserved`` / ``djust:sticky-unmounted``
// CustomEvents to react to lifecycle transitions.

(function () {


    const djust = globalThis.djust = globalThis.djust || {};

    // Per-view VDOM version tracking (Phase B). Keyed by view_id, with
    // the sentinel "__root" reserved for the top-level view.
    //
    // The top-level ``clientVdomVersion`` module-local (04-cache.js)
    // remains authoritative for the root view for now — we mirror its
    // value into this map so any consumer that wants per-view versions
    // can use a single lookup.
    const clientVdomVersions = new Map();
    djust.clientVdomVersions = clientVdomVersions;

    // ---------------------------------------------------------------------
    // Sticky stash (Phase B).
    // ---------------------------------------------------------------------
    //
    // Map<sticky_id, HTMLElement> holding subtrees detached from the DOM
    // before a live_redirect is sent. Key is the ``dj-sticky-view``
    // attribute value; equal to the view_id the server uses for
    // ``sticky_update`` patches.
    const stickyStash = new Map();

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
     * Look up the DOM root of a sticky view by its sticky_id.
     * Returns null if no matching [dj-sticky-view] element is in the document.
     */
    function _getStickyRoot(stickyId) {
        if (!stickyId) return null;
        const selector = '[dj-sticky-view="' + _escapeSelector(stickyId) + '"]';
        return document.querySelector(selector);
    }

    /**
     * Dispatch a CustomEvent on a target element, swallowing errors in
     * ancient runtimes that lack CustomEvent.
     */
    function _dispatch(target, name, detail) {
        try {
            target.dispatchEvent(new CustomEvent(name, {
                bubbles: true,
                detail: detail || {},
            }));
        } catch (_err) {
            // CustomEvent constructor missing — ignore.
        }
    }

    /**
     * Apply a list of VDOM patches scoped to ``rootEl`` by calling the
     * top-level ``applyPatches`` function with the scoping root. When
     * the top-level function is missing (tests or malformed bundles)
     * this is a no-op so the receiver still dispatches its lifecycle
     * event without tripping an exception.
     */
    async function _applyScopedPatches(patches, rootEl) {
        if (typeof applyPatches !== "function") {
            if (globalThis.djustDebug) {
                console.warn("[djust] applyPatches is not in scope; skipping");
            }
            return false;
        }
        return applyPatches(patches, rootEl);
    }

    /**
     * Handle an inbound ``child_update`` frame.
     *
     * Phase B: applies the frame's patches scoped to the child subtree
     * (``[dj-view][data-djust-embedded="<view_id>"]``). Updates the
     * per-view VDOM version in ``clientVdomVersions``. Dispatches
     * ``djust:child-updated`` before the patch pass so observers can
     * see pre-apply state; this matches the Phase A event shape with
     * ``phase: 'B'``.
     */
    async function handleChildUpdate(message) {
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

        // Dispatch pre-apply so observers see the pre-apply state.
        _dispatch(root, "djust:child-updated", {
            view_id: viewId,
            version: message.version,
            patches: message.patches || [],
            phase: "B",
        });

        // Apply scoped patches. Child patches carry their own dj-id
        // namespace; without rootEl the doc-wide lookup in
        // getNodeByPath would cross subtree boundaries.
        if (Array.isArray(message.patches) && message.patches.length > 0) {
            await _applyScopedPatches(message.patches, root);
        }
        clientVdomVersions.set(viewId, message.version);
    }

    /**
     * Handle an inbound ``sticky_update`` frame. Same shape as
     * ``child_update`` but targets a sticky subtree via the
     * ``[dj-sticky-view]`` selector.
     */
    async function handleStickyUpdate(message) {
        if (!message) return;
        const viewId = message.view_id;
        if (!viewId) return;
        const root = _getStickyRoot(viewId);
        if (!root) {
            if (globalThis.djustDebug) {
                console.warn("[djust] sticky_update for unknown view_id=" + viewId);
            }
            return;
        }
        _dispatch(root, "djust:sticky-updated", {
            view_id: viewId,
            version: message.version,
            patches: message.patches || [],
        });
        if (Array.isArray(message.patches) && message.patches.length > 0) {
            await _applyScopedPatches(message.patches, root);
        }
        clientVdomVersions.set(viewId, message.version);
    }

    /**
     * Walk all ``[dj-sticky-view]`` elements and detach each into the
     * stash, keyed by its ``dj-sticky-view`` attribute value.
     *
     * Idempotent — if an id is already stashed (rapid re-entrant
     * navigation), the second call is a no-op for that id. Skips
     * ``document.contains`` checks so re-entrant calls during a
     * DOM replacement cycle stay safe.
     */
    function stashStickySubtrees() {
        const elements = document.querySelectorAll("[dj-sticky-view]");
        elements.forEach(function (el) {
            const stickyId = el.getAttribute("dj-sticky-view");
            if (!stickyId) return;
            if (stickyStash.has(stickyId)) return;  // idempotent
            // Detach from DOM — parentNode.removeChild preserves the
            // subtree including form values, focus, and third-party
            // widget references (e.g. <video> players).
            if (el.parentNode) {
                el.parentNode.removeChild(el);
            }
            stickyStash.set(stickyId, el);
        });
    }

    /**
     * Reconcile the stash against the server's authoritative list of
     * surviving sticky ids. Entries in the stash but NOT in ``views``
     * are dropped + a ``djust:sticky-unmounted`` event is dispatched
     * with reason ``server-unmount``.
     */
    function reconcileStickyHold(views) {
        const authoritative = new Set(Array.isArray(views) ? views : []);
        stickyStash.forEach(function (subtree, stickyId) {
            if (!authoritative.has(stickyId)) {
                stickyStash.delete(stickyId);
                _dispatch(subtree, "djust:sticky-unmounted", {
                    sticky_id: stickyId,
                    reason: "server-unmount",
                });
            }
        });
    }

    /**
     * After a mount-frame HTML replacement has been applied, walk
     * ``[dj-sticky-slot]`` elements in the new DOM. For each match,
     * look up the corresponding stash entry; if present, replace the
     * slot with the stashed subtree via ``replaceWith()`` (preserving
     * DOM identity). If absent, dispatch ``djust:sticky-unmounted``
     * with reason ``no-slot`` and drop the entry.
     */
    function reattachStickyAfterMount() {
        if (stickyStash.size === 0) return;
        const slots = document.querySelectorAll("[dj-sticky-slot]");
        const reattached = new Set();
        slots.forEach(function (slotEl) {
            const stickyId = slotEl.getAttribute("dj-sticky-slot");
            if (!stickyId) return;
            const subtree = stickyStash.get(stickyId);
            if (!subtree) return;
            // Use replaceWith so DOM identity of the sticky subtree is
            // preserved — form values, focus, scroll, and any
            // third-party widget references hanging off nodes inside
            // the subtree all survive.
            if (slotEl.replaceWith) {
                slotEl.replaceWith(subtree);
            } else if (slotEl.parentNode) {
                slotEl.parentNode.replaceChild(subtree, slotEl);
            }
            stickyStash.delete(stickyId);
            reattached.add(stickyId);
            _dispatch(subtree, "djust:sticky-preserved", {
                sticky_id: stickyId,
            });
        });

        // Any leftover stash entry that didn't find a matching slot is
        // unmounted with reason no-slot.
        stickyStash.forEach(function (subtree, stickyId) {
            stickyStash.delete(stickyId);
            _dispatch(subtree, "djust:sticky-unmounted", {
                sticky_id: stickyId,
                reason: "no-slot",
            });
        });
    }

    /**
     * Emit ``djust:child-mounted`` CustomEvent for every ``[dj-view]``
     * that carries a ``data-djust-embedded`` in the initial DOM on
     * DOMContentLoaded. Apps can listen to react to child view lifecycle
     * (e.g. third-party embeds, analytics).
     */
    function emitChildMountedEvents() {
        const children = document.querySelectorAll(
            "[dj-view][data-djust-embedded]"
        );
        children.forEach(function (el) {
            const viewId = el.getAttribute("data-djust-embedded");
            _dispatch(el, "djust:child-mounted", { view_id: viewId });
        });
    }

    djust.childView = {
        handleChildUpdate: handleChildUpdate,
        _getChildRoot: _getChildRoot,
    };

    /**
     * Drop every entry from the sticky stash. Called from 03-websocket.js
     * when the WebSocket closes abnormally (server crash, network
     * partition) — the server will re-mount any sticky views from
     * scratch on reconnect, so keeping detached subtrees from a
     * dead session would only cause ``no-slot`` unmount events to
     * fire on the next navigation.
     */
    function clearStash() {
        if (stickyStash.size > 0 && globalThis.djustDebug) {
            console.log("[sticky] clearing stash of " + stickyStash.size + " subtree(s) on abnormal close");
        }
        stickyStash.clear();
    }

    djust.stickyPreserve = {
        handleStickyUpdate: handleStickyUpdate,
        stashStickySubtrees: stashStickySubtrees,
        reconcileStickyHold: reconcileStickyHold,
        reattachStickyAfterMount: reattachStickyAfterMount,
        clearStash: clearStash,
        _getStickyRoot: _getStickyRoot,
        _stash: stickyStash,
    };

    // Also expose the top-level applyPatches under a stable name so
    // other modules (and tests) can invoke the scoped variant without
    // reaching into 12-vdom-patch.js's internals.
    if (typeof applyPatches === "function" && !djust._applyPatches) {
        djust._applyPatches = applyPatches;
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", emitChildMountedEvents);
    } else {
        emitChildMountedEvents();
    }
})();
